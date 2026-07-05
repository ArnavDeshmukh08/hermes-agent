"""Gmail integration for Jack — read unread mail + create drafts (NEVER send).

Draft-only is a deliberate product decision, not a gap. There is deliberately NO
send path in this module: nothing here calls users().messages().send or
users().drafts().send. Jack creates a draft in *your* Drafts folder and stops — you
review and hit send yourself.

Auth comes from integrations.google_auth (OAuth installed-app + refresh token), so
Gmail survives a Jack restart without re-authentication.

Design rules (mirror integrations/__init__.py):
    - google libs imported lazily; module imports fine without them.
    - Every public method returns None/[]/'' on ANY failure and never raises.
    - Message bodies, addresses beyond what's needed, and tokens are never logged.
"""

from __future__ import annotations

import base64
import logging
import os
from email.message import EmailMessage
from email.utils import parseaddr

logger = logging.getLogger(__name__)

# Default query: unread, in the primary inbox, excluding promotions/social noise.
# Overridable so Arnav can tune what counts as "business" mail.
_DEFAULT_UNREAD_QUERY = "is:unread in:inbox -category:promotions -category:social"


def _header(headers: list[dict], name: str) -> str:
    """Return the value of header *name* (case-insensitive) from a Gmail headers list."""
    lname = name.lower()
    for h in headers:
        if h.get("name", "").lower() == lname:
            return h.get("value", "") or ""
    return ""


class GmailClient:
    """Read unread mail and create draft replies via the Gmail v1 API.

    All methods degrade to None/[]/'' when Gmail isn't connected or an API call fails.
    """

    def __init__(self, user_id: str = "me") -> None:
        self.user_id = user_id
        self._svc = None  # lazily built

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _service(self):
        """Return a built Gmail service (OAuth-backed) or None. Cached after first build."""
        if self._svc is not None:
            return self._svc
        from integrations.google_auth import build_service  # lazy

        self._svc = build_service("gmail", "v1")
        return self._svc

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_unread(self, query: str | None = None, max_results: int = 10) -> list[dict] | None:
        """Return slim dicts for unread messages matching *query*, or None on failure.

        Each dict: {id, thread_id, from, from_email, subject, snippet, date}.
        Empty inbox → [] (not None). Never raises.
        """
        svc = self._service()
        if svc is None:
            return None

        q = query if query is not None else os.environ.get("JACK_GMAIL_QUERY", _DEFAULT_UNREAD_QUERY)
        try:
            listing = (
                svc.users()
                .messages()
                .list(userId=self.user_id, q=q, maxResults=max_results)
                .execute()
            )
        except Exception:  # noqa: BLE001
            logger.warning("gmail: message list failed (query redacted)")
            return None

        message_ids = [m["id"] for m in listing.get("messages", []) if m.get("id")]
        out: list[dict] = []
        for mid in message_ids:
            slim = self._get_metadata(svc, mid)
            if slim is not None:
                out.append(slim)
        return out

    def _get_metadata(self, svc, message_id: str) -> dict | None:
        """Fetch header-level metadata for one message. None on failure."""
        try:
            msg = (
                svc.users()
                .messages()
                .get(
                    userId=self.user_id,
                    id=message_id,
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date", "Message-ID"],
                )
                .execute()
            )
        except Exception:  # noqa: BLE001
            return None

        headers = msg.get("payload", {}).get("headers", [])
        raw_from = _header(headers, "From")
        display, email_addr = parseaddr(raw_from)
        return {
            "id": msg.get("id"),
            "thread_id": msg.get("threadId"),
            "from": display or email_addr or raw_from,
            "from_email": email_addr,
            "subject": _header(headers, "Subject") or "(no subject)",
            "snippet": msg.get("snippet", ""),
            "date": _header(headers, "Date"),
            "rfc_message_id": _header(headers, "Message-ID"),
        }

    def get_message(self, message_id: str) -> dict | None:
        """Return a fuller view of one message for drafting context, or None.

        Adds a decoded plain-text `body` (best-effort, truncated) to the metadata dict.
        Never raises.
        """
        svc = self._service()
        if svc is None:
            return None
        try:
            msg = (
                svc.users()
                .messages()
                .get(userId=self.user_id, id=message_id, format="full")
                .execute()
            )
        except Exception:  # noqa: BLE001
            logger.warning("gmail: get_message failed for id %s", message_id)
            return None

        payload = msg.get("payload", {})
        headers = payload.get("headers", [])
        raw_from = _header(headers, "From")
        display, email_addr = parseaddr(raw_from)
        return {
            "id": msg.get("id"),
            "thread_id": msg.get("threadId"),
            "from": display or email_addr or raw_from,
            "from_email": email_addr,
            "subject": _header(headers, "Subject") or "(no subject)",
            "date": _header(headers, "Date"),
            "rfc_message_id": _header(headers, "Message-ID"),
            "body": _extract_plain_body(payload),
        }

    def unread_business_summary_text(self, max_results: int = 5) -> str:
        """One-line-per-message summary of unread mail, or '' on failure/empty."""
        try:
            msgs = self.list_unread(max_results=max_results)
            if not msgs:
                return ""
            return "\n".join(f"• {m['from']}: {m['subject']}" for m in msgs)
        except Exception:  # noqa: BLE001
            return ""

    # ------------------------------------------------------------------
    # Draft (NO send — deliberate)
    # ------------------------------------------------------------------

    def create_draft(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
    ) -> dict | None:
        """Create a Gmail draft and return {id, message_id, thread_id}, or None.

        This NEVER sends. It creates a draft in the user's Drafts folder for review.
        When *thread_id* / *in_reply_to* are given, the draft threads under the original
        so it shows up as a reply. Never raises.
        """
        svc = self._service()
        if svc is None:
            return None

        try:
            mime = EmailMessage()
            mime["To"] = to
            mime["Subject"] = subject
            if in_reply_to:
                # Thread the reply correctly in the recipient's client.
                mime["In-Reply-To"] = in_reply_to
                mime["References"] = in_reply_to
            mime.set_content(body)

            raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
            draft_body: dict = {"message": {"raw": raw}}
            if thread_id:
                draft_body["message"]["threadId"] = thread_id

            result = (
                svc.users()
                .drafts()
                .create(userId=self.user_id, body=draft_body)
                .execute()
            )
        except Exception:  # noqa: BLE001
            logger.warning("gmail: draft creation failed")
            return None

        return {
            "id": result.get("id"),
            "message_id": result.get("message", {}).get("id"),
            "thread_id": result.get("message", {}).get("threadId"),
        }


def _extract_plain_body(payload: dict, limit: int = 4000) -> str:
    """Best-effort decode of a message's plain-text body, truncated to *limit* chars."""
    def _decode(data: str) -> str:
        try:
            return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return ""

    # Simple (non-multipart) body.
    body = payload.get("body", {})
    if body.get("data") and payload.get("mimeType", "").startswith("text/plain"):
        return _decode(body["data"])[:limit]

    # Walk multipart parts, preferring text/plain.
    stack = list(payload.get("parts", []) or [])
    plain, html = "", ""
    while stack:
        part = stack.pop(0)
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if part.get("parts"):
            stack.extend(part["parts"])
        if data and mime == "text/plain" and not plain:
            plain = _decode(data)
        elif data and mime == "text/html" and not html:
            html = _decode(data)
    return (plain or html)[:limit]
