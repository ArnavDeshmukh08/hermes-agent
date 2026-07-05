#!/usr/bin/env python3
"""Jack OS — personal agent dashboard backend.

A dependency-free (stdlib only) HTTP server that powers Jack's sci-fi command centre.
It runs on the Mac (where the rich integrations live) and aggregates a live picture of
the whole agent OS:

  • Integrations (local, direct):  Orsa CRM, Gmail, Google Calendar, Garmin
  • Agent core (the VPS box):      service health + reminders + goals + proactive log,
                                   pulled in ONE cached SSH read (30s TTL)
  • Bridge/services status:        the Mac's own launchd services + the Tailscale bridge
  • Intent preview:               /api/classify runs the real router (read-only) so the
                                   command bar shows what Jack *would* do — it never acts.

Every endpoint degrades gracefully: a data source that's down returns
{"available": false, ...} instead of failing the page. Nothing here writes anything.
Read-only + draft-safe, consistent with the rest of Jack.

Run:  python3 dashboard/server.py     (then open http://localhost:8899)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_STATIC = Path(__file__).resolve().parent / "static"
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks" / "jack_router")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PORT = int(os.environ.get("JACK_DASHBOARD_PORT", "8899"))
_IST = timezone(timedelta(hours=5, minutes=30))

# VPS (agent core) — one cached SSH read for services + reminders + goals + nudges.
_VPS_HOST = os.environ.get("JACK_VPS_SSH", "hermes@167.233.108.213")
_VPS_KEY = os.path.expanduser(os.environ.get("JACK_VPS_SSH_KEY", "~/.ssh/hermes_vps"))
_VPS_TTL = 30.0
_vps_cache: dict = {"at": 0.0, "data": None}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_ist_iso() -> str:
    return datetime.now(_IST).isoformat()


def _http_json(url: str, timeout: float = 4.0):
    """GET a local service's JSON (bridge/garmin), or None."""
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
            return json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        return None


# The remote probe: one python program, run once over SSH, returns the whole agent-core
# snapshot as JSON. Read-only. List-form subprocess (no shell=True).
_REMOTE_PROBE = r"""
import json, os, subprocess
base = os.path.expanduser('~/.hermes')
def load(name):
    try:
        with open(os.path.join(base, name)) as fh:
            return json.load(fh)
    except Exception:
        return None
def active(svc):
    try:
        out = subprocess.run(['systemctl','--user','is-active',svc],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except Exception:
        return 'unknown'
services = {s: active(s+'.service') for s in
            ('hermes-gateway','jack-reminders','jack-briefing','jack-proactive')}
print(json.dumps({
    'services': services,
    'reminders': load('reminders.json'),
    'goals': (load('goals.json') or {}).get('goals'),
    'proactive': load('proactive_log.json'),
}))
"""


