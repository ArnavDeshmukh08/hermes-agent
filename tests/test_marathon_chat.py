"""Tests for the marathon training check-in intent — no real network calls, no LLM.

All handler tests set HERMES_LLM_MOCK=1 so _voice() returns fallback_plain,
keeping assertions deterministic and independent of the personality layer.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure both the router directory and the project root are on the path.
_ROOT = Path(__file__).resolve().parents[1]
_ROUTER_DIR = _ROOT / "hooks" / "jack_router"
for _p in (str(_ROOT), str(_ROUTER_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import jack_intent_router as router  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_marathon_goal():
    """Return a minimal Goal-like object for mocking."""
    g = MagicMock()
    g.title = "Run a marathon"
    g.metric = "garmin_runs"
    g.deadline = "2026-08-02"
    g.progress_notes = ()
    return g


# ---------------------------------------------------------------------------
# 1. Intent classification — marathon phrases → marathon_training
# ---------------------------------------------------------------------------

class TestMarathonIntentClassification(unittest.TestCase):

    def test_hows_my_marathon_training(self):
        r = router.classify("how's my marathon training")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "marathon_training", repr(r))

    def test_hows_my_marathon_no_apostrophe(self):
        r = router.classify("hows my marathon training")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "marathon_training", repr(r))

    def test_am_i_on_track_for_the_marathon(self):
        r = router.classify("am I on track for the marathon")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "marathon_training", repr(r))

    def test_am_i_on_track_for_marathon_no_article(self):
        r = router.classify("am I on track for marathon")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "marathon_training", repr(r))

    def test_marathon_progress(self):
        r = router.classify("marathon progress")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "marathon_training", repr(r))

    def test_training_check_in(self):
        r = router.classify("training check-in")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "marathon_training", repr(r))

    def test_training_checkin_no_hyphen(self):
        r = router.classify("training checkin")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "marathon_training", repr(r))

    def test_marathon_checkin(self):
        r = router.classify("marathon check-in")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "marathon_training", repr(r))

    def test_marathon_status(self):
        r = router.classify("marathon status")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "marathon_training", repr(r))

    def test_how_is_my_training_for_the_marathon(self):
        r = router.classify("how is my training for the marathon")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "marathon_training", repr(r))

    def test_am_i_ready_for_the_marathon(self):
        r = router.classify("am I ready for the marathon")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "marathon_training", repr(r))

    def test_marathon_update(self):
        r = router.classify("marathon update")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "marathon_training", repr(r))


# ---------------------------------------------------------------------------
# 2. No false positives on generic goal queries
# ---------------------------------------------------------------------------

class TestNoStealGoalQuery(unittest.TestCase):

    def test_how_am_i_tracking_is_goal(self):
        r = router.classify("how am I tracking")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "goal", f"Expected goal, got {r.intent!r}")

    def test_what_are_my_goals_is_goal(self):
        r = router.classify("what are my goals")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "goal", f"Expected goal, got {r.intent!r}")

    def test_my_goals_is_goal(self):
        r = router.classify("my goals")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "goal", f"Expected goal, got {r.intent!r}")

    def test_goal_progress_is_goal(self):
        r = router.classify("goal progress")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "goal", f"Expected goal, got {r.intent!r}")


# ---------------------------------------------------------------------------
# 3. No false positives on health queries
# ---------------------------------------------------------------------------

class TestNoStealHealthQuery(unittest.TestCase):

    def test_how_did_i_sleep_is_health(self):
        r = router.classify("how did I sleep")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "health_query", f"Expected health_query, got {r.intent!r}")

    def test_my_steps_today_is_health(self):
        r = router.classify("my steps today")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "health_query", f"Expected health_query, got {r.intent!r}")

    def test_garmin_stats_is_health(self):
        r = router.classify("garmin stats")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "health_query", f"Expected health_query, got {r.intent!r}")

    def test_body_battery_is_health(self):
        r = router.classify("body battery")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "health_query", f"Expected health_query, got {r.intent!r}")


# ---------------------------------------------------------------------------
# 4. Handler returns a real data string (with fixture health data)
# ---------------------------------------------------------------------------

class TestHandlerReturnsRealDataString(unittest.TestCase):

    def setUp(self):
        os.environ["HERMES_LLM_MOCK"] = "1"

    def tearDown(self):
        os.environ.pop("HERMES_LLM_MOCK", None)

    def test_reply_contains_marathon_and_step_count(self):
        import handler as h

        mock_goal = _make_marathon_goal()

        health_fixture = {
            "fetched_at_utc": "2026-06-29T11:00:00Z",
            "date_ist": "2026-06-29",
            "is_stale": False,
            "stats": {"steps": 5000, "body_battery_high": 80, "calories": 1500, "stress_avg": 30},
            "sleep": {"total_sleep_h": 7.0, "deep_h": 1.5, "rem_h": 1.8, "score": 74},
            "runs_today": None,
            "runs_available": False,
        }

        with patch("handler._goal_store") as mock_store, \
             patch("handler._load_health_today", return_value=health_fixture):
            mock_store.return_value.list_active_goals.return_value = [mock_goal]
            route = router.Route("marathon_training", {"text": "how's my marathon training"})
            reply = h._run_marathon_training("how's my marathon training", route)

        # Must reference marathon context
        lower = reply.lower()
        has_marathon_ref = any(w in lower for w in ("marathon", "aug", "week"))
        self.assertTrue(has_marathon_ref, f"No marathon reference in: {reply!r}")

        # Must include the step count (5000 formatted as 5,000)
        self.assertIn("5,000", reply, f"Step count 5,000 not found in: {reply!r}")

    def test_reply_does_not_prescribe(self):
        import handler as h

        health_fixture = {
            "is_stale": False,
            "stats": {"steps": 1000, "body_battery_high": 60},
            "sleep": {"total_sleep_h": 6.0, "deep_h": 1.0, "rem_h": 1.0, "score": 60},
            "runs_today": None,
            "runs_available": False,
        }

        with patch("handler._goal_store") as mock_store, \
             patch("handler._load_health_today", return_value=health_fixture):
            mock_store.return_value.list_active_goals.return_value = [_make_marathon_goal()]
            route = router.Route("marathon_training", {"text": "marathon check-in"})
            reply = h._run_marathon_training("marathon check-in", route)

        lower = reply.lower()
        for forbidden in ("should run", "need to train", "increase mileage", "rest day", "interval"):
            self.assertNotIn(forbidden, lower, f"Prescriptive phrase {forbidden!r} found in: {reply!r}")


# ---------------------------------------------------------------------------
# 5. Handler is honest when no data is available
# ---------------------------------------------------------------------------

class TestHandlerHonestWhenNoData(unittest.TestCase):

    def setUp(self):
        os.environ["HERMES_LLM_MOCK"] = "1"

    def tearDown(self):
        os.environ.pop("HERMES_LLM_MOCK", None)

    def test_honest_when_health_today_missing_and_garmin_none(self):
        import handler as h

        # No health_today.json and GarminClient returns None on all calls.
        mock_garmin = MagicMock()
        mock_garmin.get_stats.return_value = None
        mock_garmin.last_night_sleep.return_value = None

        with patch("handler._goal_store") as mock_store, \
             patch("handler._load_health_today", return_value=None), \
             patch("integrations.garmin.GarminClient", return_value=mock_garmin):
            mock_store.return_value.list_active_goals.return_value = [_make_marathon_goal()]
            route = router.Route("marathon_training", {"text": "marathon check-in"})
            reply = h._run_marathon_training("marathon check-in", route)

        lower = reply.lower()
        # Must say data is unavailable honestly
        honest_words = ("no garmin data", "unavailable", "no data", "not available")
        found_honest = any(w in lower for w in honest_words)
        self.assertTrue(found_honest, f"Honest data-missing language not found in: {reply!r}")

        # Must NOT fabricate step counts or sleep numbers
        import re
        # Should not contain multi-digit isolated numbers that look like real data
        # (allow week/day counts which are small integers like "5" or "34")
        self.assertNotIn("steps today: 0", lower)
        self.assertNotIn("5,000", reply)
        self.assertNotIn("sleep:", lower)

    def test_no_fabricated_numbers_when_garmin_unavailable(self):
        import handler as h

        with patch("handler._goal_store") as mock_store, \
             patch("handler._load_health_today", return_value=None), \
             patch("integrations.garmin.GarminClient", side_effect=Exception("unreachable")):
            mock_store.return_value.list_active_goals.return_value = [_make_marathon_goal()]
            route = router.Route("marathon_training", {"text": "marathon status"})
            reply = h._run_marathon_training("marathon status", route)

        # Must not fabricate a step count (would include "steps today: <number>")
        lower = reply.lower()
        # A fabricated reply would say "steps today: 3500" — we must not see that
        self.assertNotIn("3,500", reply)
        self.assertNotIn("3500", reply)

    def test_reply_still_contains_deadline_info_when_no_health(self):
        """Even with no health data, we still know the deadline — reflect it."""
        import handler as h

        with patch("handler._goal_store") as mock_store, \
             patch("handler._load_health_today", return_value=None), \
             patch("integrations.garmin.GarminClient", side_effect=Exception("down")):
            mock_store.return_value.list_active_goals.return_value = [_make_marathon_goal()]
            route = router.Route("marathon_training", {"text": "marathon check-in"})
            reply = h._run_marathon_training("marathon check-in", route)

        # The deadline info is always available (computed from today's date)
        lower = reply.lower()
        has_deadline = "aug" in lower or "week" in lower or "marathon" in lower
        self.assertTrue(has_deadline, f"No deadline info in: {reply!r}")


# ---------------------------------------------------------------------------
# 6. No prescriptive language in fallback_plain
# ---------------------------------------------------------------------------

class TestNoPrescriptiveLanguage(unittest.TestCase):

    def setUp(self):
        os.environ["HERMES_LLM_MOCK"] = "1"

    def tearDown(self):
        os.environ.pop("HERMES_LLM_MOCK", None)

    def test_low_steps_no_prescription(self):
        """Low step count + no run data must not produce prescriptive advice."""
        import handler as h

        health_fixture = {
            "is_stale": False,
            "stats": {"steps": 1000, "body_battery_high": 50},
            "sleep": {"total_sleep_h": 5.5, "deep_h": 0.8, "rem_h": 0.9, "score": 55},
            "runs_today": None,
            "runs_available": False,
        }

        with patch("handler._goal_store") as mock_store, \
             patch("handler._load_health_today", return_value=health_fixture):
            mock_store.return_value.list_active_goals.return_value = [_make_marathon_goal()]
            route = router.Route("marathon_training", {"text": "training check-in"})
            reply = h._run_marathon_training("training check-in", route)

        lower = reply.lower()
        banned = [
            "should run",
            "need to train",
            "increase mileage",
            "rest day",
            "interval",
            "you need",
            "you must",
            "you should",
            "train harder",
            "run more",
        ]
        for phrase in banned:
            self.assertNotIn(phrase, lower, f"Prescriptive phrase {phrase!r} in: {reply!r}")

    def test_fallback_reflects_not_prescribes(self):
        """Fallback string is a reflection (data echo), not a coaching directive."""
        import handler as h

        health_fixture = {
            "is_stale": False,
            "stats": {"steps": 8000, "body_battery_high": 75},
            "sleep": {"total_sleep_h": 7.5, "deep_h": 1.8, "rem_h": 1.5, "score": 76},
            "runs_today": {"distance_km": 5.0},
            "runs_available": True,
        }

        with patch("handler._goal_store") as mock_store, \
             patch("handler._load_health_today", return_value=health_fixture):
            mock_store.return_value.list_active_goals.return_value = [_make_marathon_goal()]
            route = router.Route("marathon_training", {"text": "how is my training for the marathon"})
            reply = h._run_marathon_training("how is my training for the marathon", route)

        # Should be a factual reflection containing actual data
        self.assertIn("8,000", reply, f"Step count 8,000 not in: {reply!r}")


if __name__ == "__main__":
    unittest.main()
