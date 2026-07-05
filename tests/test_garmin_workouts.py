"""
Tests for the /workouts endpoint in garmin_service.py.

Requires Flask to be importable (garminconnect is mocked away).
Run with: pytest tests/test_garmin_workouts.py -v
"""

import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure mac_services is on the path so garmin_service can be imported.
# ---------------------------------------------------------------------------
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_mac_services = os.path.join(_repo_root, "mac_services")
if _mac_services not in sys.path:
    sys.path.insert(0, _mac_services)

# ---------------------------------------------------------------------------
# Stub out garminconnect before importing garmin_service so the module can
# load even if the real package is not installed on the test machine.
# ---------------------------------------------------------------------------

_gc_stub = types.ModuleType("garminconnect")
_gc_stub.Garmin = MagicMock()
_gc_stub.GarminConnectAuthenticationError = type("GarminConnectAuthenticationError", (Exception,), {})
_gc_stub.GarminConnectTooManyRequestsError = type("GarminConnectTooManyRequestsError", (Exception,), {})
sys.modules.setdefault("garminconnect", _gc_stub)

# Now import the service (Flask path)
import garmin_service  # noqa: E402  (must come after stub)
from garmin_service import _parse_workouts, _parse_days_param  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """Return a Flask test client with a clean garmin-client state."""
    assert garmin_service.app is not None, "Flask app must be present for these tests"
    garmin_service._garmin_client = MagicMock()  # skip real login
    garmin_service.app.config["TESTING"] = True
    with garmin_service.app.test_client() as c:
        yield c


_SAMPLE_ACTIVITY = {
    "activityId": 987654321,
    "activityName": "Morning Run",
    "startTimeLocal": "2026-06-29 06:30:00",
    "distance": 10500.0,        # 10.5 km
    "duration": 3180.0,         # 53 min
    "averageSpeed": 3.302,      # ~5.05 min/km
    "activityType": {"typeKey": "running"},
}


# ---------------------------------------------------------------------------
# Unit tests for _parse_workouts helper
# ---------------------------------------------------------------------------

class TestParseWorkouts:
    def test_parses_single_activity_correctly(self):
        result = _parse_workouts([_SAMPLE_ACTIVITY])
        assert len(result) == 1
        w = result[0]
        assert w["activity_id"] == 987654321
        assert w["activity_name"] == "Morning Run"
        assert w["start_time_local"] == "2026-06-29 06:30:00"
        assert w["distance_km"] == 10.5
        assert w["duration_s"] == 3180
        assert isinstance(w["avg_pace_min_km"], float)
        assert w["is_run"] is True

    def test_zero_speed_yields_null_pace(self):
        activity = {**_SAMPLE_ACTIVITY, "averageSpeed": 0}
        result = _parse_workouts([activity])
        assert result[0]["avg_pace_min_km"] is None

    def test_missing_speed_yields_null_pace(self):
        activity = {k: v for k, v in _SAMPLE_ACTIVITY.items() if k != "averageSpeed"}
        result = _parse_workouts([activity])
        assert result[0]["avg_pace_min_km"] is None

    def test_empty_list_returns_empty(self):
        assert _parse_workouts([]) == []

    def test_none_input_returns_empty(self):
        assert _parse_workouts(None) == []

    def test_non_dict_entries_are_skipped(self):
        result = _parse_workouts(["not a dict", 42, None, _SAMPLE_ACTIVITY])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Unit tests for _parse_days_param
# ---------------------------------------------------------------------------

class TestParseDaysParam:
    def test_default_when_key_absent(self):
        assert _parse_days_param({}) == 7

    def test_custom_value(self):
        assert _parse_days_param({"days": "14"}) == 14

    def test_capped_at_max(self):
        assert _parse_days_param({"days": "999"}) == 30

    def test_minimum_is_one(self):
        assert _parse_days_param({"days": "0"}) == 1

    def test_invalid_string_returns_default(self):
        assert _parse_days_param({"days": "abc"}) == 7


# ---------------------------------------------------------------------------
# Integration tests via Flask test client
# ---------------------------------------------------------------------------

class TestWorkoutsEndpoint:
    def test_returns_workouts_list(self, client):
        with patch.object(garmin_service, "_call_garmin", return_value=[_SAMPLE_ACTIVITY]):
            resp = client.get("/workouts?days=7")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "workouts" in data
        assert "count" in data
        assert data["count"] == 1
        assert data["workouts"][0]["distance_km"] == 10.5
        assert data["workouts"][0]["is_run"] is True

    def test_empty_list_when_no_activities(self, client):
        with patch.object(garmin_service, "_call_garmin", return_value=[]):
            resp = client.get("/workouts?days=7")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["workouts"] == []
        assert data["count"] == 0

    def test_garmin_unavailable_returns_503(self, client):
        with patch.object(garmin_service, "_call_garmin", side_effect=RuntimeError("garmin_unavailable")):
            resp = client.get("/workouts?days=7")
        assert resp.status_code == 503
        data = json.loads(resp.data)
        assert data["error"] == "garmin_unavailable"

    def test_days_param_limits_date_range(self, client):
        """Verify that the start_date shifts correctly when days=3."""
        import datetime as dt
        captured_args = []

        def fake_call_garmin(fn, *args, **kwargs):
            captured_args.extend(args)
            return []

        with patch.object(garmin_service, "_call_garmin", side_effect=fake_call_garmin):
            resp = client.get("/workouts?days=3")

        assert resp.status_code == 200
        # args = (start_date, end_date) passed after fn
        start_date, end_date = captured_args[0], captured_args[1]
        start = dt.datetime.strptime(start_date, "%Y-%m-%d")
        end = dt.datetime.strptime(end_date, "%Y-%m-%d")
        assert (end - start).days == 2  # 3 days inclusive → diff of 2

    def test_zero_speed_activity_has_null_pace_in_response(self, client):
        activity = {**_SAMPLE_ACTIVITY, "averageSpeed": 0}
        with patch.object(garmin_service, "_call_garmin", return_value=[activity]):
            resp = client.get("/workouts")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["workouts"][0]["avg_pace_min_km"] is None

    def test_default_days_is_seven(self, client):
        """No days param → default 7 → start_date is 6 days before today."""
        import datetime as dt
        captured_args = []

        def fake_call_garmin(fn, *args, **kwargs):
            captured_args.extend(args)
            return []

        with patch.object(garmin_service, "_call_garmin", side_effect=fake_call_garmin):
            resp = client.get("/workouts")

        assert resp.status_code == 200
        start_date, end_date = captured_args[0], captured_args[1]
        start = dt.datetime.strptime(start_date, "%Y-%m-%d")
        end = dt.datetime.strptime(end_date, "%Y-%m-%d")
        assert (end - start).days == 6  # 7 days inclusive → diff of 6

    def test_multiple_activities_all_included(self, client):
        activities = [_SAMPLE_ACTIVITY, {**_SAMPLE_ACTIVITY, "activityId": 111, "distance": 5000.0}]
        with patch.object(garmin_service, "_call_garmin", return_value=activities):
            resp = client.get("/workouts?days=7")
        data = json.loads(resp.data)
        assert data["count"] == 2
        distances = {w["distance_km"] for w in data["workouts"]}
        assert distances == {10.5, 5.0}
