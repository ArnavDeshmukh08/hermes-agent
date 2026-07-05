"""Tests for marathon-aware proactive reasoning in proactive/reasoner.py.

These tests verify that gather_context() correctly:
1. Loads and surfaces health_today.json data (steps, runs, sleep, body battery).
2. Flags sedentary streaks and absent run data for the LLM to reason about.
3. Handles a missing health_today.json gracefully (no crash, clear fallback text).
4. Flags stale health data with an explicit note.
5. Contains the FRAMING RULE and MARATHON AWARENESS text in the prompt.
6. Does NOT contain prescriptive language in the system prompt (_SYSTEM_PROMPT).
"""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_IST = timezone(timedelta(hours=5, minutes=30))
# 18:00 IST = 12:30 UTC
_NOW_LATE_IST = datetime(2026, 6, 29, 12, 30, tzinfo=timezone.utc)
# 09:00 IST = 03:30 UTC
_NOW_MORNING_IST = datetime(2026, 6, 29, 3, 30, tzinfo=timezone.utc)


def _make_hourly_steps(*, sedentary_hours: int = 0, base_steps: int = 0) -> list[dict]:
    """Build a list of hourly step dicts.

    Produces 8 hours of data.  The last `sedentary_hours` hours each have a
    step delta < 500 (50 steps per hour).  The earlier hours contribute 600
    steps each to ensure they break the streak.
    """
    n_total = 8
    hourly = []
    cumulative = base_steps
    for i in range(n_total):
        is_sedentary = i >= (n_total - sedentary_hours)
        delta = 50 if is_sedentary else 600
        cumulative += delta
        hourly.append({"hour": i, "steps": cumulative})
    return hourly


def _make_health_json(
    *,
    steps: int = 3500,
    body_battery_high: int = 85,
    calories: int = 1200,
    stress_avg: int = 32,
    total_sleep_h: float = 7.2,
    deep_h: float = 1.5,
    rem_h: float = 1.8,
    sleep_score: int = 72,
    runs_today=None,
    runs_available: bool = False,
    is_stale: bool = False,
    sedentary_hours: int = 0,
) -> dict:
    """Build a health_today.json-style dict."""
    hourly = _make_hourly_steps(sedentary_hours=sedentary_hours, base_steps=steps)
    return {
        "fetched_at_utc": "2026-06-29T11:00:00+00:00",
        "date_ist": "2026-06-29",
        "is_stale": is_stale,
        "stats": {
            "steps": steps,
            "body_battery_high": body_battery_high,
            "calories": calories,
            "stress_avg": stress_avg,
        },
        "sleep": {
            "total_sleep_h": total_sleep_h,
            "deep_h": deep_h,
            "rem_h": rem_h,
            "score": sleep_score,
        },
        "runs_today": runs_today,
        "runs_available": runs_available,
        "hourly_steps": hourly,
    }


def _make_marathon_goal() -> MagicMock:
    """Return a MagicMock that looks like the marathon Goal object."""
    g = MagicMock()
    g.id = "d0ac89d6"
    g.title = "Run a marathon"
    g.type = "fitness"
    g.metric = "garmin_runs"
    g.target = "42.2 km"
    g.plan = ""
    g.deadline = "2026-08-02"
    g.progress_notes = []
    g.last_checked = None
    return g


def _call_gather_context(
    now: datetime = _NOW_LATE_IST,
    health_file_content: dict | None = None,
    health_file_exists: bool = True,
    tmp_path: Path | None = None,
) -> str:
    """Call gather_context() with controlled health_today.json.

    Parameters
    ----------
    now:
        The ``now`` datetime passed to gather_context().
    health_file_content:
        If provided (and health_file_exists=True), write this dict as JSON to a
        temp file and patch Path.home() so the code finds it.
    health_file_exists:
        If False, patch Path.home() to a directory that has no health_today.json.
    tmp_path:
        A pytest tmp_path for writing the fixture file.  Required when
        health_file_content is not None.
    """
    from proactive.reasoner import gather_context

    mc = MagicMock()
    mc.search.return_value = []

    store = MagicMock()
    store.list_pending.return_value = []

    queue = MagicMock()
    queue.__len__ = MagicMock(return_value=0)

    if health_file_exists and health_file_content is not None:
        assert tmp_path is not None, "tmp_path is required when health_file_content is provided"
        hermes_dir = tmp_path / ".hermes"
        hermes_dir.mkdir(parents=True, exist_ok=True)
        health_file = hermes_dir / "health_today.json"
        health_file.write_text(json.dumps(health_file_content), encoding="utf-8")
        fake_home = tmp_path
    else:
        # No file: point home() at a fresh empty dir
        fake_home = tmp_path if tmp_path is not None else Path("/nonexistent_path_xyz")

    with patch("proactive.reasoner.Path") as mock_path_cls:
        # We only intercept Path.home(); other Path usages must still work.
        real_path = Path

        def path_side_effect(*args):
            return real_path(*args)

        mock_path_cls.side_effect = path_side_effect
        mock_path_cls.home.return_value = fake_home

        with patch("jack_goals.store.list_active_goals", return_value=[_make_marathon_goal()]):
            ctx = gather_context(mc, store, None, queue, "arnav", now)

    return ctx


# ---------------------------------------------------------------------------
# Test 1: accountability — no run, late afternoon, sedentary streak
# ---------------------------------------------------------------------------


