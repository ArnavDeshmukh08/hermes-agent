"""Tests that health and goal handlers route successful data responses through
the personality layer (_voice / compose_reply).

Design:
- All tests mock jack_voice.compose.compose_reply (via handler._voice) to
  verify that (a) it is called with the expected intent and data, and
  (b) its return value is forwarded to channel.send.
- Error / service-down / disambiguation paths are also tested to confirm
  compose_reply is NOT called in those cases.
- HERMES_LLM_MOCK=1 prevents any real LLM calls; _voice itself is always
  patched here so no actual compose path runs.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

# Ensure project root and router dir are on the path.
_ROOT = Path(__file__).resolve().parents[1]
_ROUTER_DIR = _ROOT / "hooks" / "jack_router"
for _p in (str(_ROOT), str(_ROUTER_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("HERMES_LLM_MOCK", "1")
os.environ.setdefault("HERMES_ROOT", str(_ROOT))

import jack_intent_router as router  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _make_message(text: str = ""):
    channel = MagicMock()
    channel.send = AsyncMock()
    msg = MagicMock()
    msg.channel = channel
    msg.content = text
    return msg


def _make_goal(title="Run a marathon", deadline="2026-09-01", metric="manual"):
    """Create a minimal Goal-like mock."""
    g = MagicMock()
    g.id = "abc12345"
    g.title = title
    g.deadline = deadline
    g.metric = metric
    g.is_garmin = False
    g.to_dict.return_value = {
        "id": g.id,
        "title": title,
        "type": "fitness",
        "target": "42km race",
        "plan": "Train 4 days/week",
        "metric": metric,
        "status": "active",
        "deadline": deadline,
        "progress_notes": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "last_checked": None,
    }
    return g


# ---------------------------------------------------------------------------
# Shared _voice patcher
# ---------------------------------------------------------------------------

class _VoiceSpy:
    """Records the first call to _voice and returns a sentinel reply."""

    REPLY = "voiced reply from personality layer"

    def __init__(self):
        self.calls = []

    def __call__(self, intent, data, user_message, fallback_plain):
        self.calls.append({
            "intent": intent,
            "data": data,
            "user_message": user_message,
            "fallback_plain": fallback_plain,
        })
        return self.REPLY

    @property
    def called(self):
        return len(self.calls) > 0

    @property
    def call_count(self):
        return len(self.calls)

    @property
    def last_call(self):
        return self.calls[-1] if self.calls else None


# ===========================================================================
# HEALTH handler
# ===========================================================================

class TestHealthSleepPersonality(unittest.TestCase):
    """_run_health sleep path — successful data routes through compose."""

    def _run_health(self, text, mock_client, spy):
        import handler as h
        msg = _make_message(text)
        route = router.Route("health_query", {"text": text})
        with patch("integrations.garmin.GarminClient", return_value=mock_client), \
             patch("handler._voice", side_effect=spy):
            _run(h._run_health(msg, route))
        return msg

    def test_sleep_routes_through_compose(self):
        """Sleep success path calls _voice and sends its return value."""
        spy = _VoiceSpy()
        sleep_data = {"total_sleep_h": 7.2, "deep_h": 1.5, "rem_h": 0.9, "score": 76}
        client = MagicMock()
        client.last_night_sleep.return_value = sleep_data

        msg = self._run_health("how did I sleep", client, spy)

        self.assertTrue(spy.called, "_voice should have been called for sleep success")
        self.assertEqual(spy.last_call["intent"], "health_sleep")
        msg.channel.send.assert_called_once_with(_VoiceSpy.REPLY)

    def test_sleep_passes_sleep_dict_in_data(self):
        """data dict passed to _voice contains the sleep key with the real dict."""
        spy = _VoiceSpy()
        sleep_data = {"total_sleep_h": 5.8, "deep_h": 1.4, "rem_h": 0.4, "score": 61}
        client = MagicMock()
        client.last_night_sleep.return_value = sleep_data

        self._run_health("how did I sleep", client, spy)

        self.assertIn("sleep", spy.last_call["data"])
        self.assertEqual(spy.last_call["data"]["sleep"], sleep_data)

    def test_sleep_fallback_plain_contains_total_hours(self):
        """fallback_plain passed to _voice contains the formatted total hours."""
        spy = _VoiceSpy()
        sleep_data = {"total_sleep_h": 7.2, "deep_h": 1.5, "rem_h": 0.9, "score": 76}
        client = MagicMock()
        client.last_night_sleep.return_value = sleep_data

        # Use a phrase that classify_health_query routes to 'sleep'.
        self._run_health("how did I sleep last night", client, spy)

        self.assertIn("7.2", spy.last_call["fallback_plain"])

    def test_sleep_none_bypasses_compose(self):
        """When sleep is None (no data), _voice is NOT called."""
        spy = _VoiceSpy()
        client = MagicMock()
        client.last_night_sleep.return_value = None

        import handler as h
        msg = _make_message("how did I sleep")
        route = router.Route("health_query", {"text": "how did I sleep"})
        with patch("integrations.garmin.GarminClient", return_value=client), \
             patch("handler._voice", side_effect=spy):
            _run(h._run_health(msg, route))

        self.assertFalse(spy.called, "_voice should NOT be called when sleep is None")
        sent = msg.channel.send.call_args[0][0]
        self.assertIn("No sleep data", sent)

    def test_sleep_fallback_when_voice_raises(self):
        """If _voice raises, the handler still sends a non-empty string via the
        real _voice fallback mechanism (which returns fallback_plain on exception).
        Uses _voice patched to raise, verifying the outer try/except in _voice catches it."""
        # Use the real _voice (it catches exceptions internally) but patch compose_reply to raise.
        sleep_data = {"total_sleep_h": 7.2, "deep_h": 1.5, "rem_h": 0.9, "score": 76}
        client = MagicMock()
        client.last_night_sleep.return_value = sleep_data

        import handler as h
        msg = _make_message("how did I sleep")
        route = router.Route("health_query", {"text": "how did I sleep"})
        with patch("integrations.garmin.GarminClient", return_value=client), \
             patch("jack_voice.compose.compose_reply", side_effect=RuntimeError("boom")):
            _run(h._run_health(msg, route))

        msg.channel.send.assert_called_once()
        sent = msg.channel.send.call_args[0][0]
        # Should have fallen back to the plain formatted string.
        self.assertIsInstance(sent, str)
        self.assertTrue(len(sent) > 0)


class TestHealthStatsPersonality(unittest.TestCase):
    """_run_health stats path — successful data routes through compose."""

    def test_stats_routes_through_compose(self):
        """Stats success path calls _voice with intent health_stats."""
        spy = _VoiceSpy()
        stats_data = {"steps": 8432, "body_battery_high": 82, "calories": 450, "stress_avg": 28}
        client = MagicMock()
        client.get_stats.return_value = stats_data

        import handler as h
        msg = _make_message("my steps today")
        route = router.Route("health_query", {"text": "my steps today"})
        with patch("integrations.garmin.GarminClient", return_value=client), \
             patch("handler._voice", side_effect=spy):
            _run(h._run_health(msg, route))

        self.assertTrue(spy.called)
        self.assertEqual(spy.last_call["intent"], "health_stats")
        msg.channel.send.assert_called_once_with(_VoiceSpy.REPLY)

    def test_stats_passes_raw_stats_dict_in_data(self):
        """data['stats'] matches the raw dict from GarminClient.get_stats."""
        spy = _VoiceSpy()
        stats_data = {"steps": 191, "body_battery_high": 75, "calories": 894}
        client = MagicMock()
        client.get_stats.return_value = stats_data

        import handler as h
        msg = _make_message("steps")
        route = router.Route("health_query", {"text": "steps"})
        with patch("integrations.garmin.GarminClient", return_value=client), \
             patch("handler._voice", side_effect=spy):
            _run(h._run_health(msg, route))

        self.assertEqual(spy.last_call["data"]["stats"], stats_data)

    def test_stats_none_bypasses_compose(self):
        """When get_stats returns None, _voice is NOT called."""
        spy = _VoiceSpy()
        client = MagicMock()
        client.get_stats.return_value = None

        import handler as h
        msg = _make_message("steps")
        route = router.Route("health_query", {"text": "steps"})
        with patch("integrations.garmin.GarminClient", return_value=client), \
             patch("handler._voice", side_effect=spy):
            _run(h._run_health(msg, route))

        self.assertFalse(spy.called)
        sent = msg.channel.send.call_args[0][0]
        self.assertIn("can't reach", sent.lower())

    def test_stats_empty_lines_bypasses_compose(self):
        """When format_stats returns [] (no data), _voice is NOT called."""
        spy = _VoiceSpy()
        client = MagicMock()
        # A stats dict with no recognized fields → format_stats returns [].
        client.get_stats.return_value = {}

        import handler as h
        msg = _make_message("steps")
        route = router.Route("health_query", {"text": "steps"})
        with patch("integrations.garmin.GarminClient", return_value=client), \
             patch("handler._voice", side_effect=spy):
            _run(h._run_health(msg, route))

        self.assertFalse(spy.called)
        sent = msg.channel.send.call_args[0][0]
        self.assertIn("No activity data", sent)


class TestHealthFullPersonality(unittest.TestCase):
    """_run_health full path — successful data routes through compose."""

    def test_full_routes_through_compose(self):
        """Full / general health query calls _voice with intent health_full."""
        spy = _VoiceSpy()
        summary = {
            "sleep": {"total_sleep_h": 7.5, "deep_h": 2.0, "rem_h": 1.5, "score": 78},
            "stats": {"steps": 9000, "body_battery_high": 85},
        }
        client = MagicMock()
        client.get_daily_summary.return_value = summary

        import handler as h
        msg = _make_message("my garmin")
        route = router.Route("health_query", {"text": "my garmin"})
        with patch("integrations.garmin.GarminClient", return_value=client), \
             patch("handler._voice", side_effect=spy):
            _run(h._run_health(msg, route))

        self.assertTrue(spy.called)
        self.assertEqual(spy.last_call["intent"], "health_full")
        msg.channel.send.assert_called_once_with(_VoiceSpy.REPLY)

    def test_full_passes_summary_dict_in_data(self):
        """data['summary'] is the raw summary dict."""
        spy = _VoiceSpy()
        summary = {
            "sleep": {"total_sleep_h": 6.5, "deep_h": 1.2, "rem_h": 1.0, "score": 65},
            "stats": {"steps": 5000},
        }
        client = MagicMock()
        client.get_daily_summary.return_value = summary

        import handler as h
        msg = _make_message("garmin data")
        route = router.Route("health_query", {"text": "garmin data"})
        with patch("integrations.garmin.GarminClient", return_value=client), \
             patch("handler._voice", side_effect=spy):
            _run(h._run_health(msg, route))

        self.assertIn("summary", spy.last_call["data"])

    def test_full_summary_none_bypasses_compose(self):
        """When get_daily_summary returns None, _voice is NOT called."""
        spy = _VoiceSpy()
        client = MagicMock()
        client.get_daily_summary.return_value = None

        import handler as h
        msg = _make_message("my garmin")
        route = router.Route("health_query", {"text": "my garmin"})
        with patch("integrations.garmin.GarminClient", return_value=client), \
             patch("handler._voice", side_effect=spy):
            _run(h._run_health(msg, route))

        self.assertFalse(spy.called)


# ===========================================================================
# GOAL handler
# ===========================================================================

class TestGoalCreatePersonality(unittest.TestCase):
    """_run_goal create action — routes through compose on success."""

    def _run_goal_create(self, spy, goal=None):
        if goal is None:
            goal = _make_goal()

        import handler as h
        msg = _make_message("my goal is to run a marathon in 10 weeks")
        route = router.Route("goal", {"action": "create", "text": msg.content})

        parsed_fields = {
            "title": goal.title,
            "type": "fitness",
            "target": "42km race",
            "plan": "Train 4 days/week",
            "metric": "manual",
            "deadline": goal.deadline,
        }

        with patch("jack_goals.intents.parse_create_goal", return_value=parsed_fields), \
             patch("handler._goal_store") as mock_store_fn, \
             patch("lib.llm.complete", return_value="parsed"), \
             patch("handler._voice", side_effect=spy):
            mock_store = MagicMock()
            mock_store.create_goal.return_value = goal
            mock_store_fn.return_value = mock_store
            _run(h._run_goal(msg, route))

        return msg

    def test_goal_create_routes_through_compose(self):
        """Goal create success calls _voice with intent 'goal_create'."""
        spy = _VoiceSpy()
        msg = self._run_goal_create(spy)

        self.assertTrue(spy.called)
        self.assertEqual(spy.last_call["intent"], "goal_create")
        msg.channel.send.assert_called_once_with(_VoiceSpy.REPLY)

    def test_goal_create_passes_goal_dict(self):
        """data['goal'] in the _voice call contains title, target, plan."""
        spy = _VoiceSpy()
        goal = _make_goal(title="Read 12 books this year")
        self._run_goal_create(spy, goal=goal)

        data = spy.last_call["data"]
        self.assertIn("goal", data)
        goal_dict = data["goal"]
        self.assertIn("title", goal_dict)
        self.assertIn("Read 12 books", goal_dict["title"])

    def test_goal_create_fallback_contains_confirmation(self):
        """fallback_plain is the confirm_create_message() output string."""
        spy = _VoiceSpy()
        self._run_goal_create(spy)

        fallback = spy.last_call["fallback_plain"]
        self.assertIsInstance(fallback, str)
        self.assertTrue(len(fallback) > 0)


class TestGoalQueryPersonality(unittest.TestCase):
    """_run_goal query action — routes through compose when goals exist."""

    def _run_goal_query(self, spy, goals):
        import handler as h
        msg = _make_message("what are my goals")
        route = router.Route("goal", {"action": "query", "text": msg.content})

        with patch("jack_goals.intents.parse_query_goal", return_value={}), \
             patch("handler._goal_store") as mock_store_fn, \
             patch("handler._garmin_progress_line", return_value=""), \
             patch("handler._voice", side_effect=spy):
            mock_store = MagicMock()
            mock_store.list_active_goals.return_value = goals
            mock_store_fn.return_value = mock_store
            _run(h._run_goal(msg, route))

        return msg

    def test_goal_query_routes_through_compose(self):
        """Goal query with goals calls _voice with intent 'goal_query'."""
        spy = _VoiceSpy()
        goals = [_make_goal("Run a marathon"), _make_goal("Save 5 lakh", metric="manual")]
        msg = self._run_goal_query(spy, goals)

        self.assertTrue(spy.called)
        self.assertEqual(spy.last_call["intent"], "goal_query")
        msg.channel.send.assert_called_once_with(_VoiceSpy.REPLY)

    def test_goal_query_passes_goal_titles(self):
        """data['goals'] list contains dicts with 'title' keys matching goal titles."""
        spy = _VoiceSpy()
        goals = [_make_goal("Run a marathon"), _make_goal("Read 12 books")]
        self._run_goal_query(spy, goals)

        data = spy.last_call["data"]
        self.assertIn("goals", data)
        titles = [g["title"] for g in data["goals"]]
        self.assertIn("Run a marathon", titles)
        self.assertIn("Read 12 books", titles)

    def test_goal_query_passes_count(self):
        """data['count'] matches the number of active goals."""
        spy = _VoiceSpy()
        goals = [_make_goal("Goal A"), _make_goal("Goal B"), _make_goal("Goal C")]
        self._run_goal_query(spy, goals)

        self.assertEqual(spy.last_call["data"]["count"], 3)

    def test_goal_query_no_goals_bypasses_compose(self):
        """When no active goals, _voice is NOT called."""
        spy = _VoiceSpy()
        import handler as h
        msg = _make_message("what are my goals")
        route = router.Route("goal", {"action": "query", "text": msg.content})

        with patch("jack_goals.intents.parse_query_goal", return_value={}), \
             patch("handler._goal_store") as mock_store_fn, \
             patch("handler._voice", side_effect=spy):
            mock_store = MagicMock()
            mock_store.list_active_goals.return_value = []
            mock_store_fn.return_value = mock_store
            _run(h._run_goal(msg, route))

        self.assertFalse(spy.called)
        sent = msg.channel.send.call_args[0][0]
        self.assertIn("no active goals", sent.lower())


class TestGoalUpdateStatusPersonality(unittest.TestCase):
    """_run_goal update action (status change) — routes through compose."""

    def _run_goal_update_status(self, spy, new_status="done", goal=None):
        if goal is None:
            goal = _make_goal("Run a marathon")

        import handler as h
        msg = _make_message("mark the marathon goal done")
        route = router.Route("goal", {"action": "update", "text": msg.content})

        upd = {"goal_hint": "marathon", "updates": {"status": new_status}}

        with patch("jack_goals.intents.parse_update_goal", return_value=upd), \
             patch("handler._goal_store") as mock_store_fn, \
             patch("lib.llm.complete", return_value="parsed"), \
             patch("handler._match_goal", return_value=goal), \
             patch("handler._voice", side_effect=spy):
            mock_store = MagicMock()
            mock_store.list_active_goals.return_value = [goal]
            mock_store.set_status.return_value = None
            mock_store_fn.return_value = mock_store
            _run(h._run_goal(msg, route))

        return msg

    def test_goal_update_status_routes_through_compose(self):
        """Goal status update calls _voice with intent 'goal_update_status'."""
        spy = _VoiceSpy()
        msg = self._run_goal_update_status(spy, new_status="done")

        self.assertTrue(spy.called)
        self.assertEqual(spy.last_call["intent"], "goal_update_status")
        msg.channel.send.assert_called_once_with(_VoiceSpy.REPLY)

    def test_goal_update_status_passes_goal_title_and_new_status(self):
        """data contains 'goal_title' and 'new_status' from the update."""
        spy = _VoiceSpy()
        self._run_goal_update_status(spy, new_status="paused")

        data = spy.last_call["data"]
        self.assertIn("goal_title", data)
        self.assertIn("new_status", data)
        self.assertEqual(data["new_status"], "paused")

    def test_goal_update_status_done_contains_verb(self):
        """data['verb'] reflects the done/paused/active mapping."""
        spy = _VoiceSpy()
        self._run_goal_update_status(spy, new_status="done")

        self.assertEqual(spy.last_call["data"]["verb"], "marked complete")

    def test_goal_update_paused_verb(self):
        spy = _VoiceSpy()
        self._run_goal_update_status(spy, new_status="paused")
        self.assertEqual(spy.last_call["data"]["verb"], "paused")


class TestGoalUpdateProgressPersonality(unittest.TestCase):
    """_run_goal update action (progress note) — routes through compose."""

    def _run_goal_update_progress(self, spy, goal=None, note="ran 5km today"):
        if goal is None:
            goal = _make_goal("Run a marathon")

        import handler as h
        msg = _make_message("logged: ran 5km today")
        route = router.Route("goal", {"action": "update", "text": msg.content})

        upd = {"goal_hint": "marathon", "updates": {"progress_note": note}}

        with patch("jack_goals.intents.parse_update_goal", return_value=upd), \
             patch("handler._goal_store") as mock_store_fn, \
             patch("lib.llm.complete", return_value="parsed"), \
             patch("handler._match_goal", return_value=goal), \
             patch("handler._voice", side_effect=spy):
            mock_store = MagicMock()
            mock_store.list_active_goals.return_value = [goal]
            mock_store.append_progress.return_value = None
            mock_store_fn.return_value = mock_store
            _run(h._run_goal(msg, route))

        return msg

    def test_goal_update_progress_routes_through_compose(self):
        """Progress note path calls _voice with intent 'goal_update_progress'."""
        spy = _VoiceSpy()
        msg = self._run_goal_update_progress(spy)

        self.assertTrue(spy.called)
        self.assertEqual(spy.last_call["intent"], "goal_update_progress")
        msg.channel.send.assert_called_once_with(_VoiceSpy.REPLY)

    def test_goal_update_progress_passes_goal_title(self):
        """data['goal_title'] matches the matched goal's title."""
        spy = _VoiceSpy()
        self._run_goal_update_progress(spy)

        self.assertEqual(spy.last_call["data"]["goal_title"], "Run a marathon")

    def test_goal_update_progress_passes_note(self):
        """data['note'] is the progress_note from the parsed update."""
        spy = _VoiceSpy()
        self._run_goal_update_progress(spy, note="cycled 20km")

        self.assertEqual(spy.last_call["data"]["note"], "cycled 20km")

    def test_goal_update_progress_fallback_contains_title(self):
        """fallback_plain mentions the goal title."""
        spy = _VoiceSpy()
        self._run_goal_update_progress(spy)

        fallback = spy.last_call["fallback_plain"]
        self.assertIn("Run a marathon", fallback)


