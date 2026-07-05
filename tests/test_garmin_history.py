"""Tests for run-fetching and 14-day history in garmin.py / garmin_poller.py.

All tests are fully offline — no real network calls or file-system side effects
beyond the pytest tmp_path fixture.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "hooks" / "jack_router"))

import pytest  # noqa: E402

from integrations.garmin import GarminClient  # noqa: E402
from integrations.garmin_poller import (  # noqa: E402
    _build_snapshot,
    _history_path,
    _write_history_entry,
    days_since_last_run,
    runs_this_week,
)

_UTC = datetime.timezone.utc
_NOW_UTC = datetime.datetime(2026, 6, 29, 11, 0, 0, tzinfo=_UTC)
_DATE_IST = "2026-06-29"

_SAMPLE_WORKOUTS = [
    {
        "activity_id": 123456,
        "activity_name": "Running",
        "start_time_local": "2026-06-29 11:00:00",
        "distance_km": 5.23,
        "duration_s": 1850,
        "avg_pace_min_km": 5.89,
        "is_run": True,
    }
]

_SUMMARY = {
    "date": "2026-06-29",
    "sleep": {
        "total_sleep_h": 6.5,
        "deep_h": 1.2,
        "rem_h": 1.5,
        "score": 70,
    },
    "stats": {
        "steps": 8000,
        "body_battery_high": 80,
        "calories": 1800,
        "stress_avg": 30,
    },
}

_SLEEP = {
    "total_sleep_h": 6.5,
    "deep_h": 1.2,
    "rem_h": 1.5,
    "score": 70,
}


# ---------------------------------------------------------------------------
# 1. GarminClient.get_recent_runs — success
# ---------------------------------------------------------------------------

def test_get_recent_runs_success() -> None:
    """get_recent_runs() returns parsed list when Mac responds correctly."""
    mock_response = {"workouts": _SAMPLE_WORKOUTS, "count": 1}

    with patch("integrations.garmin._get", return_value=mock_response):
        client = GarminClient()
        result = client.get_recent_runs(days=1)

    assert result is not None
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["distance_km"] == 5.23
    assert result[0]["avg_pace_min_km"] == 5.89


# ---------------------------------------------------------------------------
# 2. GarminClient.get_recent_runs — Mac unreachable
# ---------------------------------------------------------------------------

def test_get_recent_runs_mac_down() -> None:
    """get_recent_runs() returns None when Mac is unreachable (raises)."""
    with patch("integrations.garmin._get", side_effect=ConnectionError("Mac down")):
        client = GarminClient()
        result = client.get_recent_runs(days=1)

    assert result is None


# ---------------------------------------------------------------------------
# 3. GarminClient.get_recent_runs — empty list (no workouts)
# ---------------------------------------------------------------------------

def test_get_recent_runs_empty() -> None:
    """get_recent_runs() returns empty list when no workouts exist."""
    mock_response = {"workouts": [], "count": 0}

    with patch("integrations.garmin._get", return_value=mock_response):
        client = GarminClient()
        result = client.get_recent_runs(days=1)

    assert result == []


# ---------------------------------------------------------------------------
# 4. _build_snapshot() — with runs provided
# ---------------------------------------------------------------------------

def test_build_snapshot_with_runs() -> None:
    """Snapshot includes runs_today and runs_available=True when runs provided."""
    snapshot = _build_snapshot(
        summary=_SUMMARY,
        sleep=_SLEEP,
        prev=None,
        now=_NOW_UTC,
        runs=_SAMPLE_WORKOUTS,
    )

    assert snapshot["runs_available"] is True
    assert snapshot["runs_today"] == _SAMPLE_WORKOUTS
    assert len(snapshot["runs_today"]) == 1


# ---------------------------------------------------------------------------
# 5. _build_snapshot() — Mac down (runs=None), no prev
# ---------------------------------------------------------------------------

def test_build_snapshot_no_runs_mac_down() -> None:
    """When runs=None (Mac down) and no prev snapshot, runs_today is None."""
    snapshot = _build_snapshot(
        summary=_SUMMARY,
        sleep=_SLEEP,
        prev=None,
        now=_NOW_UTC,
        runs=None,
    )

    assert snapshot["runs_available"] is False
    assert snapshot["runs_today"] is None


# ---------------------------------------------------------------------------
# 6. _build_snapshot() — Mac down but prev has run data (fallback)
# ---------------------------------------------------------------------------

def test_build_snapshot_falls_back_to_prev_runs() -> None:
    """When runs=None and prev has runs_today, falls back to prev runs."""
    prev = {
        "date_ist": _DATE_IST,
        "runs_today": _SAMPLE_WORKOUTS,
        "runs_available": True,
        "hourly_steps": [],
    }
    snapshot = _build_snapshot(
        summary=_SUMMARY,
        sleep=_SLEEP,
        prev=prev,
        now=_NOW_UTC,
        runs=None,
    )

    # runs_available is False (Mac was down this poll), but runs_today falls back
    assert snapshot["runs_available"] is False
    assert snapshot["runs_today"] == _SAMPLE_WORKOUTS


# ---------------------------------------------------------------------------
# 7. _write_history_entry() — creates new file
# ---------------------------------------------------------------------------

def test_write_history_entry_creates_file(tmp_path: Path) -> None:
    """_write_history_entry() creates health_history.json with today's entry."""
    snapshot = {
        "date_ist": _DATE_IST,
        "stats": {"steps": 8000},
        "sleep": {"total_sleep_h": 6.5},
        "runs_today": _SAMPLE_WORKOUTS,
        "runs_available": True,
        "hourly_steps": [],
    }
    hist_path = tmp_path / "health_history.json"

    with patch("integrations.garmin_poller._history_path", return_value=hist_path):
        _write_history_entry(snapshot)

    assert hist_path.exists()
    history = json.loads(hist_path.read_text())
    assert isinstance(history, list)
    assert len(history) == 1
    entry = history[0]
    assert entry["date_ist"] == _DATE_IST
    assert entry["steps"] == 8000
    assert entry["sleep_h"] == 6.5
    assert entry["runs"] is not None
    assert entry["runs"]["count"] == 1
    assert entry["runs"]["total_km"] == 5.23