def test_marathon_accountability_when_no_run_and_late(tmp_path):
    """Context reflects low activity and no run when it's late and nothing has been logged."""
    health = _make_health_json(
        steps=2000,
        runs_today=None,
        runs_available=False,
        is_stale=False,
        sedentary_hours=4,
    )
    ctx = _call_gather_context(
        now=_NOW_LATE_IST,
        health_file_content=health,
        health_file_exists=True,
        tmp_path=tmp_path,
    )

    # Context must contain the marathon deadline so the LLM can compute urgency
    assert "2026-08-02" in ctx or "Aug 2" in ctx, (
        "Deadline 2026-08-02 or 'Aug 2' must appear in context for LLM deadline math"
    )

    # Context must show the low step count or signal no run data
    assert "2000" in ctx or "None" in ctx or "null" in ctx or "Runs logged today: None" in ctx, (
        "Low step count or null run data must be visible in context"
    )

    # Sedentary streak should be reflected
    assert "Sedentary streak" in ctx, "Sedentary streak line must appear in health snapshot"

    # Health snapshot section header must exist
    assert "Intraday Health Snapshot" in ctx, "Health snapshot section must be present"


# ---------------------------------------------------------------------------
# Test 2: on track — run logged, steps high — context stays positive
# ---------------------------------------------------------------------------


def test_marathon_on_track_stays_quiet(tmp_path):
    """When a run is logged and steps are high, context reflects positive state."""
    health = _make_health_json(
        steps=9000,
        runs_today={"distance_km": 5.0, "duration_min": 32},
        runs_available=True,
        is_stale=False,
        sedentary_hours=0,
    )
    ctx = _call_gather_context(
        now=_NOW_LATE_IST,
        health_file_content=health,
        health_file_exists=True,
        tmp_path=tmp_path,
    )

    # Step count visible
    assert "9000" in ctx, "High step count must appear in context"

    # Run data present and not null
    assert "Runs logged today:" in ctx, "Runs logged today line must appear"
    assert "None" not in ctx.split("Runs logged today:")[1].split("\n")[0], (
        "Run data should not read 'None' when a run was logged"
    )

    # Health snapshot section must be present
    assert "Intraday Health Snapshot" in ctx


# ---------------------------------------------------------------------------
# Test 3: missing health file — graceful degradation
# ---------------------------------------------------------------------------


def test_no_health_file_graceful(tmp_path):
    """gather_context() must not raise when health_today.json is absent."""
    ctx = _call_gather_context(
        now=_NOW_LATE_IST,
        health_file_content=None,
        health_file_exists=False,
        tmp_path=tmp_path,
    )
    assert isinstance(ctx, str), "Must return a string even with no health file"
    assert "No intraday health snapshot" in ctx or "health snapshot" in ctx.lower(), (
        "Context must clearly state no health snapshot is available"
    )
    # Core sections still present
    assert "# Current time" in ctx
    assert "# What I remember about Arnav" in ctx


# ---------------------------------------------------------------------------
# Test 4: stale health file — flagged in context
# ---------------------------------------------------------------------------


def test_stale_health_flagged(tmp_path):
    """When is_stale=True, the context must mention 'stale' or 'Mac unreachable'."""
    health = _make_health_json(
        steps=3500,
        is_stale=True,
    )
    ctx = _call_gather_context(
        now=_NOW_LATE_IST,
        health_file_content=health,
        health_file_exists=True,
        tmp_path=tmp_path,
    )
    # The stale warning must appear
    assert "stale" in ctx.lower() or "mac unreachable" in ctx.lower(), (
        "Stale health data must be flagged in context with 'stale' or 'Mac unreachable'"
    )


# ---------------------------------------------------------------------------
# Test 5: FRAMING RULE appears; forbidden prescriptive words absent
# ---------------------------------------------------------------------------


def test_framing_rule_in_prompt(tmp_path):
    """The context string contains FRAMING RULE and no prescriptive words."""
    health = _make_health_json()
    ctx = _call_gather_context(
        now=_NOW_LATE_IST,
        health_file_content=health,
        health_file_exists=True,
        tmp_path=tmp_path,
    )

    # FRAMING RULE must appear
    assert "FRAMING RULE" in ctx or "NEVER prescribe" in ctx, (
        "FRAMING RULE or 'NEVER prescribe' must appear in the context prompt"
    )

    ctx_lower = ctx.lower()
    # These prescriptive constructs must NOT appear as instructions TO the LLM.
    # Note: the framing text may say "NEVER prescribe ... recovery protocol" —
    # that mention is fine (it's a prohibition). We check constructs that would
    # only appear if the context were actually prescribing something.
    actually_prescriptive = [
        "do 5x800m",
        "run at 4:30 pace",
        "eat more protein",
        "rest 48 hours",
    ]
    for term in actually_prescriptive:
        assert term not in ctx_lower, (
            f"Prescriptive instruction {term!r} must not appear in context prompt"
        )


# ---------------------------------------------------------------------------
# Test 6: _SYSTEM_PROMPT contains no prescriptive training language
# ---------------------------------------------------------------------------


def test_no_prescriptive_language_in_system_prompt():
    """_SYSTEM_PROMPT must not contain prescriptive training/diet/medical language."""
    from proactive.reasoner import _SYSTEM_PROMPT

    sp_lower = _SYSTEM_PROMPT.lower()
    # These should never appear in the system prompt itself
    forbidden_in_system = ["workout plan", "diet", "prescription"]
    for term in forbidden_in_system:
        assert term not in sp_lower, (
            f"System prompt must not contain prescriptive term {term!r}"
        )

    # Also verify "train" as a standalone training instruction isn't in there
    # (allow "training" only if it's framing language like "training mirror" — we
    # check the system prompt itself which is short and should not have it at all)
    assert "train" not in sp_lower, (
        "System prompt must not contain 'train' (no coaching language)"
    )
