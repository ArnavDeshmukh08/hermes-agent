"""Tests for the self_config intent: router classification and handler behaviour.

All tests are offline and deterministic — no real LLM calls, no systemctl,
no file I/O.  JackSelfConfig and _parse_config_request are monkeypatched.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# sys.path: repo root + jack_router dir (mirrors test_intent_regression.py)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "hooks" / "jack_router"))

import handler  # noqa: E402
import jack_intent_router as router  # noqa: E402

# ---------------------------------------------------------------------------
# Fake message / channel helpers (mirrors test_calendar.py style)
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
# A.  Router classification tests
# ---------------------------------------------------------------------------

class TestSelfConfigClassify(unittest.TestCase):
    # --- set action ---
    def test_set_briefing_time(self):
        r = router.classify("change my briefing time to 8am")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "self_config")
        self.assertEqual(r.params["action"], "set")

    def test_set_turn_off_weather(self):
        r = router.classify("turn off weather")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "self_config")
        self.assertEqual(r.params["action"], "set")

    def test_set_enable_news(self):
        r = router.classify("enable news")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "self_config")
        self.assertEqual(r.params["action"], "set")

    def test_set_disable_memory(self):
        r = router.classify("disable memory")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "self_config")
        self.assertEqual(r.params["action"], "set")

    def test_set_reminder_poll(self):
        r = router.classify("set reminder poll to 60 seconds")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "self_config")
        self.assertEqual(r.params["action"], "set")

    def test_set_update_briefing(self):
        r = router.classify("update my briefing time to 7:30")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "self_config")
        self.assertEqual(r.params["action"], "set")

    # --- status action ---
    def test_status_are_you_running_okay(self):
        r = router.classify("are you running okay?")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "self_config")
        self.assertEqual(r.params["action"], "status")

    def test_status_are_you_ok(self):
        r = router.classify("are you ok")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "self_config")
        self.assertEqual(r.params["action"], "status")

    def test_status_whats_your_status(self):
        r = router.classify("what's your status")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "self_config")
        self.assertEqual(r.params["action"], "status")

    def test_status_system_status(self):
        r = router.classify("system status")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "self_config")
        self.assertEqual(r.params["action"], "status")

    def test_status_everything_ok(self):
        r = router.classify("is everything ok")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "self_config")
        self.assertEqual(r.params["action"], "status")

    def test_status_you_alive(self):
        r = router.classify("you alive?")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "self_config")
        self.assertEqual(r.params["action"], "status")

    # --- list action ---
    def test_list_what_can_i_configure(self):
        r = router.classify("what can I configure")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "self_config")
        self.assertEqual(r.params["action"], "list")

    def test_list_settings(self):
        r = router.classify("list your settings")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "self_config")
        self.assertEqual(r.params["action"], "list")

    def test_list_show_your_settings(self):
        r = router.classify("show your settings")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "self_config")
        self.assertEqual(r.params["action"], "list")

    def test_list_what_can_you_let_me_change(self):
        r = router.classify("what can you let me change")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "self_config")
        self.assertEqual(r.params["action"], "list")

    # --- no-hijack guards ---
    def test_no_hijack_reminder(self):
        r = router.classify("remind me to call mom at 6pm")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "reminder", f"got {r.intent!r} instead of reminder")

    def test_no_hijack_calendar(self):
        r = router.classify("add meeting to my calendar tomorrow")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "calendar", f"got {r.intent!r} instead of calendar")

    def test_no_hijack_status_queue(self):
        # bare "status" is task-queue status, NOT self_config
        r = router.classify("status")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "status")

    def test_no_hijack_queue(self):
        r = router.classify("queue")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "status")

    def test_set_requires_setting_noun(self):
        # "change my plan" has a verb but no known setting noun — should NOT be self_config/set
        r = router.classify("change my plan")
        self.assertIsNotNone(r)
        self.assertNotEqual(r.intent, "self_config")

    def test_set_unknown_setting_still_routes(self):
        # An UNKNOWN setting must still route to self_config so Jack can answer
        # honestly ("I don't have that setting") instead of a capability denial.
        for s in (
            "change the quantum setting to maximum",
            "update the foobar setting to 5",
            "configure my dashboard",
        ):
            r = router.classify(s)
            self.assertIsNotNone(r, s)
            self.assertEqual(r.intent, "self_config", f"{s!r} -> {r.intent}")
            self.assertEqual(r.params.get("action"), "set", s)

    def test_generic_does_not_steal_plain_chat(self):
        # No "setting(s)" word and no known noun => stays conversational.
        for s in ("change my plan for the day", "set the table for dinner"):
            r = router.classify(s)
            self.assertEqual(r.intent, "conversational", f"{s!r} -> {r.intent}")


# ---------------------------------------------------------------------------
# B.  Handler tests
# ---------------------------------------------------------------------------

def _fake_self_config(configurable=None, get_status=None, set_result=None):
    """Build a mock JackSelfConfig with controllable return values."""
    mock_c = MagicMock()
    mock_c.get_status.return_value = get_status or {
        "jack-briefing.service": "active",
        "jack-reminders.service": "active",
        "hermes-gateway.service": "active",
    }
    if configurable is None:
        configurable = [
            {"key": "JACK_BRIEFING_TIME_IST", "friendly": "briefing_time", "value": "07:00",
             "type": "time", "description": "Time (IST) at which the morning briefing fires"},
            {"key": "JACK_WEATHER_ENABLED", "friendly": "weather_enabled", "value": "1",
             "type": "bool", "description": "Include weather in the morning briefing"},
        ]
    mock_c.list_configurable.return_value = configurable
    if set_result is not None:
        mock_c.set.return_value = set_result
    return mock_c


class TestHandlerSelfConfigStatus(unittest.TestCase):
    def _run_status(self, get_status_dict):
        msg = _make_message("are you running okay?")
        route = router.Route("self_config", {"action": "status", "text": "are you running okay?"})
        mock_c = _fake_self_config(get_status=get_status_dict)
        with patch("jack_tools.self_config.JackSelfConfig", return_value=mock_c):
            _run(handler._run_self_config(msg, route))
        return msg.channel.sent

    def test_all_active_replies_all_systems_running(self):
        sent = self._run_status({
            "jack-briefing.service": "active",
            "jack-reminders.service": "active",
            "hermes-gateway.service": "active",
        })
        self.assertTrue(any("All systems running" in s for s in sent),
                        f"Expected 'All systems running' in {sent!r}")

    def test_partial_inactive_lists_services(self):
        sent = self._run_status({
            "jack-briefing.service": "active",
            "jack-reminders.service": "inactive",
            "hermes-gateway.service": "active",
        })
        # Should NOT say "All systems running"
        self.assertFalse(any("All systems running" in s for s in sent),
                         f"Should not say all running when one is inactive: {sent!r}")
        # Should mention the inactive service
        combined = " ".join(sent)
        self.assertIn("jack-reminders.service", combined)


class TestHandlerSelfConfigList(unittest.TestCase):
    def test_list_mentions_settings(self):
        msg = _make_message("what can I configure")
        route = router.Route("self_config", {"action": "list", "text": "what can I configure"})
        mock_c = _fake_self_config()
        with patch("jack_tools.self_config.JackSelfConfig", return_value=mock_c):
            _run(handler._run_self_config(msg, route))
        combined = " ".join(msg.channel.sent)
        self.assertIn("briefing_time", combined)
        self.assertIn("weather_enabled", combined)



class TestHandlerSelfConfigSet(unittest.TestCase):
    def test_set_success_sends_done_checkmark(self):
        msg = _make_message("change my briefing time to 8am")
        route = router.Route("self_config", {"action": "set", "text": "change my briefing time to 8am"})
        mock_c = _fake_self_config(set_result={"success": True, "message": "Updated JACK_BRIEFING_TIME_IST to 08:00."})
        parsed_val = {"key": "JACK_BRIEFING_TIME_IST", "value": "08:00"}
        with (
            patch("jack_tools.self_config.JackSelfConfig", return_value=mock_c),
            patch("handler._parse_config_request", return_value=parsed_val),
        ):
            _run(handler._run_self_config(msg, route))
        combined = " ".join(msg.channel.sent)
        self.assertIn("✅", combined)
        self.assertIn("Done", combined)

    def test_set_failure_no_done_no_checkmark(self):
        """CRITICAL: when result["success"] is False, must NOT say Done or ✅."""
        msg = _make_message("change briefing to 25:00")
        route = router.Route("self_config", {"action": "set", "text": "change briefing to 25:00"})
        mock_c = _fake_self_config(set_result={"success": False, "message": "'25:00' is not a valid time."})
        parsed_val = {"key": "JACK_BRIEFING_TIME_IST", "value": "25:00"}
        with (
            patch("jack_tools.self_config.JackSelfConfig", return_value=mock_c),
            patch("handler._parse_config_request", return_value=parsed_val),
        ):
            _run(handler._run_self_config(msg, route))
        combined = " ".join(msg.channel.sent)
        self.assertNotIn("✅", combined, f"Must not send ✅ on failure: {combined!r}")
        self.assertNotIn("Done", combined, f"Must not say Done on failure: {combined!r}")
        self.assertIn("❌", combined)

    def test_set_parse_failure_gives_friendly_list(self):
        """When _parse_config_request returns None, reply honestly without ✅/Done."""
        msg = _make_message("change the thing to something")
        route = router.Route("self_config", {"action": "set", "text": "change the thing to something"})
        mock_c = _fake_self_config()
        with (
            patch("jack_tools.self_config.JackSelfConfig", return_value=mock_c),
            patch("handler._parse_config_request", return_value=None),
        ):
            _run(handler._run_self_config(msg, route))
        combined = " ".join(msg.channel.sent)
        self.assertNotIn("✅", combined, f"Must not send ✅ when parse fails: {combined!r}")
        self.assertNotIn("Done", combined)
        # Should mention available settings
        self.assertTrue(
            "briefing_time" in combined or "can change" in combined or "can configure" in combined,
            f"Expected mention of configurable settings: {combined!r}",
        )

    def test_set_exception_from_c_set_is_handled(self):
        """If c.set() raises, handler must NOT send ✅ and must send a safe error."""
        msg = _make_message("set weather off")
        route = router.Route("self_config", {"action": "set", "text": "set weather off"})
        mock_c = _fake_self_config()
        mock_c.set.side_effect = RuntimeError("disk full")
        parsed_val = {"key": "JACK_WEATHER_ENABLED", "value": "false"}
        with (
            patch("jack_tools.self_config.JackSelfConfig", return_value=mock_c),
            patch("handler._parse_config_request", return_value=parsed_val),
        ):
            _run(handler._run_self_config(msg, route))
        combined = " ".join(msg.channel.sent)
        self.assertNotIn("✅", combined)
        self.assertNotIn("Done", combined)
        # Should send something (error message)
        self.assertTrue(len(msg.channel.sent) > 0)


if __name__ == "__main__":
    unittest.main()
