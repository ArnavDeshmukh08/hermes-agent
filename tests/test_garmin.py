"""Tests for integrations.garmin.GarminClient.

All 5 tests are fully offline — zero real network calls.
garminconnect need not be installed for these tests to pass.

sys.path wiring mirrors tests/test_memory_updater.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "hooks" / "jack_router"))

import pytest  # noqa: E402

from integrations.garmin import GarminClient  # noqa: E402

# ---------------------------------------------------------------------------
# Helper fake
# ---------------------------------------------------------------------------

def _make_sleep_response(
    sleep_secs: int = 22320,
    deep_secs: int = 3960,
    rem_secs: int = 0,
    score_value: int | None = 72,
) -> dict:
    """Build a realistic Garmin sleep-data response dict."""
    response: dict = {
        "dailySleepDTO": {
            "sleepTimeSeconds": sleep_secs,
            "deepSleepSeconds": deep_secs,
            "remSleepSeconds": rem_secs,
        },
    }
    if score_value is not None:
        response["sleepScores"] = {"overall": {"value": score_value}}
    return response


class FakeGarmin:
    """Minimal stand-in for garminconnect.Garmin."""

    def __init__(self, response: dict | None = None) -> None:
        self._response = response

    def get_sleep_data(self, date_str: str) -> dict | None:  # noqa: ARG002
        return self._response


# ---------------------------------------------------------------------------
# Test 1 — no credentials → everything returns None / ''
# ---------------------------------------------------------------------------

def test_no_creds_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no credentials configured, both methods degrade gracefully."""
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.delenv("GARMIN_PASSWORD", raising=False)

    # garminconnect must not have been imported by this test
    imported_before = "garminconnect" in sys.modules

    client = GarminClient()  # no args, no env vars
    assert client.last_night_sleep() is None
    assert client.sleep_summary_text() == ""

    # Verify garminconnect was never imported during this test
    if not imported_before:
        assert "garminconnect" not in sys.modules


# ---------------------------------------------------------------------------
# Test 2 — _login() returns None → both methods degrade
# ---------------------------------------------------------------------------

def test_login_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """When _login() returns None, both public methods return None/''."""
    client = GarminClient(email="x@example.com", password="secret")
    monkeypatch.setattr(client, "_login", lambda: None)

    assert client.last_night_sleep() is None
    assert client.sleep_summary_text() == ""


# ---------------------------------------------------------------------------
# Test 3 — happy path with realistic mock response
# ---------------------------------------------------------------------------

def test_sleep_parsed_from_mock_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """A realistic Garmin response is parsed correctly into the summary dict."""
    fake_garmin = FakeGarmin(response=_make_sleep_response())

    client = GarminClient(email="x@example.com", password="secret")
    monkeypatch.setattr(client, "_login", lambda: fake_garmin)

    result = client.last_night_sleep(day="2026-06-19")

    assert result is not None
    assert abs(result["total_sleep_h"] - 6.2) < 0.05   # 22320 / 3600 ≈ 6.2
    assert abs(result["deep_h"] - 1.1) < 0.05          # 3960 / 3600 ≈ 1.1
    assert result["score"] == 72

    text = client.sleep_summary_text(day="2026-06-19")
    assert "6.2h" in text
    assert "score 72" in text


# ---------------------------------------------------------------------------
# Test 4 — malformed / empty response → None, no exception
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_response", [{}, None])
def test_malformed_response_returns_none(
    monkeypatch: pytest.MonkeyPatch,
    bad_response: dict | None,
) -> None:
    """Empty or None response from the API yields None, never an exception."""
    fake_garmin = FakeGarmin(response=bad_response)

    client = GarminClient(email="x@example.com", password="secret")
    monkeypatch.setattr(client, "_login", lambda: fake_garmin)

    assert client.last_night_sleep() is None
    assert client.sleep_summary_text() == ""


# ---------------------------------------------------------------------------
# Test 5 — garminconnect ImportError is swallowed inside _login
# ---------------------------------------------------------------------------

def test_import_error_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """If garminconnect is not importable, _login() returns None gracefully."""
    # Setting sys.modules['garminconnect'] = None makes
    # `from garminconnect import Garmin` raise ImportError.
    monkeypatch.setitem(sys.modules, "garminconnect", None)  # type: ignore[arg-type]

    client = GarminClient(email="x@example.com", password="secret")
    # _login must catch the ImportError and return None
    assert client._login() is None
    assert client.last_night_sleep() is None
    assert client.sleep_summary_text() == ""
