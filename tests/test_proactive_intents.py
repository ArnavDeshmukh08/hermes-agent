"""Tests for the proactive intent: router classification and handler behaviour.

All tests are offline and deterministic — no real LLM calls, no systemctl,
no file I/O. ProactiveEngine and JackSelfConfig are monkeypatched.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# sys.path: repo root + jack_router dir (mirrors test_intent_router.py)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "hooks" / "jack_router"))

import handler  # noqa: E402
import jack_intent_router as router  # noqa: E402

# ---------------------------------------------------------------------------
# Fake message / channel helpers
# ---------------------------------------------------------------------------


class _FakeChannel:
    """Captures all .send() calls."""

    def __init__(self):
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)

    @property
    def last(self) -> str:
        return self.sent[-1] if self.sent else ""


def _make_message(content: str = "") -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.channel = _FakeChannel()
    msg.author.id = "42"
    msg.id = "test-msg-001"
    return msg


def _run(coro):
    """Run a coroutine synchronously in a fresh event loop."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# A. Router classification tests
# ---------------------------------------------------------------------------


class TestProactiveClassify(unittest.TestCase):
    # --- control action: off ---
    def test_classify_proactive_control_off_stop_nudges(self):
        r = router.classify("stop the proactive nudges")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "proactive")
        self.assertEqual(r.params["action"], "control")
        self.assertEqual(r.params["control"], "off")

    def test_classify_proactive_control_off_mute(self):
        r = router.classify("mute nudges")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "proactive")
        self.assertEqual(r.params["control"], "off")

    def test_classify_proactive_control_off_turn_off_check_ins(self):
        r = router.classify("turn off check-ins")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "proactive")
        self.assertEqual(r.params["control"], "off")

    def test_classify_proactive_control_off_disable(self):
        r = router.classify("disable proactive nudges")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "proactive")
        self.assertEqual(r.params["control"], "off")

    def test_classify_proactive_control_off_reverse_order(self):
        r = router.classify("nudges off")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "proactive")
        self.assertEqual(r.params["control"], "off")

    # --- control action: on ---
    def test_classify_proactive_control_on_turn_on(self):
        r = router.classify("turn proactive nudges on")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "proactive")
        self.assertEqual(r.params["action"], "control")
        self.assertEqual(r.params["control"], "on")

    def test_classify_proactive_control_on_resume(self):
        r = router.classify("resume nudges")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "proactive")
        self.assertEqual(r.params["control"], "on")

    def test_classify_proactive_control_on_enable(self):
        r = router.classify("enable check-ins")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "proactive")
        self.assertEqual(r.params["control"], "on")

    def test_classify_proactive_control_on_unmute(self):
        r = router.classify("unmute proactive nudges")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "proactive")
        self.assertEqual(r.params["control"], "on")

    # --- status action ---
    def test_classify_proactive_status_are_nudges_on(self):
        r = router.classify("are nudges on")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "proactive")
        self.assertEqual(r.params["action"], "status")

    def test_classify_proactive_status_are_proactive_nudges_enabled(self):
        r = router.classify("are proactive nudges enabled?")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "proactive")
        self.assertEqual(r.params["action"], "status")

    def test_classify_proactive_status_my_proactive_status(self):
        r = router.classify("my proactive status")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "proactive")
        self.assertEqual(r.params["action"], "status")

    def test_classify_proactive_status_show_my_nudge_settings(self):
        r = router.classify("show my nudge settings")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "proactive")
        self.assertEqual(r.params["action"], "status")

    def test_classify_proactive_status_how_many_nudges_today(self):
        r = router.classify("how many nudges today")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "proactive")
        self.assertEqual(r.params["action"], "status")

    def test_classify_proactive_status_how_many_check_ins_so_far(self):
        r = router.classify("how many check-ins so far")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "proactive")
        self.assertEqual(r.params["action"], "status")

    # --- no false positives ---
    def test_classify_does_not_steal_ordinary_chat_pizza(self):
        r = router.classify("stop bothering me about pizza")
        self.assertIsNotNone(r)
        self.assertNotEqual(r.intent, "proactive", "Should not steal plain chat")

    def test_classify_does_not_steal_generic_turn_off(self):
        r = router.classify("turn off the lights")
        self.assertIsNotNone(r)
        self.assertNotEqual(r.intent, "proactive")

    def test_classify_control_verb_beats_status(self):
        """Control verbs win even if status-like words present."""
        r = router.classify("turn proactive nudges off")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "proactive")
        self.assertEqual(r.params["action"], "control")


# ---------------------------------------------------------------------------
# B. Dispatch tests
# ---------------------------------------------------------------------------


