"""Tests for integrations.garmin.GarminClient (HTTP-client edition).

All tests are fully offline — zero real network calls.
garminconnect must NOT be imported by the module under test.

sys.path wiring mirrors tests/test_memory_updater.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "hooks" / "jack_router"))

import pytest  # noqa: E402

from integrations.garmin import GarminClient  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Patch target: the module-level _get function that performs HTTP calls.
_GET = "integrations.garmin._get"


def _sleep_payload(
    total_sleep_h: float = 6.2,
    deep_h: float = 1.1,
    rem_h: float = 0.0,
    score: int | None = 72,
) -> dict:
    return {
        "total_sleep_h": total_sleep_h,
        "deep_h": deep_h,
        "rem_h": rem_h,
        "score": score,
    }


def _stats_payload() -> dict:
    return {
        "steps": 8421,
        "body_battery_high": 87,
        "calories": 2100,
        "stress_avg": 35,
    }


# ---------------------------------------------------------------------------
# Test 1 — happy path: sleep data parsed correctly
# ---------------------------------------------------------------------------

def test_sleep_parsed_correctly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A realistic Mac-service response is parsed into the expected dict."""
    monkeypatch.setenv("JACK_GARMIN_URL", "http://fake-mac:8765")

    with patch(_GET, return_value=_sleep_payload()):
        client = GarminClient()
        result = client.last_night_sleep(day="2026-06-19")

    assert result is not None
    assert abs(result["total_sleep_h"] - 6.2) < 0.05
    assert abs(result["deep_h"] - 1.1) < 0.05
    assert result["score"] == 72

    with patch(_GET, return_value=_sleep_payload()):
        text = client.sleep_summary_text(day="2026-06-19")

    assert "6.2h" in text
    assert "score 72" in text


# ---------------------------------------------------------------------------
# Test 2 — no score field → score is None, text has no "score" part
# ---------------------------------------------------------------------------

def test_sleep_no_score(monkeypatch: pytest.MonkeyPatch) -> None:
    """When score is None in the response the text omits the score part."""
    monkeypatch.setenv("JACK_GARMIN_URL", "http://fake-mac:8765")

    with patch(_GET, return_value=_sleep_payload(score=None)):
        client = GarminClient()
        result = client.last_night_sleep(day="2026-06-19")

    assert result is not None
    assert result["score"] is None

    with patch(_GET, return_value=_sleep_payload(score=None)):
        text = client.sleep_summary_text(day="2026-06-19")

    assert "score" not in text
    assert "h sleep" in text


# ---------------------------------------------------------------------------
# Test 3 — zero sleep → None (never "0.0h sleep" in briefing)
# ---------------------------------------------------------------------------

def test_zero_sleep_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A response with total_sleep_h == 0 must degrade to None."""
    monkeypatch.setenv("JACK_GARMIN_URL", "http://fake-mac:8765")

    with patch(_GET, return_value=_sleep_payload(total_sleep_h=0.0)):
        client = GarminClient()
        assert client.last_night_sleep() is None

    with patch(_GET, return_value=_sleep_payload(total_sleep_h=0.0)):
        assert client.sleep_summary_text() == ""


# ---------------------------------------------------------------------------
# Test 4 — {"error": "no_data"} → None
# ---------------------------------------------------------------------------

def test_last_night_sleep_returns_none_on_no_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Mac service says no_data, last_night_sleep returns None."""
    monkeypatch.setenv("JACK_GARMIN_URL", "http://fake-mac:8765")

    with patch(_GET, return_value={"error": "no_data"}):
        client = GarminClient()
        assert client.last_night_sleep(day="2026-06-19") is None

    with patch(_GET, return_value={"error": "no_data"}):
        assert client.sleep_summary_text(day="2026-06-19") == ""


# ---------------------------------------------------------------------------
# Test 5 — network timeout → None, no exception propagated
# ---------------------------------------------------------------------------

def test_last_night_sleep_returns_none_on_mac_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ConnectionError from _get is caught; returns None without raising."""
    monkeypatch.setenv("JACK_GARMIN_URL", "http://fake-mac:8765")

    with patch(_GET, side_effect=ConnectionError("Mac is down")):
        client = GarminClient()
        assert client.last_night_sleep(day="2026-06-19") is None

    with patch(_GET, side_effect=ConnectionError("Mac is down")):
        assert client.sleep_summary_text(day="2026-06-19") == ""


# ---------------------------------------------------------------------------
# Test 6 — get_stats returns steps
# ---------------------------------------------------------------------------

def test_get_stats_returns_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_stats parses the Mac-service stats payload correctly."""
    monkeypatch.setenv("JACK_GARMIN_URL", "http://fake-mac:8765")

    with patch(_GET, return_value=_stats_payload()):
        client = GarminClient()
        result = client.get_stats(day="2026-06-19")

    assert result is not None
    assert result["steps"] == 8421
    assert result["body_battery_high"] == 87


# ---------------------------------------------------------------------------
# Test 7 — get_stats network failure → None
# ---------------------------------------------------------------------------

def test_get_stats_returns_none_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_stats returns None on any network error, never raises."""
    monkeypatch.setenv("JACK_GARMIN_URL", "http://fake-mac:8765")

    with patch(_GET, side_effect=TimeoutError("timed out")):
        client = GarminClient()
        assert client.get_stats(day="2026-06-19") is None


# ---------------------------------------------------------------------------
# Test 8 — get_daily_summary returns date and nested dicts
# ---------------------------------------------------------------------------

def test_get_daily_summary_returns_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_daily_summary returns the full nested structure from the Mac service."""
    monkeypatch.setenv("JACK_GARMIN_URL", "http://fake-mac:8765")

    payload = {
        "date": "2026-06-19",
        "sleep": _sleep_payload(),
        "stats": _stats_payload(),
    }

    with patch(_GET, return_value=payload):
        client = GarminClient()
        result = client.get_daily_summary()

    assert result is not None
    assert result["date"] == "2026-06-19"
    assert result["sleep"]["total_sleep_h"] == 6.2
    assert result["stats"]["steps"] == 8421


# ---------------------------------------------------------------------------
# Test 9 — get_daily_summary network failure → None
# ---------------------------------------------------------------------------

def test_get_daily_summary_returns_none_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_daily_summary returns None on any network error, never raises."""
    monkeypatch.setenv("JACK_GARMIN_URL", "http://fake-mac:8765")

    with patch(_GET, side_effect=ConnectionError("unreachable")):
        client = GarminClient()
        assert client.get_daily_summary() is None


# ---------------------------------------------------------------------------
# Test 10 — garminconnect must NOT appear as an import in the module source
# ---------------------------------------------------------------------------

def test_no_garminconnect_import() -> None:
    """The integrations.garmin module must not import garminconnect at all."""
    import ast

    garmin_path = Path(_REPO_ROOT) / "integrations" / "garmin.py"
    source = garmin_path.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                names = [node.module or ""]
            for name in names:
                assert "garminconnect" not in (name or ""), (
                    f"garminconnect import found in integrations/garmin.py: {ast.dump(node)}"
                )
