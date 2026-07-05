"""Contact reconciliation for Jack.

When a person turns up in email (an unread sender) or on the calendar (an attendee)
who is NOT already a lead in Orsa, Jack should notice — enrich them via Apollo and
flag them for review, rather than silently ignoring a potential new contact.

This module is the join layer: Gmail/Calendar → (diff against Orsa) → Apollo → flags.
It's read-only and side-effect-free; it returns the flags and lets the caller surface
them. Every step degrades gracefully — a missing Orsa DB, a Gmail hiccup, or no Apollo
key each just narrow what's returned; nothing raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Personal/no-reply senders we never treat as CRM leads.
_SKIP_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "yahoo.com",
    "icloud.com",
}
_SKIP_LOCALPARTS = {"no-reply", "noreply", "donotreply", "do-not-reply", "mailer-daemon"}


@dataclass(frozen=True)
class FlaggedContact:
    """A contact seen in email/calendar that isn't in Orsa yet."""

    email: str
    name: str
    seen_in: str  # "email" | "calendar"
    apollo: dict | None = field(default=None)


def _looks_business(email: str) -> bool:
    """Heuristic: skip personal webmail and no-reply addresses."""
    if not email or "@" not in email:
        return False
    localpart, _, domain = email.partition("@")
    if domain.lower() in _SKIP_DOMAINS:
        return False
    return localpart.lower() not in _SKIP_LOCALPARTS


def reconcile_from_gmail(max_results: int = 15) -> list[FlaggedContact] | None:
    """Diff unread-email senders against Orsa; enrich + flag the unknown ones.

    Returns a list of FlaggedContact (possibly empty), or None only when Gmail itself
    is unreachable (so the caller can tell "nothing new" from "couldn't check").
    """
    from integrations.gmail import GmailClient  # lazy

    msgs = GmailClient().list_unread(max_results=max_results)
    if msgs is None:
        return None  # Gmail not connected / failed — distinct from "no new contacts"

    candidates = [
        {"email": m.get("from_email", ""), "name": m.get("from", ""), "seen_in": "email"}
        for m in msgs
        if m.get("from_email")
    ]
    return reconcile_contacts(candidates)


def reconcile_contacts(candidates: list[dict]) -> list[FlaggedContact]:
    """Given candidate contacts, return those not in Orsa (Apollo-enriched when possible).

    Each candidate: {email, name, seen_in}. Never raises; returns [] on total failure.
    """
    try:
        from integrations.orsa import OrsaClient  # lazy

        known = OrsaClient().known_emails()
    except Exception:  # noqa: BLE001
        known = None
    # If Orsa is unavailable, treat everyone as unknown (better to over-flag than to
    # silently drop a real new contact) — but only for genuinely business-looking mail.
    known_set = known if known is not None else set()

    try:
        from integrations.apollo import ApolloClient  # lazy

        apollo = ApolloClient()
    except Exception:  # noqa: BLE001
        apollo = None

    seen_emails: set[str] = set()
    flagged: list[FlaggedContact] = []
    for c in candidates:
        email = (c.get("email") or "").strip().lower()
        if not email or email in seen_emails:
            continue
        seen_emails.add(email)
        if not _looks_business(email):
            continue
        if email in known_set:
            continue  # already a lead — nothing to flag

        enrichment = None
        if apollo is not None and apollo.is_configured():
            try:
                enrichment = apollo.enrich_person(email=email, name=c.get("name") or None)
            except Exception:  # noqa: BLE001
                enrichment = None

        flagged.append(
            FlaggedContact(
                email=email,
                name=c.get("name") or email,
                seen_in=c.get("seen_in") or "email",
                apollo=enrichment,
            )
        )
    return flagged


def flags_summary_text(flags: list[FlaggedContact]) -> str:
    """Render flagged contacts as a short review list, or '' if none."""
    if not flags:
        return ""
    lines = []
    for f in flags:
        who = f.name or f.email
        if f.apollo:
            title = f.apollo.get("title")
            company = f.apollo.get("company")
            extra = " — " + ", ".join(x for x in (title, company) if x) if (title or company) else ""
            lines.append(f"• {who} ({f.email}){extra} · not in Orsa")
        else:
            lines.append(f"• {who} ({f.email}) · not in Orsa")
    return "\n".join(lines)