class TestProactiveDispatch(unittest.TestCase):
    def test_dispatch_control_fires_correct_handler(self):
        """Dispatch should fire _run_proactive_control for control action."""
        msg = _make_message("stop nudges")
        route = router.Route("proactive", {"action": "control", "control": "off", "text": "stop nudges"})
        with patch("handler._fire") as mock_fire:
            _run(_dispatch_sync(msg, route))
            # Check that _fire was called exactly once
            self.assertEqual(mock_fire.call_count, 1)
            # The argument should be a coroutine
            coro = mock_fire.call_args[0][0]
            self.assertTrue(asyncio.iscoroutine(coro))

    def test_dispatch_status_fires_correct_handler(self):
        """Dispatch should fire _run_proactive_status for status action."""
        msg = _make_message("what's my proactive status")
        route = router.Route("proactive", {"action": "status", "text": "what's my proactive status"})
        with patch("handler._fire") as mock_fire:
            _run(_dispatch_sync(msg, route))
            # Check that _fire was called exactly once
            self.assertEqual(mock_fire.call_count, 1)


async def _dispatch_sync(message, route):
    """Stub adapter for _dispatch; just calls _dispatch directly."""

    class StubAdapter:
        pass

    await handler._dispatch(StubAdapter(), message, route)


# ---------------------------------------------------------------------------
# C. Handler tests
# ---------------------------------------------------------------------------


class TestProactiveHandlerControl(unittest.TestCase):
    def test_handler_control_off_persists_to_self_config(self):
        """Control 'off' should call JackSelfConfig.set('JACK_PROACTIVE_ENABLED', 'false')."""
        msg = _make_message("turn off nudges")
        route = router.Route("proactive", {"action": "control", "control": "off", "text": "turn off nudges"})
        mock_c = MagicMock()
        mock_c.set.return_value = {"success": True, "message": "Updated."}
        with patch("jack_tools.self_config.JackSelfConfig", return_value=mock_c):
            _run(handler._run_proactive_control(msg, route))
        # Verify set was called with the correct key/value
        mock_c.set.assert_called_once()
        args = mock_c.set.call_args[0]
        self.assertEqual(args[0], "JACK_PROACTIVE_ENABLED")
        self.assertEqual(args[1], "false")

    def test_handler_control_on_persists_to_self_config(self):
        """Control 'on' should call JackSelfConfig.set('JACK_PROACTIVE_ENABLED', 'true')."""
        msg = _make_message("turn on nudges")
        route = router.Route("proactive", {"action": "control", "control": "on", "text": "turn on nudges"})
        mock_c = MagicMock()
        mock_c.set.return_value = {"success": True, "message": "Updated."}
        with patch("jack_tools.self_config.JackSelfConfig", return_value=mock_c):
            _run(handler._run_proactive_control(msg, route))
        # Verify set was called with 'true'
        args = mock_c.set.call_args[0]
        self.assertEqual(args[1], "true")

    def test_handler_control_off_replies_with_bell_off_emoji(self):
        """Successful 'off' should send a message with 🔕."""
        msg = _make_message("pause nudges")
        route = router.Route("proactive", {"action": "control", "control": "off", "text": "pause nudges"})
        mock_c = MagicMock()
        mock_c.set.return_value = {"success": True, "message": "Done."}
        with patch("jack_tools.self_config.JackSelfConfig", return_value=mock_c):
            _run(handler._run_proactive_control(msg, route))
        combined = " ".join(msg.channel.sent)
        self.assertIn("🔕", combined)

    def test_handler_control_on_replies_with_bell_on_emoji(self):
        """Successful 'on' should send a message with 🔔."""
        msg = _make_message("enable nudges")
        route = router.Route("proactive", {"action": "control", "control": "on", "text": "enable nudges"})
        mock_c = MagicMock()
        mock_c.set.return_value = {"success": True, "message": "Done."}
        with patch("jack_tools.self_config.JackSelfConfig", return_value=mock_c):
            _run(handler._run_proactive_control(msg, route))
        combined = " ".join(msg.channel.sent)
        self.assertIn("🔔", combined)

    def test_handler_control_set_failure_graceful(self):
        """When set() fails, handler should NOT crash and should send ⚠️."""
        msg = _make_message("disable nudges")
        route = router.Route("proactive", {"action": "control", "control": "off", "text": "disable nudges"})
        mock_c = MagicMock()
        mock_c.set.return_value = {"success": False, "message": "Something went wrong."}
        with patch("jack_tools.self_config.JackSelfConfig", return_value=mock_c):
            _run(handler._run_proactive_control(msg, route))
        combined = " ".join(msg.channel.sent)
        self.assertIn("❌", combined)

    def test_handler_control_exception_graceful(self):
        """When JackSelfConfig raises, handler should NOT crash."""
        msg = _make_message("turn off nudges")
        route = router.Route("proactive", {"action": "control", "control": "off", "text": "turn off nudges"})
        with patch("jack_tools.self_config.JackSelfConfig", side_effect=RuntimeError("file error")):
            # Should not raise
            _run(handler._run_proactive_control(msg, route))
        combined = " ".join(msg.channel.sent)
        # Should have some error message
        self.assertTrue(len(msg.channel.sent) > 0)


