"""Tests for integrations.garmin_poller.

All tests are fully offline — no real network calls or file-system side effects
beyond the pytest tmp_path fixture.

sys.path wiring mirrors tests/test_garmin.py.
"""

from __future__ import annotations

import datetime
import json
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "hooks" / "jack_router"))

import pytest  # noqa: E402

from integrations import garmin_poller  # noqa: E402
from integrations.garmin_poller import (  # noqa: E402
    _build_snapshot,
    _read_snapshot,
    _today_ist,
    _write_snapshot,
    poll_once,
    sedentary_streak_hours,
    steps_trend,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_UTC = datetime.timezone.utc
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

_NOW_UTC = datetime.datetime(2026, 6, 29, 11, 0, 0, tzinfo=_UTC)
_DATE_IST = "2026-06-29"  # 11:00 UTC = 16:30 IST, same calendar day

_SUMMARY = {
    "date": "2026-06-29",
    "sleep": {
        "total_sleep_h": 7.2,
        "deep_h": 1.5,
        "rem_h": 1.8,
        "score": 72,
    },
    "stats": {
        "steps": 3500,
        "body_battery_high": 85,
        "calories": 1200,
        "stress_avg": 32,
    },
}

_SLEEP = {
    "total_sleep_h": 7.2,
    "deep_h": 1.5,
    "rem_h": 1.8,
    "score": 72,
}


def _make_mock_client(summary=_SUMMARY, sleep=_SLEEP, runs=None) -> MagicMock:
    """Return a mock GarminClient whose methods return the given payloads."""
    mock = MagicMock()
    mock.get_daily_summary.return_value = summary
    mock.last_night_sleep.return_value = sleep
    mock.get_recent_runs.return_value = runs
    return mock


# Patch target for GarminClient used inside poll_once
_GARMIN_CLIENT = "integrations.garmin_poller.GarminClient"


# ---------------------------------------------------------------------------
# Test 1 — snapshot written with required fields
# ---------------------------------------------------------------------------

def test_snapshot_written(tmp_path: Path) -> None:
    """poll_once() writes health_today.json with all required top-level keys."""
    snap_path = tmp_path / "health_today.json"

    with (
        patch(_GARMIN_CLIENT, return_value=_make_mock_client()),
        patch("integrations.garmin_poller._now_utc", return_value=_NOW_UTC),
        patch("integrations.garmin_poller._write_history_entry"),
    ):
        result = poll_once(snapshot_path=snap_path)

    # File must exist
    assert snap_path.exists(), "health_today.json was not created"

    # Persisted JSON matches returned dict
    on_disk = json.loads(snap_path.read_text())
    assert on_disk == result

    # Required fields present
    required = {"fetched_at_utc", "date_ist", "is_stale", "stats", "sleep",
                "runs_today", "runs_available", "hourly_steps"}
    assert required.issubset(set(on_disk.keys())), f"Missing keys: {required - set(on_disk.keys())}"

    # Sanity-check values
    assert on_disk["is_stale"] is False
    assert on_disk["date_ist"] == _DATE_IST
    assert on_disk["stats"]["steps"] == 3500
    assert on_disk["sleep"]["score"] == 72
    assert on_disk["runs_available"] is False
    assert isinstance(on_disk["hourly_steps"], list)


# ---------------------------------------------------------------------------
# Test 2 — stale snapshot when Mac is unreachable
# ---------------------------------------------------------------------------

def test_stale_on_mac_unreachable(tmp_path: Path) -> None:
    """When GarminClient returns None for everything, is_stale must be True."""
    snap_path = tmp_path / "health_today.json"

    with (
        patch(_GARMIN_CLIENT, return_value=_make_mock_client(summary=None, sleep=None)),
        patch("integrations.garmin_poller._now_utc", return_value=_NOW_UTC),
        patch("integrations.garmin_poller._write_history_entry"),
    ):
        result = poll_once(snapshot_path=snap_path)

    assert result["is_stale"] is True
    assert result["stats"] is None
    assert result["sleep"] is None

    on_disk = json.loads(snap_path.read_text())
    assert on_disk["is_stale"] is True


# ---------------------------------------------------------------------------
# Test 3 — sedentary streak is derivable from hourly_steps
# ---------------------------------------------------------------------------

def test_sedentary_streak_derivable() -> None:
    """Given 3 hourly snapshots all with step-deltas < 500, streak == 2."""
    # Steps: 1000 → 1300 → 1500 → 1700 (deltas: 300, 200, 200 — all < 500)
    hourly = [
        {"timestamp_utc": "2026-06-29T09:00:00+00:00", "steps_cumulative": 1000},
        {"timestamp_utc": "2026-06-29T10:00:00+00:00", "steps_cumulative": 1300},
        {"timestamp_utc": "2026-06-29T11:00:00+00:00", "steps_cumulative": 1500},
        {"timestamp_utc": "2026-06-29T12:00:00+00:00", "steps_cumulative": 1700},
    ]
    snap = {"hourly_steps": hourly}

    streak = sedentary_streak_hours(snap)
    # All three trailing deltas < 500 → streak == 3
    assert streak == 3, f"Expected sedentary streak of 3, got {streak}"


def test_sedentary_streak_breaks_on_active_hour() -> None:
    """Streak resets when a recent hour exceeds 500-step delta."""
    hourly = [
        {"timestamp_utc": "2026-06-29T09:00:00+00:00", "steps_cumulative": 1000},
        {"timestamp_utc": "2026-06-29T10:00:00+00:00", "steps_cumulative": 2500},  # +1500 active
        {"timestamp_utc": "2026-06-29T11:00:00+00:00", "steps_cumulative": 2700},  # +200 sedentary
        {"timestamp_utc": "2026-06-29T12:00:00+00:00", "steps_cumulative": 2900},  # +200 sedentary
    ]
    snap = {"hourly_steps": hourly}

    streak = sedentary_streak_hours(snap)
    assert streak == 2, f"Expected streak of 2 (stopped at active hour), got {streak}"


# ---------------------------------------------------------------------------
# Test 4 — steps trend is derivable from hourly_steps
# ---------------------------------------------------------------------------

def test_steps_trend_derivable_increasing() -> None:
    """Increasing step-deltas are identified as 'increasing'."""
    hourly = [
        {"timestamp_utc": "2026-06-29T09:00:00+00:00", "steps_cumulative": 1000},
        {"timestamp_utc": "2026-06-29T10:00:00+00:00", "steps_cumulative": 1200},  # +200
        {"timestamp_utc": "2026-06-29T11:00:00+00:00", "steps_cumulative": 1500},  # +300 (100 more)
    ]
    snap = {"hourly_steps": hourly}
    assert steps_trend(snap) == "increasing"


def test_steps_trend_derivable_flat() -> None:
    """Flat or decreasing deltas are identified as 'flat'."""
    hourly = [
        {"timestamp_utc": "2026-06-29T09:00:00+00:00", "steps_cumulative": 1000},
        {"timestamp_utc": "2026-06-29T10:00:00+00:00", "steps_cumulative": 1300},  # +300
        {"timestamp_utc": "2026-06-29T11:00:00+00:00", "steps_cumulative": 1550},  # +250 (less)
    ]
    snap = {"hourly_steps": hourly}
    assert steps_trend(snap) == "flat"


def test_steps_trend_unknown_if_insufficient_data() -> None:
    """Returns 'unknown' when fewer than 3 hourly data points exist."""
    snap = {"hourly_steps": [
        {"timestamp_utc": "2026-06-29T09:00:00+00:00", "steps_cumulative": 1000},
    ]}
    assert steps_trend(snap) == "unknown"


# ---------------------------------------------------------------------------
# Test 5 — concurrent writes don't corrupt the file (flock)
# ---------------------------------------------------------------------------

def test_flock_write(tmp_path: Path) -> None:
    """Multiple concurrent poll_once() calls produce a valid JSON file."""
    snap_path = tmp_path / "health_today.json"
    errors: list[Exception] = []

    def _run() -> None:
        try:
            with (
                patch(_GARMIN_CLIENT, return_value=_make_mock_client()),
                patch("integrations.garmin_poller._now_utc", return_value=_NOW_UTC),
                patch("integrations.garmin_poller._write_history_entry"),
            ):
                poll_once(snapshot_path=snap_path)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, f"Concurrent write errors: {errors}"

    # File must still be valid JSON after concurrent writes
    data = json.loads(snap_path.read_text())
    assert "is_stale" in data
    assert "hourly_steps" in data


# ---------------------------------------------------------------------------
# Test 6 — midnight reset: hourly_steps clears on new IST date
# ---------------------------------------------------------------------------

def test_midnight_reset(tmp_path: Path) -> None:
    """When date_ist changes (midnight IST), hourly_steps resets to one entry."""
    snap_path = tmp_path / "health_today.json"

    # Simulate a snapshot from yesterday
    yesterday_utc = datetime.datetime(2026, 6, 28, 10, 0, 0, tzinfo=_UTC)
    yesterday_ist = "2026-06-28"

    old_hourly = [
        {"timestamp_utc": "2026-06-28T09:00:00+00:00", "steps_cumulative": 9000},
        {"timestamp_utc": "2026-06-28T10:00:00+00:00", "steps_cumulative": 9500},
    ]
    old_snapshot = {
        "fetched_at_utc": yesterday_utc.isoformat(),
        "date_ist": yesterday_ist,
        "is_stale": False,
        "stats": {"steps": 9500, "body_battery_high": 60, "calories": 2200, "stress_avg": 40},
        "sleep": None,
        "runs_today": None,
        "runs_available": False,
        "hourly_steps": old_hourly,
    }
    snap_path.write_text(json.dumps(old_snapshot))

    # Now poll on the new day (2026-06-29 UTC = 2026-06-29 IST)
    today_utc = datetime.datetime(2026, 6, 29, 11, 0, 0, tzinfo=_UTC)

    today_summary = {
        "date": "2026-06-29",
        "sleep": _SLEEP,
        "stats": {"steps": 500, "body_battery_high": 80, "calories": 200, "stress_avg": 25},
    }

    with (
        patch(_GARMIN_CLIENT, return_value=_make_mock_client(summary=today_summary, sleep=_SLEEP)),
        patch("integrations.garmin_poller._now_utc", return_value=today_utc),
        patch("integrations.garmin_poller._write_history_entry"),
    ):
        result = poll_once(snapshot_path=snap_path)

    # date_ist should reflect today
    assert result["date_ist"] == "2026-06-29"

    # hourly_steps must NOT contain yesterday's entries
    timestamps = [e["timestamp_utc"] for e in result["hourly_steps"]]
    for ts in timestamps:
        assert "2026-06-28" not in ts, f"Yesterday's timestamp leaked into new day: {ts}"

    # Only one entry (the new poll) should be present
    assert len(result["hourly_steps"]) == 1
    assert result["hourly_steps"][0]["steps_cumulative"] == 500


# ---------------------------------------------------------------------------
# Test 7 — hourly_steps rolls off after 24 entries
# ---------------------------------------------------------------------------

def test_hourly_steps_rolling_window(tmp_path: Path) -> None:
    """After 24 entries the oldest is evicted, keeping exactly 24."""
    snap_path = tmp_path / "health_today.json"

    # Pre-populate with 24 entries for today
    today_ist = _today_ist(_NOW_UTC)
    existing_hourly = [
        {
            "timestamp_utc": f"2026-06-29T{h:02d}:00:00+00:00",
            "steps_cumulative": h * 400,
        }
        for h in range(24)
    ]
    old_snapshot = {
        "fetched_at_utc": "2026-06-29T10:00:00+00:00",
        "date_ist": today_ist,
        "is_stale": False,
        "stats": {"steps": 9600, "body_battery_high": 80, "calories": 2000, "stress_avg": 30},
        "sleep": None,
        "runs_today": None,
        "runs_available": False,
        "hourly_steps": existing_hourly,
    }
    snap_path.write_text(json.dumps(old_snapshot))

    new_summary = {
        "date": today_ist,
        "sleep": None,
        "stats": {"steps": 10100, "body_battery_high": 75, "calories": 2100, "stress_avg": 31},
    }

    with (
        patch(_GARMIN_CLIENT, return_value=_make_mock_client(summary=new_summary, sleep=None)),
        patch("integrations.garmin_poller._now_utc", return_value=_NOW_UTC),
        patch("integrations.garmin_poller._write_history_entry"),
    ):
        result = poll_once(snapshot_path=snap_path)

    assert len(result["hourly_steps"]) == 24
    # Latest entry should have the new cumulative value
    assert result["hourly_steps"][-1]["steps_cumulative"] == 10100
