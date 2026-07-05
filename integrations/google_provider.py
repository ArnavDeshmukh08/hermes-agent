"""Provider factory: direct clients on the Mac, remote proxies on the VPS.

The handler and briefing don't care where the data physically lives — they call these
factories. On the Mac (where the OAuth token and Orsa DB are), the factories return the
direct clients. On the VPS gateway, setting JACK_GOOGLE_REMOTE=1 makes them return the
Tailscale HTTP proxies (integrations.mac_remote) that call the Mac service.

Default is direct, so local dev, the Mac service itself, and the existing test suite
(which patches integrations.calendar/gmail/orsa) all keep working untouched.
"""

from __future__ import annotations

import os

_TRUE = {"1", "true", "yes", "on"}


def _remote() -> bool:
    return str(os.environ.get("JACK_GOOGLE_REMOTE", "")).strip().lower() in _TRUE


def calendar_client():
    """Return a calendar read client (remote proxy on the VPS, else direct)."""
    if _remote():
        from integrations.mac_remote import RemoteCalendar

        return RemoteCalendar()
    from integrations.calendar import CalendarClient

    return CalendarClient()


def gmail_client():
    """Return a Gmail read+draft client (remote proxy on the VPS, else direct)."""
    if _remote():
        from integrations.mac_remote import RemoteGmail

        return RemoteGmail()
    from integrations.gmail import GmailClient

    return GmailClient()


def orsa_client():
    """Return an Orsa read-only client (remote proxy on the VPS, else direct)."""
    if _remote():
        from integrations.mac_remote import RemoteOrsa

        return RemoteOrsa()
    from integrations.orsa import OrsaClient

    return OrsaClient()


def unknown_contact_flags_text() -> str:
    """Summary of unread senders not in Orsa (Apollo-enriched when possible), or ''.

    Remote: one call to the Mac /contacts/flags endpoint. Direct: run the reconciler
    locally against Gmail + Orsa + Apollo. Best-effort — returns '' on any failure.
    """
    try:
        if _remote():
            from integrations.mac_remote import unknown_contact_flags_text as _remote_flags

            return _remote_flags()
        from integrations.contact_reconciler import flags_summary_text, reconcile_from_gmail

        flags = reconcile_from_gmail()
        if not flags:
            return ""
        return flags_summary_text(flags)
    except Exception:  # noqa: BLE001 — reconciliation is always best-effort
        return ""