class TestGoalErrorPathsBypassCompose(unittest.TestCase):
    """Disambiguation and error paths must NOT call _voice."""

    def test_goal_no_active_goals_update_bypasses_compose(self):
        """No active goals on update — sends direct message, no compose."""
        spy = _VoiceSpy()
        import handler as h
        msg = _make_message("mark the goal done")
        route = router.Route("goal", {"action": "update", "text": msg.content})

        upd = {"goal_hint": "marathon", "updates": {"status": "done"}}

        with patch("jack_goals.intents.parse_update_goal", return_value=upd), \
             patch("handler._goal_store") as mock_store_fn, \
             patch("lib.llm.complete", return_value="parsed"), \
             patch("handler._voice", side_effect=spy):
            mock_store = MagicMock()
            mock_store.list_active_goals.return_value = []
            mock_store_fn.return_value = mock_store
            _run(h._run_goal(msg, route))

        self.assertFalse(spy.called)

    def test_goal_disambiguation_bypasses_compose(self):
        """When _match_goal returns None (ambiguous), compose is NOT called."""
        spy = _VoiceSpy()
        import handler as h
        msg = _make_message("mark it done")
        route = router.Route("goal", {"action": "update", "text": msg.content})

        upd = {"goal_hint": "", "updates": {"status": "done"}}
        goals = [_make_goal("Goal A"), _make_goal("Goal B")]

        with patch("jack_goals.intents.parse_update_goal", return_value=upd), \
             patch("handler._goal_store") as mock_store_fn, \
             patch("lib.llm.complete", return_value="parsed"), \
             patch("handler._match_goal", return_value=None), \
             patch("handler._voice", side_effect=spy):
            mock_store = MagicMock()
            mock_store.list_active_goals.return_value = goals
            mock_store_fn.return_value = mock_store
            _run(h._run_goal(msg, route))

        self.assertFalse(spy.called)
        sent = msg.channel.send.call_args[0][0]
        self.assertIn("Which goal", sent)


if __name__ == "__main__":
    unittest.main()
