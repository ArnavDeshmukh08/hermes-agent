"""VPS-side HTTP proxies for the Mac-resident Google + Orsa data.

Split-brain: the OAuth token and the Orsa SQLite DB live on the Mac, but the gateway
(intent router/handler) runs on the VPS. Rather than ship credentials to the VPS, the
VPS calls a small Mac service over Tailscale — exactly the pattern integrations/garmin.py
already uses for Garmin.

These proxy classes mirror the method signatures of the direct clients
(CalendarClient / GmailClient / OrsaClient) so the handler can use either transparently
via integrations.google_provider. Every method degrades to None/[]/'' on any failure and
never raises.

Config:
    JACK_GOOGLE_SERVICE_URL   base URL of the Mac service (default the Mac's Tailscale IP)
    JACK_MAC_SERVICE_TOKEN    optional shared secret; sent as X-Jack-Token when set
    JACK_GOOGLE_TIMEOUT       per-request timeout seconds (default 6)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:  # pragma: no cover - requests is present on the VPS
    _requests = None  # type: ignore[assignment]
    _HAS_REQUESTS = False

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://100.120.65.115:8770"  # Mac Tailscale IP, dedicated port


def _base_url() -> str:
    return os.environ.get("JACK_GOOGLE_SERVICE_URL", _DEFAULT_URL).rstrip("/")


def _timeout() -> float:
    return float(os.environ.get("JACK_GOOGLE_TIMEOUT", "6"))


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("JACK_MAC_SERVICE_TOKEN", "").strip()
    if token:
        headers["X-Jack-Token"] = token
    return headers


def _get(path: str, params: dict | None = None) -> Any | None:
    """GET the Mac service; return parsed JSON or None on any failure."""
    url = f"{_base_url()}{path}"
    try:
        if _HAS_REQUESTS:
            resp = _requests.get(url, params=params, headers=_headers(), timeout=_timeout())
            resp.raise_for_status()
            return resp.json()
        import urllib.parse
        import urllib.request

        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=_headers())
        with urllib.request.urlopen(req, timeout=_timeout()) as r:  # noqa: S310
            return json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        logger.warning("mac_remote: GET %s failed", path)
        return None


def _post(path: str, body: dict) -> Any | None:
    """POST JSON to the Mac service; return parsed JSON or None on any failure."""
    url = f"{_base_url()}{path}"
    try:
        if _HAS_REQUESTS:
            resp = _requests.post(url, json=body, headers=_headers(), timeout=_timeout())
            resp.raise_for_status()
            return resp.json()
        import urllib.request

        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers=_headers(), method="POST")
        with urllib.request.urlopen(req, timeout=_timeout()) as r:  # noqa: S310
            return json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        logger.warning("mac_remote: POST %s failed", path)
        return None


class RemoteCalendar:
    """Proxy for integrations.calendar.CalendarClient (read methods only)."""

    def list_events(self, day=None, max_results: int = 10) -> list[dict] | None:  # noqa: ARG002
        data = _get("/calendar/events")
        if not isinstance(data, dict):
            return None
        events = data.get("events")
        return events if isinstance(events, list) else None

    def events_summary_text(self, day=None) -> str:  # noqa: ARG002
        data = _get("/calendar/events")
        if isinstance(data, dict) and isinstance(data.get("summary"), str):
            return data["summary"]
        return ""

    def add_event(self, *args, **kwargs) -> None:  # noqa: ARG002
        """Calendar is read-only over the bridge — writes aren't proxied.

        Returns None so the handler shows its normal "not connected for writes"
        message instead of raising. Calendar *write* stays a service-account concern.
        """
        return None


class RemoteGmail:
    """Proxy for integrations.gmail.GmailClient (read + draft-only, no send)."""

    def list_unread(self, query: str | None = None, max_results: int = 10) -> list[dict] | None:
        data = _get("/gmail/unread", {"max": max_results})
        if not isinstance(data, dict):
            return None
        msgs = data.get("messages")
        return msgs if isinstance(msgs, list) else None

    def get_message(self, message_id: str) -> dict | None:
        data = _get("/gmail/message", {"id": message_id})
        if isinstance(data, dict) and isinstance(data.get("message"), dict):
            return data["message"]
        return None

    def create_draft(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
    ) -> dict | None:
        data = _post(
            "/gmail/draft",
            {
                "to": to,
                "subject": subject,
                "body": body,
                "thread_id": thread_id,
                "in_reply_to": in_reply_to,
            },
        )
        if isinstance(data, dict) and isinstance(data.get("draft"), dict):
            return data["draft"]
        return None

    def unread_business_summary_text(self, max_results: int = 5) -> str:
        msgs = self.list_unread(max_results=max_results)
        if not msgs:
            return ""
        return "\n".join(f"• {m.get('from', '')}: {m.get('subject', '')}" for m in msgs)


class RemoteOrsa:
    """Proxy for integrations.orsa.OrsaClient (read-only)."""

    def is_connected(self) -> bool:
        data = _get("/orsa/status")
        return bool(isinstance(data, dict) and data.get("connected"))

    def lead_count(self) -> int | None:
        data = _get("/orsa/count")
        if isinstance(data, dict) and isinstance(data.get("total"), int):
            return data["total"]
        return None

    def new_leads_count(self, since_days: int = 7) -> int | None:
        data = _get("/orsa/new_count", {"days": since_days})
        if isinstance(data, dict) and isinstance(data.get("count"), int):
            return data["count"]
        return None

    def new_leads_summary_text(self, since_days: int = 7) -> str:
        data = _get("/orsa/summary", {"days": since_days})
        if isinstance(data, dict) and isinstance(data.get("summary"), str):
            return data["summary"]
        return ""


def unknown_contact_flags_text() -> str:
    """Fetch the Mac-side reconciliation summary (senders not in Orsa), or ''."""
    data = _get("/contacts/flags")
    if isinstance(data, dict) and isinstance(data.get("summary"), str):
        return data["summary"]
    return ""
