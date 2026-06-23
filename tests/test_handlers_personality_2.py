"""Tests verifying that reminders, calendar, and self_config handlers route
successful data responses through the personality layer (_voice).

Pattern: patch handler._voice to return fallback_plain (so no real LLM runs)
and assert it was called with the expected intent and data.
Error/disambiguation/service-down paths are tested to confirm _voice is NOT called.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "hooks" / "jack_router"))

os.environ.setdefault("HERMES_LLM_MOCK", "1")
os.environ.setdefault("HERMES_ROOT", str(_REPO))

import handler  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _fake_channel():
    ch = MagicMock()
    ch.send = AsyncMock()
    return ch


def _fake_message(content="", channel=None):
    msg = MagicMock()
    msg.content = content
    msg.channel = channel or _fake_channel()
    msg.author.id = "99"
    msg.id = "msg-002"
    return msg


def _make_route(intent, **params):
    from jack_intent_router import Route  # noqa: PLC0415
    return Route(intent, params)


def _fire_at_utc():
    return datetime(2026, 6, 23, 15, 30, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Reminder handler tests
# ---------------------------------------------------------------------------

class TestReminderPersonalityRouting(unittest.TestCase):
    def _voice_capture(self):
        calls = []
        def _fv(intent, data, user_message, fallback_plain):
            calls.append({"intent": intent, "data": data, "fallback": fallback_plain})
            return fallback_plain
        return calls, _fv

    def test_reminder_set_routes_through_voice(self):
        msg = _fake_message("remind me to call Spandan at 9pm")
        route = _make_route("reminder", action="set", text="remind me to call Spandan at 9pm")
        calls, fv = self._voice_capture()
        parsed = MagicMock()
        parsed.fire_at = _fire_at_utc()
        parsed.recurring = False
        with patch("handler._voice", side_effect=fv), \
             patch("reminders.store.ReminderStore") as MockStore, \
             patch("reminders.parser.parse_time", return_value=parsed):
            store = MockStore.return_value
            store.add.return_value = None
            handler._STORE = None
            with patch("reminders.store.ReminderStore", return_value=store):
                _run(handler._run_reminder(msg, route))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["intent"], "reminder_set")

    def test_reminder_set_data_preserves_time_and_message(self):
        msg = _fake_message("remind me to call Spandan at 9pm")
        route = _make_route("reminder", action="set", text="remind me to call Spandan at 9pm")
        calls, fv = self._voice_capture()
        parsed = MagicMock()
        parsed.fire_at = _fire_at_utc()
        parsed.recurring = False
        with patch("handler._voice", side_effect=fv), \
             patch("reminders.store.ReminderStore") as MockStore, \
             patch("reminders.parser.parse_time", return_value=parsed):
            store = MockStore.return_value
            store.add.return_value = None
            handler._STORE = None
            with patch("reminders.store.ReminderStore", return_value=store):
                _run(handler._run_reminder(msg, route))
        data = calls[0]["data"]
        self.assertIn("message", data)
        self.assertIn("fire_at_ist", data)
        self.assertTrue(len(data["fire_at_ist"]) > 0)

    def test_reminder_set_fallback_preserved_on_compose_down(self):
        """If compose raises, channel.send still receives the old 'Got it ⏰' string."""
        msg = _fake_message("remind me to eat at 1pm")
        route = _make_route("reminder", action="set", text="remind me to eat at 1pm")
        parsed = MagicMock()
        parsed.fire_at = _fire_at_utc()
        parsed.recurring = False
        ch = _fake_channel()
        msg.channel = ch
        with patch("jack_voice.compose.compose_reply", side_effect=RuntimeError("down")), \
             patch("reminders.store.ReminderStore") as MockStore, \
             patch("reminders.parser.parse_time", return_value=parsed):
            store = MockStore.return_value
            store.add.return_value = None
            handler._STORE = None
            with patch("reminders.store.ReminderStore", return_value=store):
                _run(handler._run_reminder(msg, route))
        sent = ch.send.call_args_list[0][0][0]
        self.assertTrue(len(sent) > 0)

    def test_reminder_list_routes_through_voice(self):
        msg = _fake_message("what reminders do I have?")
        route = _make_route("reminder", action="list", text="what reminders do I have?")
        items = [{"id": "r1", "message": "call mom", "fire_at": _fire_at_utc().isoformat(), "recurring": False}]
        calls, fv = self._voice_capture()
        with patch("handler._voice", side_effect=fv), \
             patch("reminders.store.ReminderStore") as MockStore:
            store = MockStore.return_value
            store.list_pending.return_value = items
            handler._STORE = None
            with patch("reminders.store.ReminderStore", return_value=store):
                _run(handler._run_reminder(msg, route))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["intent"], "reminder_list")
        self.assertIn("reminders", calls[0]["data"])
        self.assertEqual(len(calls[0]["data"]["reminders"]), 1)

    def test_reminder_list_empty_bypasses_voice(self):
        """No reminders → direct '📭 No reminders set.' — voice not called."""
        msg = _fake_message("reminders?")
        route = _make_route("reminder", action="list", text="reminders?")
        with patch("handler._voice") as mock_voice, \
             patch("reminders.store.ReminderStore") as MockStore:
            store = MockStore.return_value
            store.list_pending.return_value = []
            handler._STORE = None
            with patch("reminders.store.ReminderStore", return_value=store):
                _run(handler._run_reminder(msg, route))
        mock_voice.assert_not_called()

    def test_reminder_cancel_routes_through_voice(self):
        msg = _fake_message("cancel the call mom reminder")
        route = _make_route("reminder", action="cancel", text="cancel the call mom reminder")
        items = [{"id": "r1", "message": "call mom", "fire_at": _fire_at_utc().isoformat(), "recurring": False}]
        calls, fv = self._voice_capture()
        with patch("handler._voice", side_effect=fv), \
             patch("reminders.store.ReminderStore") as MockStore:
            store = MockStore.return_value
            store.list_pending.return_value = items
            store.cancel.return_value = True
            handler._STORE = None
            with patch("reminders.store.ReminderStore", return_value=store):
                _run(handler._run_reminder(msg, route))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["intent"], "reminder_cancel")
        self.assertEqual(calls[0]["data"]["cancelled_message"], "call mom")


# ---------------------------------------------------------------------------
# Calendar handler tests
# ---------------------------------------------------------------------------

class TestCalendarPersonalityRouting(unittest.TestCase):
    def _voice_capture(self):
        calls = []
        def _fv(intent, data, user_message, fallback_plain):
            calls.append({"intent": intent, "data": data, "fallback": fallback_plain})
            return fallback_plain
        return calls, _fv

    def test_calendar_list_routes_through_voice(self):
        msg = _fake_message("what's on my calendar today?")
        route = _make_route("calendar", action="list", text="what's on my calendar today?")
        calls, fv = self._voice_capture()
        with patch("handler._voice", side_effect=fv), \
             patch("integrations.calendar.CalendarClient") as MockCal:
            client = MockCal.return_value
            client.list_events.return_value = [{"summary": "Team standup", "start": "10:00"}]
            client.events_summary_text.return_value = "10:00 — Team standup"
            _run(handler._run_calendar(msg, route))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["intent"], "calendar_list")
        self.assertIn("events_summary", calls[0]["data"])
        self.assertIn("Team standup", calls[0]["data"]["events_summary"])

    def test_calendar_list_empty_bypasses_voice(self):
        """No events → direct 'Nothing on the calendar today' — voice not called."""
        msg = _fake_message("calendar?")
        route = _make_route("calendar", action="list", text="calendar?")
        with patch("handler._voice") as mock_voice, \
             patch("integrations.calendar.CalendarClient") as MockCal:
            client = MockCal.return_value
            client.list_events.return_value = []
            _run(handler._run_calendar(msg, route))
        mock_voice.assert_not_called()

    def test_calendar_add_routes_through_voice(self):
        msg = _fake_message("add team standup at 10am today")
        route = _make_route("calendar", action="add", text="add team standup at 10am today")
        calls, fv = self._voice_capture()
        parsed_time = MagicMock()
        parsed_time.fire_at = _fire_at_utc()
        with patch("handler._voice", side_effect=fv), \
             patch("integrations.calendar.CalendarClient") as MockCal, \
             patch("reminders.parser.parse_time", return_value=parsed_time):
            client = MockCal.return_value
            client.add_event.return_value = {"summary": "team standup", "id": "evt-001"}
            _run(handler._run_calendar(msg, route))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["intent"], "calendar_add")
        self.assertIn("event_title", calls[0]["data"])
        self.assertIn("when_ist", calls[0]["data"])
        self.assertEqual(calls[0]["data"]["event_title"], "team standup")

    def test_calendar_not_connected_bypasses_voice(self):
        """Calendar returns None → direct 'not connected' message, voice not called."""
        msg = _fake_message("what's on my calendar?")
        route = _make_route("calendar", action="list", text="what's on my calendar?")
        with patch("handler._voice") as mock_voice, \
             patch("integrations.calendar.CalendarClient") as MockCal:
            client = MockCal.return_value
            client.list_events.return_value = None
            _run(handler._run_calendar(msg, route))
        mock_voice.assert_not_called()


# ---------------------------------------------------------------------------
# Self-config handler tests
# ---------------------------------------------------------------------------

class TestSelfConfigPersonalityRouting(unittest.TestCase):
    def _voice_capture(self):
        calls = []
        def _fv(intent, data, user_message, fallback_plain):
            calls.append({"intent": intent, "data": data, "fallback": fallback_plain})
            return fallback_plain
        return calls, _fv

    def _fake_self_config(self, set_result=None, configurable=None, get_status=None):
        mock_c = MagicMock()
        mock_c.get_status.return_value = get_status or {"hermes-gateway.service": "active"}
        mock_c.list_configurable.return_value = configurable or [
            {"key": "JACK_BRIEFING_TIME_IST", "friendly": "briefing_time", "value": "07:00", "type": "time", "description": "Briefing time"}
        ]
        if set_result is not None:
            mock_c.set.return_value = set_result
        return mock_c

    def test_self_config_status_all_ok_routes_through_voice(self):
        msg = _fake_message("status?")
        route = _make_route("self_config", action="status", text="status?")
        mock_c = self._fake_self_config()
        calls, fv = self._voice_capture()
        with patch("handler._voice", side_effect=fv), \
             patch("jack_tools.self_config.JackSelfConfig", return_value=mock_c):
            _run(handler._run_self_config(msg, route))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["intent"], "self_config_status")
        self.assertTrue(calls[0]["data"]["all_active"])

    def test_self_config_set_ok_routes_through_voice(self):
        msg = _fake_message("change briefing to 8am")
        route = _make_route("self_config", action="set", text="change briefing to 8am")
        mock_c = self._fake_self_config(set_result={"success": True, "message": "Updated."})
        calls, fv = self._voice_capture()
        with patch("handler._voice", side_effect=fv), \
             patch("jack_tools.self_config.JackSelfConfig", return_value=mock_c), \
             patch("handler._parse_config_request", return_value={"key": "JACK_BRIEFING_TIME_IST", "value": "08:00"}):
            _run(handler._run_self_config(msg, route))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["intent"], "self_config_set")
        self.assertTrue(calls[0]["data"]["success"])

    def test_self_config_set_failure_routes_through_voice(self):
        """Failed set also goes through voice so Jack can voice the failure."""
        msg = _fake_message("change briefing to 25:00")
        route = _make_route("self_config", action="set", text="change briefing to 25:00")
        mock_c = self._fake_self_config(set_result={"success": False, "message": "Invalid time."})
        calls, fv = self._voice_capture()
        with patch("handler._voice", side_effect=fv), \
             patch("jack_tools.self_config.JackSelfConfig", return_value=mock_c), \
             patch("handler._parse_config_request", return_value={"key": "JACK_BRIEFING_TIME_IST", "value": "25:00"}):
            _run(handler._run_self_config(msg, route))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["intent"], "self_config_set")
        self.assertFalse(calls[0]["data"]["success"])
        # HONESTY CHECK: the fallback (which _voice returns here) must NOT claim success
        self.assertNotIn("✅", calls[0]["fallback"])
        self.assertNotIn("Done", calls[0]["fallback"])


if __name__ == "__main__":
    unittest.main()
