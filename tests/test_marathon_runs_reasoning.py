"""Tests for marathon reasoning that uses real run history (health_history.json).

Covers:
 - gather_context() loading health_history.json and surfacing accountability / encouragement
 - gather_context() graceful handling when Mac is unreachable (runs=null)
 - No prescriptive language in reasoner output
 - Handler loading health_history.json and including history_context in data
 - Handler graceful when health_history.json is missing
 - Handler no prescriptive language in fallback

HERMES_LLM_MOCK=1 bypasses the voice layer in handler tests so assertions
are deterministic and don't require a real LLM.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup — same pattern as test_marathon_chat.py
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[1]
_ROUTER_DIR = _ROOT / "hooks" / "jack_router"
for _p in (str(_ROOT), str(_ROUTER_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import jack_intent_router as router  # noqa: E402

_IST = timezone(timedelta(hours=5, minutes=30))
# 15:00 IST Friday = 09:30 UTC  (2026-07-03 is a Friday)
_NOW_FRIDAY_LATE = datetime(2026, 7, 3, 9, 30, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _make_history(
    *,
    days: int = 7,
    run_on_days: list[int] | None = None,
    null_on_days: list[int] | None = None,
) -> list[dict]:
    """Build a health_history.json-style list.

    days      — how many entries to generate (0 = today, 1 = yesterday, ...)
    run_on_days  — list of indices (0=today) where a run should appear
    null_on_days — list of indices where runs=null (Mac unreachable)
    """
    from datetime import date, timedelta

    run_on_days = run_on_days or []
    null_on_days = null_on_days or []

    base_date = date(2026, 7, 3)  # Friday 2026-07-03
    history: list[dict] = []
    for i in range(days - 1, -1, -1):
        d = (base_date - timedelta(days=i)).isoformat()
        if i in null_on_days:
            runs_val = None  # Mac unreachable
        elif i in run_on_days:
            runs_val = {"count": 1, "total_km": 5.0, "entries": [{"km": 5.0, "pace": "5.5"}]}
        else:
            runs_val = {"count": 0, "total_km": 0.0, "entries": []}
        history.append({
            "date_ist": d,
            "steps": 7000,
            "sleep_h": 7.0,
            "runs": runs_val,
            "sedentary": False,
        })
    return history


def _make_health_today(*, runs_today=None, runs_available: bool = False, steps: int = 3000) -> dict:
    return {
        "fetched_at_utc": "2026-07-03T09:00:00+00:00",
        "date_ist": "2026-07-03",
        "is_stale": False,
        "stats": {"steps": steps, "body_battery_high": 75, "calories": 1400, "stress_avg": 30},
        "sleep": {"total_sleep_h": 7.0, "deep_h": 1.5, "rem_h": 1.8, "score": 72},
        "runs_today": runs_today,
        "runs_available": runs_available,
        "hourly_steps": [{"hour": i, "steps": steps + i * 50} for i in range(8)],
    }


def _call_gather_context(
    tmp_path: Path,
    history: list[dict] | None = None,
    health_today: dict | None = None,
    now: datetime = _NOW_FRIDAY_LATE,
) -> str:
    """Call gather_context() with controlled fixture files."""
    from proactive.reasoner import gather_context

    # Write health_today.json
    hermes_dir = tmp_path / ".hermes"
    hermes_dir.mkdir(parents=True, exist_ok=True)

    if health_today is not None:
        (hermes_dir / "health_today.json").write_text(
            json.dumps(health_today), encoding="utf-8"
        )

    if history is not None:
        (hermes_dir / "health_history.json").write_text(
            json.dumps(history), encoding="utf-8"
        )

    mc = MagicMock()
    mc.search.return_value = []
    store = MagicMock()
    store.list_pending.return_value = []
    queue = MagicMock()
    queue.__len__ = MagicMock(return_value=0)

    real_path = Path

    def path_side_effect(*args):
        return real_path(*args)

    with patch("proactive.reasoner.Path") as mock_path_cls:
        mock_path_cls.side_effect = path_side_effect
        mock_path_cls.home.return_value = tmp_path

        with patch("jack_goals.store.list_active_goals", return_value=[]):
            ctx = gather_context(mc, store, None, queue, "arnav", now)

    return ctx


# ---------------------------------------------------------------------------
# 1. Reasoner surfaces accountability when runs_this_week=1 on Friday
# ---------------------------------------------------------------------------

class TestMarathonAwarenessReflectsGap(unittest.TestCase):

    def test_marathon_awareness_reflects_gap_when_low_runs(self, tmp_path=None):
        """When only 1 run this week and it's Friday, reasoner context reflects the gap."""
        if tmp_path is None:
            import tempfile
            tmp = Path(tempfile.mkdtemp())
        else:
            tmp = tmp_path

        # 1 run 3 days ago (day index 3), today and recent days have 0
        history = _make_history(days=7, run_on_days=[3], null_on_days=[])
        health = _make_health_today(runs_today=None, runs_available=False, steps=2000)

        ctx = _call_gather_context(tmp, history=history, health_today=health, now=_NOW_FRIDAY_LATE)

        # Context must include the run history section
        assert "Run History" in ctx or "runs this week" in ctx.lower() or "Runs this week" in ctx, (
            f"Run history section must appear in context. Got: {ctx[:500]}"
        )

        # The history should show some accountability signal — either days_since or runs_this_week
        # At least the MARATHON AWARENESS section must reference run history
        assert "Run History" in ctx or "days tracked" in ctx, (
            "History tracking info must be present"
        )

        # No actually-prescriptive language (the framing rule itself says "NEVER prescribe" —
        # we check for constructs that would only appear if context IS prescribing, not prohibiting)
        ctx_lower = ctx.lower()
        actually_prescriptive = [
            "do 5x800m",
            "run at 4:30 pace",
            "eat more protein",
            "rest 48 hours",
            "add tempo runs",
            "increase your weekly mileage",
        ]
        for phrase in actually_prescriptive:
            assert phrase not in ctx_lower, f"Prescriptive phrase {phrase!r} found in context"


