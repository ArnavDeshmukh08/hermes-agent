"""Tests for integrations.daily_sync.DailySyncJob.

All tests are fully offline — no real Garmin, no real Mem0, no network.
Dependencies are injected via make_job().
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

import pytest  # noqa: E402

from integrations.daily_sync import DailySyncJob  # noqa: E402


# ---------------------------------------------------------------------------
# Shared factory
# ---------------------------------------------------------------------------

def make_job(
    sleep_data=None,
    stats_data=None,
    add_return=None,
    add_raises=None,
) -> DailySyncJob:
    """Build a DailySyncJob with fully injected mock clients."""
    mock_garmin = MagicMock()
    mock_garmin.last_night_sleep.return_value = sleep_data
    mock_garmin.get_stats.return_value = stats_data

    mock_memory = MagicMock()
    if add_raises:
        mock_memory.add.side_effect = add_raises
    else:
        mock_memory.add.return_value = add_return or {"id": "test"}

    return DailySyncJob(garmin_client=mock_garmin, memory_client=mock_memory)


_SAMPLE_SLEEP = {
    "total_sleep_h": 7.2,
    "deep_h": 1.1,
    "rem_h": 1.4,
    "score": 78,
}

_SAMPLE_STATS = {
    "steps": 8432,
    "body_battery_high": 82,
    "calories": 1920,
    "stress_avg": 28,
}


# ---------------------------------------------------------------------------
# sync_sleep tests
# ---------------------------------------------------------------------------

def test_sync_sleep_writes_memory_when_data_available():
    """When Garmin returns sleep data, add() is called with correct metadata."""
    job = make_job(sleep_data=_SAMPLE_SLEEP)
    result = job.sync_sleep(day="2026-06-22")

    assert result is True
    job._memory.add.assert_called_once()

    call_kwargs = job._memory.add.call_args
    # Positional first arg is the message string
    message = call_kwargs.args[0]
    assert "7.2h" in message
    assert "1.1h deep" in message
    assert "1.4h REM" in message
    assert "78" in message

    metadata = call_kwargs.kwargs["metadata"]
    assert metadata["source"] == "garmin"
    assert metadata["type"] == "sleep"
    assert metadata["date"] == "2026-06-22"
    assert call_kwargs.kwargs["user_id"] == "arnav"


def test_sync_sleep_omits_score_when_none():
    """Sleep message excludes the score clause when score is None."""
    data = {**_SAMPLE_SLEEP, "score": None}
    job = make_job(sleep_data=data)
    job.sync_sleep(day="2026-06-22")

    message = job._memory.add.call_args.args[0]
    assert "Sleep score" not in message
    assert "7.2h" in message


def test_sync_sleep_returns_false_when_no_data():
    """When Garmin returns None, sync_sleep returns False and add() is never called."""
    job = make_job(sleep_data=None)
    result = job.sync_sleep(day="2026-06-22")

    assert result is False
    job._memory.add.assert_not_called()


def test_sync_sleep_returns_false_when_memory_fails():
    """When add() raises ConnectionError, sync_sleep returns False without propagating."""
    job = make_job(sleep_data=_SAMPLE_SLEEP, add_raises=ConnectionError("Mac unreachable"))
    result = job.sync_sleep(day="2026-06-22")

    assert result is False
    job._memory.add.assert_called_once()  # was attempted


# ---------------------------------------------------------------------------
# sync_stats tests
# ---------------------------------------------------------------------------

def test_sync_stats_writes_memory_when_data_available():
    """When Garmin returns stats, add() is called with steps in the message."""
    job = make_job(stats_data=_SAMPLE_STATS)
    result = job.sync_stats(day="2026-06-22")

    assert result is True
    job._memory.add.assert_called_once()

    call_kwargs = job._memory.add.call_args
    message = call_kwargs.args[0]
    assert "8432 steps" in message
    assert "82%" in message
    assert "1920 kcal" in message

    metadata = call_kwargs.kwargs["metadata"]
    assert metadata["source"] == "garmin"
    assert metadata["type"] == "stats"
    assert metadata["date"] == "2026-06-22"


def test_sync_stats_returns_false_when_no_data():
    """When Garmin returns None for stats, sync_stats returns False."""
    job = make_job(stats_data=None)
    result = job.sync_stats(day="2026-06-22")

    assert result is False
    job._memory.add.assert_not_called()


def test_sync_stats_returns_false_when_get_stats_missing():
    """If get_stats attribute is absent on garmin client, sync_stats returns False."""
    mock_garmin = MagicMock(spec=[])  # spec=[] means no attributes allowed
    mock_memory = MagicMock()
    job = DailySyncJob(garmin_client=mock_garmin, memory_client=mock_memory)

    result = job.sync_stats(day="2026-06-22")

    assert result is False
    mock_memory.add.assert_not_called()


def test_sync_stats_returns_false_when_memory_fails():
    """When add() raises, sync_stats returns False without propagating."""
    job = make_job(stats_data=_SAMPLE_STATS, add_raises=ConnectionError("Mac unreachable"))
    result = job.sync_stats(day="2026-06-22")

    assert result is False
    job._memory.add.assert_called_once()


# ---------------------------------------------------------------------------
# sync_daily / run tests
# ---------------------------------------------------------------------------

def test_sync_daily_returns_both_results():
    """When both sleep and stats succeed, sync_daily returns {sleep: True, stats: True}."""
    job = make_job(sleep_data=_SAMPLE_SLEEP, stats_data=_SAMPLE_STATS)
    result = job.sync_daily()

    assert result == {"sleep": True, "stats": True}
    assert job._memory.add.call_count == 2


def test_sync_daily_partial_success_sleep_fails():
    """When sleep data is None but stats succeed, returns {sleep: False, stats: True}."""
    job = make_job(sleep_data=None, stats_data=_SAMPLE_STATS)
    result = job.sync_daily()

    assert result == {"sleep": False, "stats": True}
    assert job._memory.add.call_count == 1


def test_sync_daily_partial_success_stats_fails():
    """When sleep succeeds but stats return None, returns {sleep: True, stats: False}."""
    job = make_job(sleep_data=_SAMPLE_SLEEP, stats_data=None)
    result = job.sync_daily()

    assert result == {"sleep": True, "stats": False}
    assert job._memory.add.call_count == 1


def test_sync_daily_both_fail():
    """When both return None, returns {sleep: False, stats: False}."""
    job = make_job(sleep_data=None, stats_data=None)
    result = job.sync_daily()

    assert result == {"sleep": False, "stats": False}
    job._memory.add.assert_not_called()


def test_run_is_alias_for_sync_daily():
    """run() returns the same shape as sync_daily()."""
    job = make_job(sleep_data=_SAMPLE_SLEEP, stats_data=_SAMPLE_STATS)
    result = job.run()

    assert isinstance(result, dict)
    assert set(result.keys()) == {"sleep", "stats"}
    assert result["sleep"] is True
    assert result["stats"] is True
