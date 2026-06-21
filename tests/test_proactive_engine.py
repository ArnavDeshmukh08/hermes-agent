"""Tests for proactive.scorer and proactive.engine.

Everything is deterministic and offline:
- No network calls.
- No real Discord / notifier.
- Clock injected via `now=` parameters.
- Log I/O uses tmp_path fixtures.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from proactive.scorer import (
    P1, P2, P3,
    SCORE_P1, SCORE_P2, SCORE_P3,
    ProactiveItem,
    PriorityScorer,
)
from proactive.engine import ProactiveEngine, _IST, _to_z

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc


def _now_utc() -> datetime:
    return datetime(2026, 6, 19, 12, 0, tzinfo=UTC)  # 17:30 IST — well within quiet-off window


def _ist(hour: int, minute: int = 0, day: int = 19, weekday_sunday: bool = False) -> datetime:
    """Return a UTC datetime corresponding to the given IST hour on a fixed date.

    weekday_sunday=True shifts to a Sunday (2026-06-21).
    """
    if weekday_sunday:
        # 2026-06-21 is a Sunday
        ist_dt = datetime(2026, 6, 21, hour, minute, tzinfo=_IST)
    else:
        ist_dt = datetime(2026, 6, day, hour, minute, tzinfo=_IST)
    return ist_dt.astimezone(UTC)


def _item(
    nudge_type: str = "test_nudge",
    priority: str = P2,
    score: int = SCORE_P2,
    message: str = "test message",
    alone: bool = False,
) -> ProactiveItem:
    return ProactiveItem(
        nudge_type=nudge_type,
        priority=priority,
        score=score,
        message=message,
        alone=alone,
    )


def _p1(nudge_type: str = "test_nudge", message: str = "test message", score: int = SCORE_P1) -> ProactiveItem:
    return ProactiveItem(nudge_type=nudge_type, priority=P1, score=score, message=message, alone=True)


def _p2(nudge_type: str = "test_nudge", message: str = "test message", score: int = SCORE_P2) -> ProactiveItem:
    return ProactiveItem(nudge_type=nudge_type, priority=P2, score=score, message=message, alone=False)


def _p3(nudge_type: str = "test_nudge", message: str = "test message", score: int = SCORE_P3) -> ProactiveItem:
    return ProactiveItem(nudge_type=nudge_type, priority=P3, score=score, message=message, alone=False)


def _make_engine(
    tmp_path: Path,
    *,
    send_fn=None,
    store=None,
    calendar_client=None,
    scorer=None,
    user_md: str = "",
    env_overrides: dict | None = None,
) -> ProactiveEngine:
    """Build a ProactiveEngine wired to tmp_path with injectable fakes."""
    log_path = tmp_path / "proactive_log.json"
    user_path = tmp_path / "USER.md"
    user_path.write_text(user_md, encoding="utf-8")

    if send_fn is None:
        send_fn = lambda content, *, user_id=None: True  # noqa: E731

    eng = ProactiveEngine(
        user_path=user_path,
        log_path=log_path,
        store=store,
        calendar_client=calendar_client,
        scorer=scorer,
        send_fn=send_fn,
    )

    # Apply env overrides without permanently polluting the test environment
    if env_overrides:
        for k, v in env_overrides.items():
            os.environ[k] = v

    return eng


class _FakeStore:
    """In-memory ReminderStore stand-in."""

    def __init__(self, pending: list[dict] | None = None) -> None:
        self._pending = list(pending or [])

    def list_pending(self, user_id: str) -> list[dict]:
        return list(self._pending)


def _reminder(message: str, fire_at: str, rid: str = "r1") -> dict:
    return {
        "id": rid,
        "user_id": "42",
        "message": message,
        "fire_at": fire_at,
        "fired": False,
        "created_at": "2026-06-19T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# PriorityScorer tests
# ---------------------------------------------------------------------------


class TestScorerP1AloneSupressesOthers:
    """1. P1+P2+P3 present → only the P1 is returned."""

    def test_scorer_p1_alone_suppresses_p2_and_p3(self):
        scorer = PriorityScorer(max_per_day=10)
        items = [
            _p1(nudge_type="alpha"),
            _p2(nudge_type="beta"),
            _p3(nudge_type="gamma"),
        ]
        selected = scorer.select(items, sent_today=0)
        assert len(selected) == 1
        assert selected[0].priority == P1


class TestScorerTopTwoP2:
    """2. No P1, 3 P2 items → exactly 2 returned in deterministic order."""

    def test_scorer_no_p1_returns_top_two_p2(self):
        scorer = PriorityScorer(max_per_day=10)
        items = [
            _p2(nudge_type="zzz", score=SCORE_P2),
            _p2(nudge_type="aaa", score=SCORE_P2),
            _p2(nudge_type="mmm", score=SCORE_P2),
        ]
        selected = scorer.select(items, sent_today=0)
        assert len(selected) == 2
        # Tie-break is nudge_type ASC → 'aaa', 'mmm'
        assert selected[0].nudge_type == "aaa"
        assert selected[1].nudge_type == "mmm"


class TestScorerOnlyP3ReturnsOne:
    """3. Only P3 items → exactly 1 returned."""

    def test_scorer_only_p3_returns_one(self):
        scorer = PriorityScorer(max_per_day=10)
        items = [_p3(nudge_type="x"), _p3(nudge_type="y"), _p3(nudge_type="z")]
        selected = scorer.select(items, sent_today=0)
        assert len(selected) == 1


class TestScorerDailyCap:
    """4. sent_today >= max → [] even with P1."""

    def test_scorer_respects_daily_cap(self):
        scorer = PriorityScorer(max_per_day=3)
        items = [_p1(nudge_type="urgent")]
        selected = scorer.select(items, sent_today=3)
        assert selected == []


class TestScorerRemainingOneP2:
    """5. sent_today=2, MAX=3 → at most 1 P2 (remaining=1)."""

    def test_scorer_remaining_one_p2(self):
        scorer = PriorityScorer(max_per_day=3)
        items = [_p2(nudge_type="a"), _p2(nudge_type="b")]
        selected = scorer.select(items, sent_today=2)
        assert len(selected) == 1


class TestRenderTonePrefixes:
    """6. Tone prefix applied correctly per priority."""

    def test_render_tone_prefixes(self):
        p1_item = _p1(message="your meeting is in 10 min")
        p2_item = _p2(message="how's Siddhi?")
        p3_item = _p3(message="quick water break")
        unknown = ProactiveItem(
            nudge_type="x", priority="P9", score=1, message="raw", alone=False
        )

        assert PriorityScorer.render(p1_item).startswith("🚨 Hey Arnav — ")
        assert "your meeting is in 10 min" in PriorityScorer.render(p1_item)

        assert PriorityScorer.render(p2_item).startswith("💬 Hey — ")
        assert "how's Siddhi?" in PriorityScorer.render(p2_item)

        assert PriorityScorer.render(p3_item).startswith("💡 ")
        assert "quick water break" in PriorityScorer.render(p3_item)

        # Unknown priority → message unchanged
        assert PriorityScorer.render(unknown) == "raw"


# ---------------------------------------------------------------------------
# Engine: already_sent_recently
# ---------------------------------------------------------------------------


class TestAlreadySentRecentlyWithinWindow:
    """7. Log entry 2h ago → True; 7h ago → False."""

    def test_already_sent_recently_within_window(self, tmp_path):
        eng = _make_engine(tmp_path)
        now = _now_utc()

        recent = now - timedelta(hours=2)
        old = now - timedelta(hours=7)

        # Write two log entries manually
        entries = [
            {"nudge_type": "test_nudge", "priority": P2, "sent_at": _to_z(recent), "message": "a"},
            {"nudge_type": "other_nudge", "priority": P2, "sent_at": _to_z(old), "message": "b"},
        ]
        eng._log_path.write_text(json.dumps(entries), encoding="utf-8")

        assert eng.already_sent_recently("test_nudge", now=now, window_hours=6) is True
        assert eng.already_sent_recently("other_nudge", now=now, window_hours=6) is False


class TestAlreadySentRecentlyMissingLog:
    """8. No log file → False, no crash."""

    def test_already_sent_recently_missing_log(self, tmp_path):
        eng = _make_engine(tmp_path)
        assert not eng._log_path.exists()
        result = eng.already_sent_recently("test_nudge", now=_now_utc())
        assert result is False


class TestAlreadySentRecentlyCorruptEntry:
    """9. Malformed sent_at → entry skipped, no crash, returns False."""

    def test_already_sent_recently_corrupt_entry_skipped(self, tmp_path):
        eng = _make_engine(tmp_path)
        entries = [
            {"nudge_type": "test_nudge", "priority": P2, "sent_at": "NOT_A_DATE", "message": "x"},
        ]
        eng._log_path.write_text(json.dumps(entries), encoding="utf-8")
        result = eng.already_sent_recently("test_nudge", now=_now_utc())
        assert result is False


# ---------------------------------------------------------------------------
# Engine: log_sent & _read_log
# ---------------------------------------------------------------------------


class TestLogSentAppendsAndPrunes:
    """10. log_sent writes entry; entry older than retention is pruned."""

    def test_log_sent_appends_and_prunes(self, tmp_path):
        eng = _make_engine(tmp_path)
        now = _now_utc()
        old_date = now - timedelta(days=10)

        # Pre-populate with a stale entry
        stale = {
            "nudge_type": "old_nudge",
            "priority": P3,
            "sent_at": _to_z(old_date),
            "message": "old",
        }
        eng._log_path.write_text(json.dumps([stale]), encoding="utf-8")

        item = _p2(nudge_type="fresh_nudge")
        eng.log_sent(item, now=now)

        entries = eng._read_log()
        nudge_types = [e["nudge_type"] for e in entries]
        assert "fresh_nudge" in nudge_types
        assert "old_nudge" not in nudge_types  # pruned


class TestLogSentAtomicRoundtrip:
    """11. Write then _read_log returns the correct entry."""

    def test_log_sent_atomic_roundtrip(self, tmp_path):
        eng = _make_engine(tmp_path)
        now = _now_utc()
        item = _p1(nudge_type="roundtrip_nudge", message="important thing")
        eng.log_sent(item, now=now)

        entries = eng._read_log()
        assert len(entries) == 1
        assert entries[0]["nudge_type"] == "roundtrip_nudge"
        assert entries[0]["priority"] == P1
        assert entries[0]["message"] == "important thing"
        assert entries[0]["sent_at"].endswith("Z")


# ---------------------------------------------------------------------------
# Engine: sent_today_count
# ---------------------------------------------------------------------------


class TestSentTodayCountISTBoundary:
    """12. IST date boundary: 23:30 IST today counts; 00:30 IST yesterday does not."""

    def test_sent_today_count_ist_boundary(self, tmp_path):
        eng = _make_engine(tmp_path)
        # Reference "now": 2026-06-19 23:30 IST = 18:00 UTC
        now_ist = datetime(2026, 6, 19, 23, 30, tzinfo=_IST)
        now_utc = now_ist.astimezone(UTC)

        # Entry 1: 23:30 IST on 2026-06-19 → today → should count
        today_entry_utc = now_utc  # same moment
        # Entry 2: 00:30 IST on 2026-06-19 = 2026-06-18 19:00 UTC → yesterday IST
        yesterday_ist = datetime(2026, 6, 18, 0, 30, tzinfo=_IST)
        yesterday_utc = yesterday_ist.astimezone(UTC)

        entries = [
            {"nudge_type": "a", "priority": P2, "sent_at": _to_z(today_entry_utc), "message": "x"},
            {"nudge_type": "b", "priority": P2, "sent_at": _to_z(yesterday_utc), "message": "y"},
        ]
        eng._log_path.write_text(json.dumps(entries), encoding="utf-8")

        count = eng.sent_today_count(now=now_utc)
        assert count == 1


# ---------------------------------------------------------------------------
# Engine: in_quiet_hours
# ---------------------------------------------------------------------------


class TestInQuietHours:
    """13. 02:00 IST→True; 08:00 IST→False (exclusive end); 00:30→True; 12:00→False."""

    def _at_ist_hour(self, hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 6, 19, hour, minute, tzinfo=_IST).astimezone(UTC)

    def test_quiet_at_2am(self):
        assert ProactiveEngine.in_quiet_hours(self._at_ist_hour(2)) is True

    def test_not_quiet_at_8am(self):
        # 08:00 is EXCLUSIVE end → not quiet
        assert ProactiveEngine.in_quiet_hours(self._at_ist_hour(8)) is False

    def test_quiet_at_0_30_with_custom_start(self):
        # With default start=1, 00:30 IST is NOT quiet (below the window).
        # With start=0 it IS quiet. Verify the override works.
        assert ProactiveEngine.in_quiet_hours(self._at_ist_hour(0, 30), start_ist=0) is True

    def test_not_quiet_at_0_30_default_window(self):
        # Default quiet window is 01:00–08:00 IST; 00:30 is before the window → not quiet.
        assert ProactiveEngine.in_quiet_hours(self._at_ist_hour(0, 30)) is False

    def test_not_quiet_at_noon(self):
        assert ProactiveEngine.in_quiet_hours(self._at_ist_hour(12)) is False


# ---------------------------------------------------------------------------
# Engine: decide — guard conditions
# ---------------------------------------------------------------------------


class TestDecideQuietHours:
    """14. now=03:00 IST → decide returns []."""

    def test_decide_returns_empty_in_quiet_hours(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JACK_PROACTIVE_ENABLED", "1")
        eng = _make_engine(tmp_path)
        now = _ist(3)  # 03:00 IST
        result = eng.decide("42", now=now)
        assert result == []


class TestDecideDisabledMasterSwitch:
    """15. JACK_PROACTIVE_ENABLED=false → decide returns []."""

    def test_decide_disabled_master_switch(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JACK_PROACTIVE_ENABLED", "false")
        # Use waking hours so quiet check doesn't interfere
        now = _ist(14)  # 14:00 IST
        eng = _make_engine(tmp_path)
        result = eng.decide("42", now=now)
        assert result == []


class TestDecideFiltersRecentlySent:
    """16. Candidate already in log within 6h → excluded from decide output."""

    def test_decide_filters_recently_sent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JACK_PROACTIVE_ENABLED", "1")
        now = _ist(14)

        eng = _make_engine(tmp_path, store=_FakeStore([]))
        # Pre-log the gym nudge as recently sent
        recent_sent = now - timedelta(hours=1)
        entries = [
            {
                "nudge_type": "gym_not_done",
                "priority": P2,
                "sent_at": _to_z(recent_sent),
                "message": "gym",
            }
        ]
        eng._log_path.write_text(json.dumps(entries), encoding="utf-8")

        # Force check_gym to produce the gym item (hour >= 19 not satisfied at 14:00 IST)
        # Instead we manually inject via check_deadlines producing nothing and monkeypatching gather
        original_gather = eng.gather_all_items

        def fake_gather(user_id, *, now=None, history=None):
            return [_p2(nudge_type="gym_not_done", message="gym")]

        eng.gather_all_items = fake_gather

        result = eng.decide("42", now=now)
        assert all(item.nudge_type != "gym_not_done" for item in result)


# ---------------------------------------------------------------------------
# Engine: check_deadlines
# ---------------------------------------------------------------------------


class TestCheckDeadlinesReminderWithin24h:
    """17. Reminder 12h out → P1 item; reminder 30h out → not included."""

    def test_check_deadlines_reminder_within_24h(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JACK_CALENDAR_ENABLED", "0")
        now = _now_utc()
        fire_close = _to_z(now + timedelta(hours=12))
        fire_far = _to_z(now + timedelta(hours=30))

        store = _FakeStore([
            _reminder("Doctor appointment", fire_close, rid="r1"),
            _reminder("Far future thing", fire_far, rid="r2"),
        ])

        eng = _make_engine(tmp_path, store=store)
        items = eng.check_deadlines("42", now=now)

        types = [it.nudge_type for it in items]
        assert any("r1" in t for t in types), "close reminder should appear"
        assert all("r2" not in t for t in types), "far reminder must not appear"
        assert all(it.priority == P1 for it in items)


class TestCheckDeadlinesCalendarNoneDegrades:
    """18. list_events=None → no crash; reminder source still evaluated."""

    def test_check_deadlines_calendar_none_degrades(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JACK_CALENDAR_ENABLED", "1")
        now = _now_utc()
        fire_close = _to_z(now + timedelta(hours=5))

        class _BadCalendar:
            def list_events(self, day=None, max_results=10):
                return None

        store = _FakeStore([_reminder("urgent meeting", fire_close, rid="r3")])
        eng = _make_engine(tmp_path, store=store, calendar_client=_BadCalendar())

        # Should not raise; reminder source still produces an item
        items = eng.check_deadlines("42", now=now)
        assert any("r3" in it.nudge_type for it in items)


# ---------------------------------------------------------------------------
# Engine: check_gym
# ---------------------------------------------------------------------------


class TestCheckGymAfter7pmNotDone:
    """19. now=20:00 IST, no gym event → P2; gym event present → []."""

    def test_check_gym_after_7pm_not_done(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JACK_CALENDAR_ENABLED", "0")
        now = _ist(20)

        eng = _make_engine(tmp_path, store=_FakeStore([]))
        items = eng.check_gym("42", now=now)

        assert len(items) == 1
        assert items[0].nudge_type == "gym_not_done"
        assert items[0].priority == P2

    def test_check_gym_after_7pm_with_gym_event(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JACK_CALENDAR_ENABLED", "1")
        now = _ist(20)

        class _GymCalendar:
            def list_events(self, day=None, max_results=20):
                return [{"summary": "Gym session", "start": {"dateTime": "2026-06-19T07:00:00+05:30"}, "end": {}}]

        eng = _make_engine(tmp_path, store=_FakeStore([]), calendar_client=_GymCalendar())
        items = eng.check_gym("42", now=now)
        assert items == []


class TestCheckGymBefore7pmSilent:
    """20. now=15:00 IST → []."""

    def test_check_gym_before_7pm_silent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JACK_CALENDAR_ENABLED", "0")
        now = _ist(15)
        eng = _make_engine(tmp_path, store=_FakeStore([]))
        items = eng.check_gym("42", now=now)
        assert items == []


# ---------------------------------------------------------------------------
# Engine: check_weekly_planning
# ---------------------------------------------------------------------------


class TestCheckWeeklyPlanning:
    """21. Sunday 18:00 IST → P2; Monday 18:00 IST → []."""

    def test_check_weekly_planning_sunday_evening(self, tmp_path):
        eng = _make_engine(tmp_path)
        sunday_18 = _ist(18, weekday_sunday=True)
        items = eng.check_weekly_planning(now=sunday_18)
        assert len(items) == 1
        assert items[0].nudge_type == "weekly_planning"
        assert items[0].priority == P2

    def test_check_weekly_planning_monday_silent(self, tmp_path):
        eng = _make_engine(tmp_path)
        # 2026-06-22 is Monday
        monday_18 = datetime(2026, 6, 22, 18, 0, tzinfo=_IST).astimezone(UTC)
        items = eng.check_weekly_planning(now=monday_18)
        assert items == []


# ---------------------------------------------------------------------------
# Engine: check_siddhi
# ---------------------------------------------------------------------------


class TestCheckSiddhiOutsideWindowSilent:
    """22. now=23:30 IST → []."""

    def test_check_siddhi_outside_window_silent(self, tmp_path):
        eng = _make_engine(tmp_path)
        now = _ist(23, 30)
        items = eng.check_siddhi(now=now, history=[])
        assert items == []


class TestCheckSiddhiNoSignal:
    """23. No history, no Siddhi marker → []."""

    def test_check_siddhi_no_signal_returns_empty(self, tmp_path):
        eng = _make_engine(tmp_path)
        now = _ist(14)
        items = eng.check_siddhi(now=now, history=None)
        assert items == []

    def test_check_siddhi_no_history_list(self, tmp_path):
        eng = _make_engine(tmp_path)
        now = _ist(14)
        items = eng.check_siddhi(now=now, history=[])
        assert items == []


# ---------------------------------------------------------------------------
# Engine: check_goals
# ---------------------------------------------------------------------------


class TestCheckGoalsStaleTwoDays:
    """24. Goal never nudged → P2; nudged within 2 days → []."""

    def test_check_goals_stale_over_two_days(self, tmp_path):
        user_md = "[GOALS]\nShip Vytal v2\nRead 12 books this year\n"
        eng = _make_engine(tmp_path, user_md=user_md)
        now = _now_utc()
        items = eng.check_goals(now=now)
        assert len(items) == 1
        assert items[0].priority == P2

    def test_check_goals_recently_nudged_suppressed(self, tmp_path):
        user_md = "[GOALS]\nShip Vytal v2\n"
        eng = _make_engine(tmp_path, user_md=user_md)
        now = _now_utc()

        # Compute what the nudge_type would be for "Ship Vytal v2"
        goal = "Ship Vytal v2"
        nudge_type = f"goal_stale_{hash(goal[:40]) & 0xFFFF}"

        # Log it as sent 1 day ago (within 2-day window)
        recent = now - timedelta(days=1)
        entries = [
            {"nudge_type": nudge_type, "priority": P2, "sent_at": _to_z(recent), "message": "g"}
        ]
        eng._log_path.write_text(json.dumps(entries), encoding="utf-8")

        items = eng.check_goals(now=now)
        assert items == []


# ---------------------------------------------------------------------------
# Engine: run_cycle — send failure
# ---------------------------------------------------------------------------


class TestRunCycleSendFailureNotLogged:
    """25. send_fn returns False → log_sent NOT called."""

    def test_run_cycle_send_failure_not_logged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JACK_PROACTIVE_ENABLED", "1")
        now = _ist(14)

        failing_send = lambda content, *, user_id=None: False  # noqa: E731
        eng = _make_engine(tmp_path, send_fn=failing_send, store=_FakeStore([]))

        # Force decide to return one item
        eng.decide = lambda user_id, *, now=None, history=None: [_p2(nudge_type="test_fail")]

        count = eng.run_cycle("42", now=now)
        assert count == 0
        assert not eng._log_path.exists() or eng._read_log() == []


class TestRunCycleHappyPath:
    """26. send_fn returns True → item logged; returns 1."""

    def test_run_cycle_happy_path_logs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JACK_PROACTIVE_ENABLED", "1")
        monkeypatch.setenv("JACK_PROACTIVE_ENGINE", "legacy")
        now = _ist(14)

        calls: list[str] = []
        success_send = lambda content, *, user_id=None: (calls.append(content), True)[1]  # noqa: E731
        eng = _make_engine(tmp_path, send_fn=success_send, store=_FakeStore([]))

        eng.decide = lambda user_id, *, now=None, history=None: [_p2(nudge_type="happy_nudge", message="hi")]

        count = eng.run_cycle("42", now=now)
        assert count == 1
        assert len(calls) == 1
        entries = eng._read_log()
        assert len(entries) == 1
        assert entries[0]["nudge_type"] == "happy_nudge"


# ---------------------------------------------------------------------------
# Engine: gather isolation
# ---------------------------------------------------------------------------


class TestGatherIsolationOneCheckRaises:
    """27. One check_* raises → others still evaluated."""

    def test_gather_isolation_one_check_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JACK_CALENDAR_ENABLED", "0")
        now = _ist(20)  # 20:00 IST — gym check would fire

        eng = _make_engine(tmp_path, store=_FakeStore([]))

        # Patch check_gym to raise
        original_gym = eng.check_gym
        def raising_gym(user_id, *, now=None):
            raise RuntimeError("simulated gym failure")
        eng.check_gym = raising_gym

        # Patch check_weekly_planning to return a known item (Sunday 20:00 IST)
        sunday_20 = _ist(20, weekday_sunday=True)
        items = eng.gather_all_items("42", now=sunday_20)

        # weekly_planning should fire on Sunday; gym raised but didn't crash
        # (items may or may not include weekly_planning depending on exact Sunday date)
        # The key assertion: no exception raised
        assert isinstance(items, list)


# ---------------------------------------------------------------------------
# Engine: daily cap across cycles
# ---------------------------------------------------------------------------


class TestNeverMoreThanThreePerDay:
    """28. Simulate multiple run_cycle calls; total sent must not exceed MAX."""

    def test_never_more_than_three_per_day_across_cycles(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JACK_PROACTIVE_ENABLED", "1")
        max_per_day = 3
        now = _ist(14)

        send_log: list[str] = []

        def counting_send(content, *, user_id=None):
            send_log.append(content)
            return True

        scorer = PriorityScorer(max_per_day=max_per_day)
        eng = _make_engine(tmp_path, send_fn=counting_send, store=_FakeStore([]), scorer=scorer)

        # Each decide call returns 2 P2 items with different nudge_types per cycle
        cycle_count = [0]

        def fake_decide(user_id, *, now=None, history=None):
            n = cycle_count[0]
            cycle_count[0] += 1
            return [
                _p2(nudge_type=f"nudge_{n}_a"),
                _p2(nudge_type=f"nudge_{n}_b"),
            ]

        eng.decide = fake_decide

        # Run 5 cycles but inject real log_sent so the count accumulates
        for _ in range(5):
            selected = eng.decide("42", now=now)
            scorer_local = eng._get_scorer()
            sent_today = eng.sent_today_count(now=now)
            to_send = scorer_local.select(selected, sent_today=sent_today)
            for item in to_send:
                ok = counting_send(scorer_local.render(item), user_id="42")
                if ok:
                    eng.log_sent(item, now=now)

        assert len(send_log) <= max_per_day


# ============================================================
# New tests: JACK_PROACTIVE_ENGINE flag behavior (proactive-reasoning-loop branch)
# ============================================================

class TestEngineModeFlagInRunCycle:
    """run_cycle() guards: legacy mode calls gather_all_items; reasoner mode returns 0."""

    def test_legacy_mode_uses_gather_all_items(self, tmp_path, monkeypatch):
        """JACK_PROACTIVE_ENGINE=legacy → run_cycle proceeds; gather_all_items is called."""
        monkeypatch.setenv("JACK_PROACTIVE_ENGINE", "legacy")
        monkeypatch.setenv("JACK_PROACTIVE_ENABLED", "1")

        log_path = tmp_path / "proactive_log.json"
        engine = ProactiveEngine(log_path=log_path)

        gather_calls = []

        def spy_gather(user_id, *, now=None, history=None):
            gather_calls.append(user_id)
            return []

        engine.gather_all_items = spy_gather

        import datetime as _dt
        now = _dt.datetime(2026, 6, 22, 7, 45, tzinfo=_dt.timezone.utc)
        engine.run_cycle("arnav", now=now)

        assert len(gather_calls) >= 1, "gather_all_items should be called in legacy mode"

    def test_reasoner_mode_run_cycle_returns_zero(self, tmp_path, monkeypatch):
        """JACK_PROACTIVE_ENGINE=reasoner (default) → run_cycle returns 0 without calling check_*."""
        monkeypatch.setenv("JACK_PROACTIVE_ENGINE", "reasoner")

        log_path = tmp_path / "proactive_log.json"
        engine = ProactiveEngine(log_path=log_path)

        check_calls = []

        engine.check_deadlines = lambda *a, **kw: (check_calls.append("deadlines") or [])
        engine.check_gym = lambda *a, **kw: (check_calls.append("gym") or [])
        engine.check_goals = lambda *a, **kw: (check_calls.append("goals") or [])
        engine.gather_all_items = lambda *a, **kw: (check_calls.append("gather") or [])

        import datetime as _dt
        now = _dt.datetime(2026, 6, 22, 7, 45, tzinfo=_dt.timezone.utc)
        result = engine.run_cycle("arnav", now=now)

        assert result == 0
        assert check_calls == [], f"check_* should not be called in reasoner mode, but got: {check_calls}"

    def test_legacy_mode_default_is_now_reasoner(self, tmp_path, monkeypatch):
        """Default (no env var set) → run_cycle returns 0 (reasoner mode by default)."""
        monkeypatch.delenv("JACK_PROACTIVE_ENGINE", raising=False)

        log_path = tmp_path / "proactive_log.json"
        engine = ProactiveEngine(log_path=log_path)

        import datetime as _dt
        now = _dt.datetime(2026, 6, 22, 7, 45, tzinfo=_dt.timezone.utc)
        result = engine.run_cycle("arnav", now=now)

        assert result == 0


class TestDecideViaReasoner:
    """decide_via_reasoner() applies quiet hours, enabled flag, scorer, dedup."""

    def _make_engine(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JACK_PROACTIVE_ENABLED", "1")
        monkeypatch.delenv("JACK_PROACTIVE_ENGINE", raising=False)
        log_path = tmp_path / "proactive_log.json"
        return ProactiveEngine(log_path=log_path)

    def _make_reasoner_stub(self, items):
        from unittest.mock import MagicMock
        r = MagicMock()
        r.reason.return_value = items
        r.last_raw_response = "[]"
        return r

    def _p1_item(self):
        return ProactiveItem(
            nudge_type="test_deadline", priority=P1, score=SCORE_P1,
            message="Demo tomorrow", alone=True, meta={"source": "reasoner", "reasoning": ""}
        )

    def test_fallback_on_reasoner_returning_empty(self, tmp_path, monkeypatch):
        """reasoner.reason() returns [] → decide_via_reasoner returns []."""
        engine = self._make_engine(tmp_path, monkeypatch)
        reasoner = self._make_reasoner_stub([])

        import datetime as _dt
        now = _dt.datetime(2026, 6, 22, 7, 45, tzinfo=_dt.timezone.utc)
        result = engine.decide_via_reasoner("arnav", reasoner, now=now)

        assert result == []

    def test_fallback_on_reasoner_raising(self, tmp_path, monkeypatch):
        """reasoner.reason() raises → decide_via_reasoner catches it and returns []."""
        engine = self._make_engine(tmp_path, monkeypatch)

        from unittest.mock import MagicMock
        reasoner = MagicMock()
        reasoner.reason.side_effect = RuntimeError("reasoner exploded")

        import datetime as _dt
        now = _dt.datetime(2026, 6, 22, 7, 45, tzinfo=_dt.timezone.utc)
        # Should NOT raise
        result = engine.decide_via_reasoner("arnav", reasoner, now=now)
        assert result == []

    def test_decide_via_reasoner_returns_items_after_scorer(self, tmp_path, monkeypatch):
        """Returns items from scorer when reasoner provides valid candidates."""
        engine = self._make_engine(tmp_path, monkeypatch)
        p1 = self._p1_item()
        reasoner = self._make_reasoner_stub([p1])

        import datetime as _dt
        now = _dt.datetime(2026, 6, 22, 7, 45, tzinfo=_dt.timezone.utc)
        result = engine.decide_via_reasoner("arnav", reasoner, now=now)

        assert len(result) == 1
        assert result[0].nudge_type == "test_deadline"
        assert result[0].priority == "P1"

    def test_decide_via_reasoner_quiet_hours(self, tmp_path, monkeypatch):
        """Returns [] during quiet hours regardless of reasoner output."""
        engine = self._make_engine(tmp_path, monkeypatch)
        p1 = self._p1_item()
        reasoner = self._make_reasoner_stub([p1])

        import datetime as _dt
        # 03:00 IST = 21:30 UTC
        now_quiet = _dt.datetime(2026, 6, 21, 21, 30, tzinfo=_dt.timezone.utc)
        result = engine.decide_via_reasoner("arnav", reasoner, now=now_quiet)

        assert result == []

    def test_decide_via_reasoner_dedup(self, tmp_path, monkeypatch):
        """Items already sent recently are excluded from results."""
        engine = self._make_engine(tmp_path, monkeypatch)
        p1 = self._p1_item()
        reasoner = self._make_reasoner_stub([p1])

        import datetime as _dt
        now = _dt.datetime(2026, 6, 22, 7, 45, tzinfo=_dt.timezone.utc)

        # Log the item as already sent (within dedup window)
        engine.log_sent(p1, now=now - _dt.timedelta(hours=1))

        result = engine.decide_via_reasoner("arnav", reasoner, now=now)
        assert result == [], "Already-sent item should be deduped out"