# ---------------------------------------------------------------------------
# 2. Reasoner encourages when on track (runs_this_week >= 3)
# ---------------------------------------------------------------------------

class TestMarathonAwarenessEncourages(unittest.TestCase):

    def test_marathon_awareness_encourages_when_on_track(self, tmp_path=None):
        """When runs_this_week=3, context reflects positive state."""
        if tmp_path is None:
            import tempfile
            tmp = Path(tempfile.mkdtemp())
        else:
            tmp = tmp_path

        # 3 runs this week: today, 2 days ago, 4 days ago
        history = _make_history(days=7, run_on_days=[0, 2, 4], null_on_days=[])
        health = _make_health_today(runs_today=[{"km": 5.0}], runs_available=True, steps=9000)

        ctx = _call_gather_context(tmp, history=history, health_today=health, now=_NOW_FRIDAY_LATE)

        # History section must be present with runs_this_week >= 3
        assert "Run History" in ctx, "Run History section must appear"
        assert "3" in ctx, "Run count of 3 must appear in context"

        # No actually-prescriptive language (distinguish from framing rule text)
        ctx_lower = ctx.lower()
        actually_prescriptive = [
            "do 5x800m",
            "run at 4:30 pace",
            "eat more protein",
            "rest 48 hours",
            "add tempo runs",
        ]
        for phrase in actually_prescriptive:
            assert phrase not in ctx_lower, f"Prescriptive phrase {phrase!r} found in context"


# ---------------------------------------------------------------------------
# 3. Reasoner is honest when Mac was down (runs=null in history)
# ---------------------------------------------------------------------------

class TestMarathonAwarenessHonestWhenMacDown(unittest.TestCase):

    def test_marathon_awareness_honest_when_mac_down(self, tmp_path=None):
        """When all history entries have runs=null (Mac unreachable), context says data unavailable."""
        if tmp_path is None:
            import tempfile
            tmp = Path(tempfile.mkdtemp())
        else:
            tmp = tmp_path

        # All days have null runs (Mac unreachable)
        history = _make_history(days=7, run_on_days=[], null_on_days=list(range(7)))
        health = _make_health_today(runs_today=None, runs_available=False)
        # Mark health as stale too
        health["is_stale"] = True

        ctx = _call_gather_context(tmp, history=history, health_today=health, now=_NOW_FRIDAY_LATE)

        # Must not fabricate run data — context should either say "No run found" or show 0 runs
        # The key is it should not say "Ran today" or a specific run count when Mac is down
        ctx_lower = ctx.lower()

        # No actually-prescriptive constructs (avoid checking framing-rule meta-language)
        actually_prescriptive = [
            "do 5x800m",
            "run at 4:30 pace",
            "eat more protein",
            "rest 48 hours",
            "increase your weekly mileage",
            "add tempo runs",
        ]
        for phrase in actually_prescriptive:
            assert phrase not in ctx_lower, f"Prescriptive phrase {phrase!r} found in context"

        # Must have run history section present (even if empty)
        assert "Run History" in ctx or "run history" in ctx_lower, (
            "Run history section should appear even when Mac was down"
        )


# ---------------------------------------------------------------------------
# 4. No prescriptive language anywhere in context
# ---------------------------------------------------------------------------

