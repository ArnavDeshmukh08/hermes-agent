#!/usr/bin/env python3
"""Jack Voice Bridge — lightweight HTTP gateway for the Mac voice service.

POST /voice  {"text": "...", "session_id": "voice-mac"} → {"reply": "...", "ok": true}
GET  /health → {"ok": true, "service": "jack-voice-bridge"}

Reuses JackConversationHandler (same brain Discord uses): memory, personality, Groq.
Binds to 0.0.0.0:8766 (override: JACK_VOICE_BRIDGE_PORT).
Runs as a systemd --user service: jack-voice-bridge.service
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

_HERMES_ROOT = Path(os.environ.get("HERMES_HOME", Path(__file__).parent.parent))
sys.path.insert(0, str(_HERMES_ROOT))
# lib/llm.py lives under jack_worker/ on the VPS (not the project root)
_JACK_WORKER = _HERMES_ROOT / "jack_worker"
if _JACK_WORKER.is_dir():
    sys.path.insert(0, str(_JACK_WORKER))
os.environ.setdefault("HERMES_ROOT", str(_HERMES_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [voice-bridge] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("voice_bridge")

PORT = int(os.environ.get("JACK_VOICE_BRIDGE_PORT", "8766"))
_SESSION_ID_DEFAULT = "voice-mac"

# Persistent asyncio loop (requests are sequential — voice is one query at a time)
_loop: asyncio.AbstractEventLoop | None = None
_conversation_handler = None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop


def _get_handler():
    global _conversation_handler
    if _conversation_handler is None:
        from hooks.jack_router.conversation import JackConversationHandler
        _conversation_handler = JackConversationHandler()
        log.info("JackConversationHandler ready")
    return _conversation_handler


def _sync_respond(text: str, session_id: str) -> str:
    loop = _get_loop()
    handler = _get_handler()
    return loop.run_until_complete(handler.respond(text, user_id=session_id))


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # route to logging module
        log.info(fmt % args)

    def do_GET(self):
        if self.path.split("?")[0] == "/health":
            self._json({"ok": True, "service": "jack-voice-bridge", "port": PORT})
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        if self.path.split("?")[0] != "/voice":
            self._json({"ok": False, "error": "not found"}, 404)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return

        text = (payload.get("text") or "").strip()
        session_id = (payload.get("session_id") or _SESSION_ID_DEFAULT).strip()

        if not text:
            self._json({"ok": False, "error": "text is empty"}, 400)
            return

        log.info("voice turn [%s]: %r", session_id, text[:120])
        try:
            reply = _sync_respond(text, session_id)
            log.info("reply: %r", reply[:120])
            self._json({"ok": True, "reply": reply})
        except Exception as exc:  # noqa: BLE001
            log.exception("respond() failed")
            self._json({"ok": False, "error": str(exc)}, 500)

    def _json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    log.info("Warming up Jack conversation handler...")
    try:
        _get_handler()
    except Exception:  # noqa: BLE001
        log.exception("Handler warm-up failed — requests will fail until fixed")

    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    log.info("Jack Voice Bridge listening on 0.0.0.0:%d", PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