def _vps_snapshot() -> dict:
    """Cached agent-core snapshot from the VPS (services/reminders/goals/nudges)."""
    now = time.time()
    if _vps_cache["data"] is not None and (now - _vps_cache["at"]) < _VPS_TTL:
        return _vps_cache["data"]
    data = {"available": False, "services": {}, "reminders": None, "goals": None, "proactive": None}
    try:
        # Pipe the probe via stdin ("python3 -") rather than -c: ssh re-tokenizes the
        # remote command through a shell, which would mangle a multi-line -c program.
        proc = subprocess.run(
            ["ssh", "-i", _VPS_KEY, "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
             _VPS_HOST, "python3", "-"],
            input=_REMOTE_PROBE, capture_output=True, text=True, timeout=20,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            parsed = json.loads(proc.stdout)
            parsed["available"] = True
            data = parsed
    except Exception:  # noqa: BLE001
        pass
    _vps_cache.update(at=now, data=data)
    return data


# ---------------------------------------------------------------------------
# API endpoint builders (each returns a plain dict, never raises)
# ---------------------------------------------------------------------------

def api_services() -> dict:
    snap = _vps_snapshot()
    bridge = _http_json("http://localhost:8770/health")
    garmin = _http_json("http://localhost:8765/health")
    agents = [
        {"name": "Gateway", "id": "hermes-gateway", "role": "Discord brain",
         "state": snap["services"].get("hermes-gateway", "unknown"), "host": "VPS"},
        {"name": "Reminders", "id": "jack-reminders", "role": "zero-token nudges",
         "state": snap["services"].get("jack-reminders", "unknown"), "host": "VPS"},
        {"name": "Briefing", "id": "jack-briefing", "role": "morning report",
         "state": snap["services"].get("jack-briefing", "unknown"), "host": "VPS"},
        {"name": "Proactive", "id": "jack-proactive", "role": "life monitor",
         "state": snap["services"].get("jack-proactive", "unknown"), "host": "VPS"},
        {"name": "Google Bridge", "id": "jack-google", "role": "calendar·mail·orsa",
         "state": "active" if (bridge and bridge.get("status") == "ok") else "down", "host": "Mac"},
        {"name": "Garmin", "id": "garmin", "role": "health relay",
         "state": "active" if (garmin and garmin.get("status") == "ok") else "down", "host": "Mac"},
    ]
    online = sum(1 for a in agents if a["state"] == "active")
    return {"available": True, "agents": agents, "online": online, "total": len(agents),
            "vps_reachable": snap.get("available", False)}


def api_calendar() -> dict:
    try:
        from integrations.calendar import CalendarClient
        events = CalendarClient().list_events()
        if events is None:
            return {"available": False}
        return {"available": True, "events": [
            {"summary": e.get("summary", "(no title)"),
             "start": (e.get("start", {}) or {}).get("dateTime") or (e.get("start", {}) or {}).get("date", "")}
            for e in events]}
    except Exception:  # noqa: BLE001
        return {"available": False}


def api_email() -> dict:
    try:
        from integrations.gmail import GmailClient
        unread = GmailClient().list_unread(max_results=8)
        if unread is None:
            return {"available": False}
        return {"available": True, "count": len(unread),
                "messages": [{"from": m.get("from", ""), "subject": m.get("subject", ""),
                              "from_email": m.get("from_email", "")} for m in unread]}
    except Exception:  # noqa: BLE001
        return {"available": False}


def api_leads() -> dict:
    try:
        from integrations.orsa import OrsaClient
        stats = OrsaClient().pipeline_stats()
        if stats is None:
            return {"available": False}
        stats["available"] = True
        return stats
    except Exception:  # noqa: BLE001
        return {"available": False}


def api_health() -> dict:
    data = _http_json("http://localhost:8765/daily_summary", timeout=6.0)
    if not isinstance(data, dict):
        return {"available": False}
    return {"available": True, **data}


def _fmt_ist(iso_utc: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(_IST)
        return dt.strftime("%a %-d %b · %-I:%M %p")
    except Exception:  # noqa: BLE001
        return iso_utc


def api_reminders() -> dict:
    snap = _vps_snapshot()
    rems = snap.get("reminders")
    if rems is None:
        return {"available": snap.get("available", False), "upcoming": []}
    now = datetime.now(timezone.utc)
    upcoming = []
    for r in rems:
        if r.get("fired"):
            continue
        try:
            fire = datetime.fromisoformat(str(r.get("fire_at", "")).replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            continue
        if fire >= now - timedelta(hours=1):
            upcoming.append({"message": r.get("message", ""), "fire_at_ist": _fmt_ist(r.get("fire_at", "")),
                             "recurring": bool(r.get("recurring")), "ts": fire.timestamp()})
    upcoming.sort(key=lambda x: x["ts"])
    return {"available": True, "upcoming": upcoming[:8], "total_pending": len(upcoming)}


def api_goals() -> dict:
    snap = _vps_snapshot()
    goals = snap.get("goals")
    if goals is None:
        return {"available": snap.get("available", False), "goals": []}
    out = []
    today = datetime.now(_IST).date()
    for g in goals:
        days_left = None
        try:
            dl = datetime.fromisoformat(str(g.get("deadline"))).date()
            days_left = (dl - today).days
        except Exception:  # noqa: BLE001
            pass
        out.append({"title": g.get("title", ""), "type": g.get("type", ""),
                    "status": g.get("status", ""), "deadline": g.get("deadline"),
                    "days_left": days_left, "notes": len(g.get("progress_notes", []) or [])})
    return {"available": True, "goals": out}


def api_nudges() -> dict:
    snap = _vps_snapshot()
    log = snap.get("proactive")
    if not isinstance(log, list):
        return {"available": snap.get("available", False), "recent": []}
    recent = []
    for e in log[-8:][::-1]:
        recent.append({"text": e.get("text") or e.get("message") or "",
                       "priority": e.get("priority") or e.get("tier") or "",
                       "at": e.get("sent_at") or e.get("at") or ""})
    return {"available": True, "recent": recent, "total": len(log)}


def api_classify(text: str) -> dict:
    """Preview how Jack routes a message — read-only, never executes."""
    try:
        import jack_intent_router as router
        route = router.classify(text or "")
        if route is None:
            return {"intent": None, "note": "empty"}
        return {"intent": route.intent, "params": {k: v for k, v in route.params.items() if k != "text"}}
    except Exception:  # noqa: BLE001
        return {"intent": None, "note": "router unavailable"}


def api_overview() -> dict:
    """Light aggregate for the header/core (counts only, fast)."""
    svc = api_services()
    return {"time_ist": _now_ist_iso(), "online": svc["online"], "total": svc["total"],
            "vps_reachable": svc["vps_reachable"]}


_ROUTES = {
    "/api/overview": lambda p: api_overview(),
    "/api/services": lambda p: api_services(),
    "/api/calendar": lambda p: api_calendar(),
    "/api/email": lambda p: api_email(),
    "/api/leads": lambda p: api_leads(),
    "/api/health": lambda p: api_health(),
    "/api/reminders": lambda p: api_reminders(),
    "/api/goals": lambda p: api_goals(),
    "/api/nudges": lambda p: api_nudges(),
    "/api/classify": lambda p: api_classify(p.get("q", [""])[0]),
}

_CONTENT_TYPES = {".html": "text/html", ".css": "text/css", ".js": "application/javascript",
                  ".svg": "image/svg+xml", ".json": "application/json"}


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path in _ROUTES:
            try:
                data = _ROUTES[path](urllib.parse.parse_qs(parsed.query))
            except Exception:  # noqa: BLE001
                data = {"available": False, "error": "internal"}
            self._send(json.dumps(data).encode(), "application/json")
            return
        # static files
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (_STATIC / rel).resolve()
        if not str(target).startswith(str(_STATIC)) or not target.is_file():
            self._send(b"not found", "text/plain", 404)
            return
        ctype = _CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        self._send(target.read_bytes(), ctype)


def main() -> None:
    print(f"⚡ Jack OS dashboard → http://localhost:{PORT}  (Ctrl-C to stop)")
    # Warm the VPS snapshot in the background so the first page load isn't blocked on SSH.
    import threading

    threading.Thread(target=_vps_snapshot, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), DashboardHandler).serve_forever()


if __name__ == "__main__":
    main()