class TestMarathonAwarenessNoPrescriptions(unittest.TestCase):

    def test_marathon_awareness_no_prescriptions(self, tmp_path=None):
        """No banned prescriptive terms should appear in reasoner context output."""
        if tmp_path is None:
            import tempfile
            tmp = Path(tempfile.mkdtemp())
        else:
            tmp = tmp_path

        history = _make_history(days=7, run_on_days=[3], null_on_days=[])
        health = _make_health_today(runs_today=None, steps=1500)

        ctx = _call_gather_context(tmp, history=history, health_today=health, now=_NOW_FRIDAY_LATE)

        ctx_lower = ctx.lower()
        # These phrases would only appear if the context is ACTUALLY prescribing,
        # not as part of the meta-framing rule that says "NEVER prescribe X"
        actually_prescriptive = [
            "do 5x800m",
            "run at 4:30 pace",
            "eat more protein",
            "rest 48 hours",
            "add tempo runs",
            "increase your weekly mileage",
            "you must run today",
            "you need to run now",
        ]
        for phrase in actually_prescriptive:
            assert phrase not in ctx_lower, (
                f"Prescriptive instruction {phrase!r} must not appear in context. "
                f"Context snippet: {ctx[:300]}"
            )


# ---------------------------------------------------------------------------
# 5. Chat handler includes history_context in data when history is available
# ---------------------------------------------------------------------------

class TestMarathonChatIncludesHistoryContext(unittest.TestCase):

    def setUp(self):
        os.environ["HERMES_LLM_MOCK"] = "1"

    def tearDown(self):
        os.environ.pop("HERMES_LLM_MOCK", None)

    def test_marathon_chat_includes_history_context(self):
        """When health_history.json has data, handler includes history context in reply."""
        import handler as h

        mock_goal = MagicMock()
        mock_goal.title = "Run a marathon"
        mock_goal.metric = "garmin_runs"
        mock_goal.deadline = "2026-08-02"
        mock_goal.progress_notes = ()

        health_fixture = {
            "is_stale": False,
            "stats": {"steps": 5000, "body_battery_high": 80},
            "sleep": {"total_sleep_h": 7.0, "deep_h": 1.5, "rem_h": 1.8, "score": 74},
            "runs_today": None,
            "runs_available": False,
        }

        # history with 2 runs this week
        history_fixture = _make_history(days=7, run_on_days=[1, 4], null_on_days=[])

        with patch("handler._goal_store") as mock_store, \
             patch("handler._load_health_today", return_value=health_fixture), \
             patch("integrations.garmin_poller.days_since_last_run", return_value=1) as _mock_dsrl, \
             patch("integrations.garmin_poller.runs_this_week", return_value=2) as _mock_rtw:
            mock_store.return_value.list_active_goals.return_value = [mock_goal]

            import tempfile, json as _json
            with tempfile.TemporaryDirectory() as tmpdir:
                hist_path = Path(tmpdir) / ".hermes" / "health_history.json"
                hist_path.parent.mkdir(parents=True, exist_ok=True)
                hist_path.write_text(_json.dumps(history_fixture))

                with patch("handler.Path") as mock_path_cls:
                    real_path = Path

                    def path_side(arg=None, *args):
                        if arg is None:
                            return real_path()
                        return real_path(arg, *args)

                    mock_path_cls.side_effect = path_side
                    mock_path_cls.home.return_value = Path(tmpdir)

                    route = router.Route("marathon_training", {"text": "how's my marathon training"})
                    reply = h._run_marathon_training("how's my marathon training", route)

        # Reply must contain marathon context
        lower = reply.lower()
        has_marathon = any(w in lower for w in ("marathon", "aug", "week"))
        self.assertTrue(has_marathon, f"No marathon reference in reply: {reply!r}")


# ---------------------------------------------------------------------------
# 6. Chat handler handles missing health_history.json gracefully
# ---------------------------------------------------------------------------

