#!/usr/bin/env python3
"""Mac-side Google + Orsa service.

Runs on the Mac (where the OAuth token and the Orsa SQLite DB live) and exposes HTTP
endpoints reachable from the VPS over Tailscale. Mirrors mac_services/garmin_service.py:
the VPS gateway proxies to this service (integrations.mac_remote) instead of shipping
credentials off the Mac.

Read + draft only — there is deliberately NO send endpoint. The /gmail/draft endpoint
creates a Gmail draft and stops; nothing here can send mail.

Security: binds 0.0.0.0 (Tailscale-private, like the Garmin service). Because Gmail is
more sensitive than Garmin, set JACK_MAC_SERVICE_TOKEN to require a matching X-Jack-Token
header on every request (recommended). With no token set, any Tailscale peer can call it.

Endpoints:
  GET  /health
  GET  /calendar/events
  GET  /gmail/unread?max=N
  GET  /gmail/message?id=...
  POST /gmail/draft            {to, subject, body, thread_id?, in_reply_to?}
  GET  /orsa/status
  GET  /orsa/count
  GET  /orsa/new_count?days=N
  GET  /orsa/summary?days=N
  GET  /contacts/flags
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse as _urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Make the repo's integrations importable when launched from anywhere.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PORT = int(os.environ.get("JACK_GOOGLE_SERVICE_PORT", "8770"))


def _log(level: str, msg: str, **extra) -> None:
    # Structured logs, mirroring garmin_service. NEVER include tokens/bodies.
    print(json.dumps({"level": level, "msg": msg, **extra}), flush=True)


def _int_param(params: dict, key: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(params.get(key, default))))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Endpoint implementations (each returns a plain dict; never raises out)
# ---------------------------------------------------------------------------

def _calendar_events() -> dict:
    from integrations.calendar import CalendarClient

    c = CalendarClient()
    events = c.list_events()
    summary = c.events_summary_text()
    return {"events": events if events is not None else [], "summary": summary or ""}


def _gmail_unread(max_results: int) -> dict:
    from integrations.gmail import GmailClient

    msgs = GmailClient().list_unread(max_results=max_results)
    return {"messages": msgs if msgs is not None else []}


def _gmail_message(message_id: str) -> dict:
    from integrations.gmail import GmailClient

    return {"message": GmailClient().get_message(message_id)}


def _gmail_draft(body: dict) -> dict:
    from integrations.gmail import GmailClient

    draft = GmailClient().create_draft(
        to=body.get("to", ""),
        subject=body.get("subject", ""),
        body=body.get("body", ""),
        thread_id=body.get("thread_id"),
        in_reply_to=body.get("in_reply_to"),
    )
    return {"draft": draft}


def _orsa_status() -> dict:
    from integrations.orsa import OrsaClient

    return {"connected": OrsaClient().is_connected()}


def _orsa_count() -> dict:
    from integrations.orsa import OrsaClient

    return {"total": OrsaClient().lead_count()}


def _orsa_new_count(days: int) -> dict:
    from integrations.orsa import OrsaClient

    return {"count": OrsaClient().new_leads_count(since_days=days)}


def _orsa_summary(days: int) -> dict:
    from integrations.orsa import OrsaClient

    return {"summary": OrsaClient().new_leads_summary_text(since_days=days)}


def _contacts_flags() -> dict:
    from integrations.contact_reconciler import flags_summary_text, reconcile_from_gmail

    flags = reconcile_from_gmail()
    if flags is None:
        return {"summary": "", "count": 0}
    return {"summary": flags_summary_text(flags), "count": len(flags)}


def _health() -> dict:
    from integrations import google_auth
    from integrations.orsa import OrsaClient

    return {
        "status": "ok",
        "google_connected": google_auth.is_connected(),
        "orsa_connected": OrsaClient().is_connected(),
    }


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class JackGoogleHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # route access logs through structured logging
        _log("info", fmt % args)

    def _json(self, data: dict, status: int = 200) -> None:
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self) -> bool:
        token = os.environ.get("JACK_MAC_SERVICE_TOKEN", "").strip()
        if not token:
            return True  # no token configured → open on the Tailscale network
        return self.headers.get("X-Jack-Token", "") == token

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode() or "{}")
        except Exception:  # noqa: BLE001
            return {}

    def do_GET(self):  # noqa: N802
        if not self._authorized():
            self._json({"error": "unauthorized"}, 401)
            return
        parsed = _urlparse.urlparse(self.path)
        params = dict(_urlparse.parse_qsl(parsed.query))
        path = parsed.path
        try:
            if path == "/health":
                self._json(_health())
            elif path == "/calendar/events":
                self._json(_calendar_events())
            elif path == "/gmail/unread":
                self._json(_gmail_unread(_int_param(params, "max", 8, 1, 50)))
            elif path == "/gmail/message":
                mid = params.get("id", "")
                self._json(_gmail_message(mid) if mid else {"message": None})
            elif path == "/orsa/status":
                self._json(_orsa_status())
            elif path == "/orsa/count":
                self._json(_orsa_count())
            elif path == "/orsa/new_count":
                self._json(_orsa_new_count(_int_param(params, "days", 7, 1, 365)))
            elif path == "/orsa/summary":
                self._json(_orsa_summary(_int_param(params, "days", 7, 1, 365)))
            elif path == "/contacts/flags":
                self._json(_contacts_flags())
            else:
                self._json({"error": "not_found"}, 404)
        except Exception as exc:  # noqa: BLE001 — an endpoint bug must not kill the service
            _log("error", "GET handler error", path=path, error=str(exc))
            self._json({"error": "internal"}, 500)

    def do_POST(self):  # noqa: N802
        if not self._authorized():
            self._json({"error": "unauthorized"}, 401)
            return
        parsed = _urlparse.urlparse(self.path)
        try:
            if parsed.path == "/gmail/draft":
                self._json(_gmail_draft(self._read_json_body()))
            else:
                self._json({"error": "not_found"}, 404)
        except Exception as exc:  # noqa: BLE001
            _log("error", "POST handler error", path=parsed.path, error=str(exc))
            self._json({"error": "internal"}, 500)


def main() -> None:
    _log("info", "Jack Google/Orsa service starting", port=PORT,
         token_required=bool(os.environ.get("JACK_MAC_SERVICE_TOKEN", "").strip()))
    server = ThreadingHTTPServer(("0.0.0.0", PORT), JackGoogleHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
