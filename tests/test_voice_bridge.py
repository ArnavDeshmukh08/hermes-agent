"""Tests for the VPS voice bridge HTTP server.

Verifies routing, JSON handling, and integration with JackConversationHandler
without requiring audio hardware or network access.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import threading
import unittest
from http.client import HTTPConnection
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

os.environ.setdefault("HERMES_LLM_MOCK", "1")
os.environ.setdefault("HERMES_ROOT", str(_REPO))
os.environ.setdefault("HERMES_HOME", str(_REPO))
os.environ.setdefault("JACK_VOICE_BRIDGE_PORT", "18766")  # test port


def _post(conn: HTTPConnection, path: str, body: dict) -> tuple[int, dict]:
    payload = json.dumps(body).encode()
    conn.request("POST", path, body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    return resp.status, data


def _get(conn: HTTPConnection, path: str) -> tuple[int, dict]:
    conn.request("GET", path)
    resp = conn.getresponse()
    data = json.loads(resp.read())
    return resp.status, data


class _MockHandler:
    async def respond(self, text: str, user_id: str) -> str:
        return f"Echo: {text}"


def _make_server(port: int) -> HTTPServer:
    from voice_bridge.server import _Handler, _get_handler, _sync_respond
    return HTTPServer(("127.0.0.1", port), _Handler)


class TestVoiceBridgeRouting(unittest.TestCase):
    PORT = 18766

    def setUp(self):
        mock_handler = _MockHandler()

        # Store the PATCHER objects (not the mocks they return) so stop() works.
        self._patcher_get = patch("voice_bridge.server._get_handler", return_value=mock_handler)
        self._patcher_respond = patch(
            "voice_bridge.server._sync_respond",
            side_effect=lambda text, session_id: f"Echo: {text}",
        )
        self._patcher_get.start()
        self._patcher_respond.start()

        from voice_bridge.server import _Handler
        self._server = HTTPServer(("127.0.0.1", self.PORT), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self):
        self._server.shutdown()
        self._patcher_respond.stop()
        self._patcher_get.stop()

    def _conn(self) -> HTTPConnection:
        return HTTPConnection("127.0.0.1", self.PORT)

    def test_health_get(self):
        conn = self._conn()
        status, data = _get(conn, "/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["service"], "jack-voice-bridge")

    def test_unknown_path_returns_404(self):
        conn = self._conn()
        status, data = _get(conn, "/nonexistent")
        self.assertEqual(status, 404)
        self.assertFalse(data["ok"])

    def test_voice_post_calls_handler(self):
        conn = self._conn()
        status, data = _post(conn, "/voice", {"text": "How did I sleep?", "session_id": "test"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIn("Echo:", data["reply"])

    def test_voice_post_empty_text_returns_400(self):
        conn = self._conn()
        status, data = _post(conn, "/voice", {"text": "   "})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])
        self.assertIn("empty", data["error"])

    def test_voice_post_missing_text_returns_400(self):
        conn = self._conn()
        status, data = _post(conn, "/voice", {"session_id": "test"})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])

    def test_voice_post_invalid_json_returns_400(self):
        conn = self._conn()
        conn.request("POST", "/voice", body=b"not json", headers={"Content-Type": "application/json", "Content-Length": "8"})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        self.assertEqual(resp.status, 400)
        self.assertFalse(data["ok"])

    def test_voice_post_wrong_path_returns_404(self):
        conn = self._conn()
        status, data = _post(conn, "/other", {"text": "hello"})
        self.assertEqual(status, 404)


class TestVoiceBridgeHandlerError(unittest.TestCase):
    PORT = 18767

    def setUp(self):
        async def _raise_respond(text, user_id):
            raise RuntimeError("brain exploded")

        mock_handler = MagicMock()
        mock_handler.respond = _raise_respond

        # Store the PATCHER (not the mock) so stop() properly removes the patch.
        self._patcher = patch("voice_bridge.server._get_handler", return_value=mock_handler)
        self._patcher.start()

        from voice_bridge.server import _Handler
        self._server = HTTPServer(("127.0.0.1", self.PORT), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self):
        self._server.shutdown()
        self._patcher.stop()

    def test_bridge_error_returns_500(self):
        conn = HTTPConnection("127.0.0.1", self.PORT)
        status, data = _post(conn, "/voice", {"text": "hello"})
        self.assertEqual(status, 500)
        self.assertFalse(data["ok"])
        self.assertIn("brain exploded", data["error"])


class TestVoiceBridgeSyncRespond(unittest.TestCase):
    """Unit-test _sync_respond wiring without a live HTTP server."""

    def test_sync_respond_calls_handler_respond(self):
        import voice_bridge.server as vbs

        loop = asyncio.new_event_loop()

        async def _fake_respond(text, user_id):
            return f"Reply to: {text}"

        mock_handler = MagicMock()
        mock_handler.respond = _fake_respond

        with (
            patch("voice_bridge.server._get_loop", return_value=loop),
            patch("voice_bridge.server._get_handler", return_value=mock_handler),
        ):
            result = vbs._sync_respond("what time is it?", "voice-mac")

        self.assertEqual(result, "Reply to: what time is it?")
        loop.close()

    def test_sync_respond_session_id_forwarded(self):
        import voice_bridge.server as vbs

        loop = asyncio.new_event_loop()
        captured: dict = {}

        async def _capture(text, user_id):
            captured["user_id"] = user_id
            return "ok"

        mock_handler = MagicMock()
        mock_handler.respond = _capture

        with (
            patch("voice_bridge.server._get_loop", return_value=loop),
            patch("voice_bridge.server._get_handler", return_value=mock_handler),
        ):
            vbs._sync_respond("hello", "voice-mac")

        self.assertEqual(captured["user_id"], "voice-mac")
        loop.close()


if __name__ == "__main__":
    unittest.main()