# ---------------------------------------------------------------------------
# 8. _write_history_entry() — replaces existing entry on re-poll
# ---------------------------------------------------------------------------

def test_write_history_entry_updates_existing(tmp_path: Path) -> None:
    """Re-polling the same day replaces the existing entry, no duplicates."""
    hist_path = tmp_path / "health_history.json"

    # First write
    snapshot1 = {
        "date_ist": _DATE_IST,
        "stats": {"steps": 5000},
        "sleep": {"total_sleep_h": 6.0},
        "runs_today": [],
        "runs_available": True,
        "hourly_steps": [],
    }
    with patch("integrations.garmin_poller._history_path", return_value=hist_path):
        _write_history_entry(snapshot1)

    # Second write (same day, updated steps)
    snapshot2 = {
        "date_ist": _DATE_IST,
        "stats": {"steps": 10000},
        "sleep": {"total_sleep_h": 6.5},
        "runs_today": _SAMPLE_WORKOUTS,
        "runs_available": True,
        "hourly_steps": [],
    }
    with patch("integrations.garmin_poller._history_path", return_value=hist_path):
        _write_history_entry(snapshot2)

    history = json.loads(hist_path.read_text())
    assert len(history) == 1  # no duplicates
    assert history[0]["steps"] == 10000  # updated value
    assert history[0]["runs"]["count"] == 1  # run present


# ---------------------------------------------------------------------------
# 9. _write_history_entry() — prunes to 14 days
# ---------------------------------------------------------------------------

def test_write_history_entry_prunes_to_14_days(tmp_path: Path) -> None:
    """History is pruned to the 14 most recent entries."""
    hist_path = tmp_path / "health_history.json"

    # Pre-populate with 14 old entries
    old_entries = [
        {
            "date_ist": f"2026-06-{d:02d}",
            "steps": 7000,
            "sleep_h": 6.0,
            "runs": None,
            "sedentary": False,
        }
        for d in range(1, 15)  # 2026-06-01 through 2026-06-14
    ]
    hist_path.write_text(json.dumps(old_entries))

    # Add today's entry (2026-06-29) — should push out the oldest
    snapshot = {
        "date_ist": _DATE_IST,
        "stats": {"steps": 8000},
        "sleep": {"total_sleep_h": 6.5},
        "runs_today": [],
        "runs_available": True,
        "hourly_steps": [],
    }
    with patch("integrations.garmin_poller._history_path", return_value=hist_path):
        _write_history_entry(snapshot)

    history = json.loads(hist_path.read_text())
    assert len(history) == 14  # capped at 14
    dates = [e["date_ist"] for e in history]
    assert _DATE_IST in dates  # today is present
    assert "2026-06-01" not in dates  # oldest was pruned


# ---------------------------------------------------------------------------
# 10. _write_history_entry() — writes runs=null when Mac is down
# ---------------------------------------------------------------------------

def test_write_history_entry_null_runs_when_mac_down(tmp_path: Path) -> None:
    """When runs_available=False (Mac down), history entry has runs=null."""
    hist_path = tmp_path / "health_history.json"

    snapshot = {
        "date_ist": _DATE_IST,
        "stats": {"steps": 3000},
        "sleep": {"total_sleep_h": 7.0},
        "runs_today": None,
        "runs_available": False,
        "hourly_steps": [],
    }
    with patch("integrations.garmin_poller._history_path", return_value=hist_path):
        _write_history_entry(snapshot)

    history = json.loads(hist_path.read_text())
    assert len(history) == 1
    assert history[0]["runs"] is None  # not fake zeros