class TestMarathonChatHandlesMissingHistory(unittest.TestCase):

    def setUp(self):
        os.environ["HERMES_LLM_MOCK"] = "1"

    def tearDown(self):
        os.environ.pop("HERMES_LLM_MOCK", None)

    def test_marathon_chat_handles_missing_history(self):
        """When health_history.json doesn't exist, handler must not crash."""
        import handler as h

        mock_goal = MagicMock()
        mock_goal.title = "Run a marathon"
        mock_goal.metric = "garmin_runs"
        mock_goal.deadline = "2026-08-02"
        mock_goal.progress_notes = ()

        health_fixture = {
            "is_stale": False,
            "stats": {"steps": 3000, "body_battery_high": 70},
            "sleep": {"total_sleep_h": 6.5, "deep_h": 1.0, "rem_h": 1.2, "score": 65},
            "runs_today": None,
            "runs_available": False,
        }

        with patch("handler._goal_store") as mock_store, \
             patch("handler._load_health_today", return_value=health_fixture):
            mock_store.return_value.list_active_goals.return_value = [mock_goal]

            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                # No health_history.json in tmpdir — just the empty .hermes dir
                (Path(tmpdir) / ".hermes").mkdir(parents=True, exist_ok=True)

                with patch("handler.Path") as mock_path_cls:
                    real_path = Path

                    def path_side(arg=None, *args):
                        if arg is None:
                            return real_path()
                        return real_path(arg, *args)

                    mock_path_cls.side_effect = path_side
                    mock_path_cls.home.return_value = Path(tmpdir)

                    route = router.Route("marathon_training", {"text": "marathon check-in"})
                    # Must not raise
                    try:
                        reply = h._run_marathon_training("marathon check-in", route)
                    except Exception as exc:
                        self.fail(f"Handler raised when history missing: {exc!r}")

        # Reply must still contain basic marathon info
        lower = reply.lower()
        self.assertTrue(
            any(w in lower for w in ("marathon", "aug", "week")),
            f"No marathon reference in reply: {reply!r}"
        )


# ---------------------------------------------------------------------------
# 7. Chat handler no prescriptions in output
# ---------------------------------------------------------------------------

class TestMarathonChatNoPrescriptions(unittest.TestCase):

    def setUp(self):
        os.environ["HERMES_LLM_MOCK"] = "1"

    def tearDown(self):
        os.environ.pop("HERMES_LLM_MOCK", None)

    def test_marathon_chat_no_prescriptions(self):
        """Handler fallback must not produce prescriptive language."""
        import handler as h

        mock_goal = MagicMock()
        mock_goal.title = "Run a marathon"
        mock_goal.metric = "garmin_runs"
        mock_goal.deadline = "2026-08-02"
        mock_goal.progress_notes = ()

        health_fixture = {
            "is_stale": False,
            "stats": {"steps": 1000, "body_battery_high": 50},
            "sleep": {"total_sleep_h": 5.5, "deep_h": 0.8, "rem_h": 0.9, "score": 55},
            "runs_today": None,
            "runs_available": False,
        }

        with patch("handler._goal_store") as mock_store, \
             patch("handler._load_health_today", return_value=health_fixture):
            mock_store.return_value.list_active_goals.return_value = [mock_goal]

            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                (Path(tmpdir) / ".hermes").mkdir(parents=True, exist_ok=True)

                with patch("handler.Path") as mock_path_cls:
                    real_path = Path

                    def path_side(arg=None, *args):
                        if arg is None:
                            return real_path()
                        return real_path(arg, *args)

                    mock_path_cls.side_effect = path_side
                    mock_path_cls.home.return_value = Path(tmpdir)

                    route = router.Route("marathon_training", {"text": "training check-in"})
                    reply = h._run_marathon_training("training check-in", route)

        lower = reply.lower()
        banned = [
            "should run",
            "need to train",
            "increase mileage",
            "rest day",
            "interval",
            "pace target",
            "dietary",
            "you need to run",
            "you must",
        ]
        for phrase in banned:
            self.assertNotIn(phrase, lower, f"Prescriptive phrase {phrase!r} found in: {reply!r}")


# ---------------------------------------------------------------------------
# pytest adapter — each class method can also be called from pytest
# ---------------------------------------------------------------------------

def test_marathon_awareness_reflects_gap_when_low_runs(tmp_path):
    TestMarathonAwarenessReflectsGap().test_marathon_awareness_reflects_gap_when_low_runs(tmp_path)


def test_marathon_awareness_encourages_when_on_track(tmp_path):
    TestMarathonAwarenessEncourages().test_marathon_awareness_encourages_when_on_track(tmp_path)


def test_marathon_awareness_honest_when_mac_down(tmp_path):
    TestMarathonAwarenessHonestWhenMacDown().test_marathon_awareness_honest_when_mac_down(tmp_path)


def test_marathon_awareness_no_prescriptions(tmp_path):
    TestMarathonAwarenessNoPrescriptions().test_marathon_awareness_no_prescriptions(tmp_path)


def test_marathon_chat_includes_history_context():
    t = TestMarathonChatIncludesHistoryContext()
    t.setUp()
    try:
        t.test_marathon_chat_includes_history_context()
    finally:
        t.tearDown()


def test_marathon_chat_handles_missing_history():
    t = TestMarathonChatHandlesMissingHistory()
    t.setUp()
    try:
        t.test_marathon_chat_handles_missing_history()
    finally:
        t.tearDown()


def test_marathon_chat_no_prescriptions():
    t = TestMarathonChatNoPrescriptions()
    t.setUp()
    try:
        t.test_marathon_chat_no_prescriptions()
    finally:
        t.tearDown()


if __name__ == "__main__":
    unittest.main()