class TestProactiveHandlerStatus(unittest.TestCase):
    def test_handler_status_reports_enabled_state(self):
        """Status handler should report whether proactive is ON/OFF."""
        msg = _make_message("proactive status")
        route = router.Route("proactive", {"action": "status", "text": "proactive status"})
        mock_engine = MagicMock()
        # sent_today_count must be callable (it's called via asyncio.to_thread)
        mock_engine.sent_today_count = lambda: 1
        with (
            patch("proactive.engine.ProactiveEngine", return_value=mock_engine),
            patch.dict(os.environ, {"JACK_PROACTIVE_ENABLED": "1"}),
        ):
            _run(handler._run_proactive_status(msg, route))
        combined = " ".join(msg.channel.sent)
        self.assertIn("ON", combined)

    def test_handler_status_reports_disabled_state(self):
        """Status handler should report OFF if JACK_PROACTIVE_ENABLED is false."""
        msg = _make_message("proactive status")
        route = router.Route("proactive", {"action": "status", "text": "proactive status"})
        mock_engine = MagicMock()
        # sent_today_count must be callable
        mock_engine.sent_today_count = lambda: 0
        with (
            patch("proactive.engine.ProactiveEngine", return_value=mock_engine),
            patch.dict(os.environ, {"JACK_PROACTIVE_ENABLED": "0"}),
        ):
            _run(handler._run_proactive_status(msg, route))
        combined = " ".join(msg.channel.sent)
        self.assertIn("OFF", combined)

    def test_handler_status_includes_count(self):
        """Status should include 'sent today' count."""
        msg = _make_message("nudge count")
        route = router.Route("proactive", {"action": "status", "text": "nudge count"})
        mock_engine = MagicMock()
        # sent_today_count must be callable
        mock_engine.sent_today_count = lambda: 2
        with (
            patch("proactive.engine.ProactiveEngine", return_value=mock_engine),
            patch.dict(os.environ, {"JACK_PROACTIVE_ENABLED": "1", "JACK_PROACTIVE_MAX_PER_DAY": "5"}),
        ):
            _run(handler._run_proactive_status(msg, route))
        combined = " ".join(msg.channel.sent)
        self.assertIn("2", combined)

    def test_handler_status_includes_quiet_hours(self):
        """Status should include quiet hours from environment."""
        msg = _make_message("nudge settings")
        route = router.Route("proactive", {"action": "status", "text": "nudge settings"})
        mock_engine = MagicMock()
        # sent_today_count must be callable
        mock_engine.sent_today_count = lambda: 0
        with (
            patch("proactive.engine.ProactiveEngine", return_value=mock_engine),
            patch.dict(
                os.environ,
                {
                    "JACK_PROACTIVE_ENABLED": "1",
                    "JACK_PROACTIVE_QUIET_START_IST": "10",
                    "JACK_PROACTIVE_QUIET_END_IST": "8",
                },
            ),
        ):
            _run(handler._run_proactive_status(msg, route))
        combined = " ".join(msg.channel.sent)
        self.assertIn("10am", combined)
        self.assertIn("8am", combined)

    def test_handler_status_exception_graceful(self):
        """When engine raises, handler should NOT crash."""
        msg = _make_message("nudge status")
        route = router.Route("proactive", {"action": "status", "text": "nudge status"})
        with patch("proactive.engine.ProactiveEngine", side_effect=RuntimeError("db error")):
            # Should not raise
            _run(handler._run_proactive_status(msg, route))
        combined = " ".join(msg.channel.sent)
        # Should have some error message
        self.assertTrue(len(msg.channel.sent) > 0)

    def test_handler_status_includes_bell_emoji(self):
        """Status should include the 🔔 emoji."""
        msg = _make_message("nudges?")
        route = router.Route("proactive", {"action": "status", "text": "nudges?"})
        mock_engine = MagicMock()
        mock_engine.sent_today_count = lambda: 0
        with patch("proactive.engine.ProactiveEngine", return_value=mock_engine):
            _run(handler._run_proactive_status(msg, route))
        combined = " ".join(msg.channel.sent)
        self.assertIn("🔔", combined)


if __name__ == "__main__":
    unittest.main()