# ---------------------------------------------------------------------------
# 11. days_since_last_run() — today has a run (returns 0)
# ---------------------------------------------------------------------------

def test_days_since_last_run_today() -> None:
    """Returns 0 when today's history entry has a run."""
    history = [
        {
            "date_ist": "2026-06-29",
            "runs": {"count": 1, "total_km": 5.23, "entries": [{"km": 5.23, "pace": "5.89"}]},
        }
    ]
    assert days_since_last_run(history) == 0


# ---------------------------------------------------------------------------
# 12. days_since_last_run() — yesterday had a run (returns 1)
# ---------------------------------------------------------------------------

def test_days_since_last_run_yesterday() -> None:
    """Returns 1 when the last run was yesterday."""
    history = [
        {
            "date_ist": "2026-06-28",
            "runs": {"count": 1, "total_km": 4.5, "entries": [{"km": 4.5, "pace": "6.1"}]},
        },
        {
            "date_ist": "2026-06-29",
            "runs": {"count": 0, "total_km": 0, "entries": []},
        },
    ]
    assert days_since_last_run(history) == 1


# ---------------------------------------------------------------------------
# 13. days_since_last_run() — no run in history (returns None)
# ---------------------------------------------------------------------------

def test_days_since_last_run_none() -> None:
    """Returns None when no run entry found in history."""
    history = [
        {"date_ist": "2026-06-27", "runs": {"count": 0, "total_km": 0, "entries": []}},
        {"date_ist": "2026-06-28", "runs": {"count": 0, "total_km": 0, "entries": []}},
        {"date_ist": "2026-06-29", "runs": None},  # stale day
    ]
    assert days_since_last_run(history) is None


# ---------------------------------------------------------------------------
# 14. runs_this_week() — correct count across multiple days
# ---------------------------------------------------------------------------

def test_runs_this_week_count() -> None:
    """Correctly counts days with at least one run in the last 7 days."""
    today = datetime.date(2026, 6, 29)

    history = [
        # Days within the 7-day window
        {"date_ist": "2026-06-23", "runs": {"count": 1, "total_km": 5.0, "entries": []}},
        {"date_ist": "2026-06-24", "runs": {"count": 0, "total_km": 0, "entries": []}},
        {"date_ist": "2026-06-25", "runs": {"count": 1, "total_km": 6.0, "entries": []}},
        {"date_ist": "2026-06-26", "runs": {"count": 0, "total_km": 0, "entries": []}},
        {"date_ist": "2026-06-27", "runs": {"count": 1, "total_km": 7.0, "entries": []}},
        {"date_ist": "2026-06-28", "runs": {"count": 0, "total_km": 0, "entries": []}},
        {"date_ist": "2026-06-29", "runs": {"count": 1, "total_km": 5.23, "entries": []}},
        # Outside 7-day window — should NOT count
        {"date_ist": "2026-06-20", "runs": {"count": 1, "total_km": 8.0, "entries": []}},
    ]

    _IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    mock_now = datetime.datetime(2026, 6, 29, 11, 0, 0, tzinfo=datetime.timezone.utc)

    with patch("integrations.garmin_poller.datetime") as mock_dt:
        mock_dt.datetime.now.return_value = mock_now.astimezone(_IST)
        mock_dt.datetime.now.return_value = mock_now.replace(tzinfo=None).replace(
            tzinfo=_IST
        )
        mock_dt.timedelta = datetime.timedelta
        mock_dt.timezone = datetime.timezone
        # Use real implementation to get the cutoff right
        result = runs_this_week(history)

    # 4 runs in the 7-day window (Jun 23, 25, 27, 29)
    assert result == 4


# ---------------------------------------------------------------------------
# 15. runs_this_week() — stale days (runs=null) not counted
# ---------------------------------------------------------------------------

def test_runs_this_week_skips_stale() -> None:
    """Stale days (runs=null) are not counted as run days or rest days."""
    history = [
        {"date_ist": "2026-06-23", "runs": None},  # stale — skip
        {"date_ist": "2026-06-24", "runs": {"count": 1, "total_km": 5.0, "entries": []}},
        {"date_ist": "2026-06-25", "runs": None},  # stale — skip
        {"date_ist": "2026-06-26", "runs": {"count": 0, "total_km": 0, "entries": []}},
        {"date_ist": "2026-06-27", "runs": None},  # stale — skip
        {"date_ist": "2026-06-28", "runs": {"count": 1, "total_km": 6.0, "entries": []}},
        {"date_ist": "2026-06-29", "runs": None},  # stale — skip
    ]
    result = runs_this_week(history)
    # Only 2 non-stale days with runs (Jun 24 and Jun 28)
    assert result == 2
