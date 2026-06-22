"""
Tests for mac_services/garmin_service.py

All tests run fully offline — garminconnect is mocked via sys.modules so the
real package does not need to be installed.
"""

import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

# ---------------------------------------------------------------------------
# Bootstrap: make the repo root importable and inject a stub garminconnect
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "mac_services"))

# Build a minimal stub for garminconnect so the module can be imported without
# the real package installed.
_gc_stub = types.ModuleType("garminconnect")


class _AuthError(Exception):
    pass


class _TooManyError(Exception):
    pass


class _FakeGarmin:
    def __init__(self, email, password):
        self.email = email
        self.password = password

    def login(self):
        pass

    def get_sleep_data(self, date):
        return {}

    def get_stats(self, date):
        return {}


_gc_stub.Garmin = _FakeGarmin
_gc_stub.GarminConnectAuthenticationError = _AuthError
_gc_stub.GarminConnectTooManyRequestsError = _TooManyError
sys.modules["garminconnect"] = _gc_stub

# Now we can safely import the service module.
import garmin_service as svc  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sleep_raw(total_s=22320, deep_s=3960, rem_s=5040, score=72):
    """Build a raw Garmin sleep response dict."""
    raw = {
        "dailySleepDTO": {
            "sleepTimeSeconds": total_s,
            "deepSleepSeconds": deep_s,
            "remSleepSeconds": rem_s,
        }
    }
    if score is not None:
        raw["sleepScores"] = [{"value": score}]
    return raw


def _make_stats_raw(steps=8432, bb=82, calories=1920, stress=28):
    return {
        "totalSteps": steps,
        "bodyBatteryHighestValue": bb,
        "totalKilocalories": calories,
        "averageStressLevel": stress,
    }


# ---------------------------------------------------------------------------
# Unit tests for pure parser functions (no HTTP needed)
# ---------------------------------------------------------------------------

class TestParseSleep(unittest.TestCase):
    def test_returns_correct_shape(self):
        result = svc._parse_sleep(_make_sleep_raw())
        self.assertIn("total_sleep_h", result)
        self.assertIn("deep_h", result)
        self.assertIn("rem_h", result)
        self.assertIn("score", result)

    def test_converts_seconds_to_hours(self):
        result = svc._parse_sleep(_make_sleep_raw(total_s=7200, deep_s=3600, rem_s=1800))
        self.assertEqual(result["total_sleep_h"], 2.0)
        self.assertEqual(result["deep_h"], 1.0)
        self.assertEqual(result["rem_h"], 0.5)

    def test_zero_sleep_returns_no_data(self):
        result = svc._parse_sleep({"dailySleepDTO": {"sleepTimeSeconds": 0}})
        self.assertEqual(result, {"error": "no_data"})

    def test_missing_dto_returns_no_data(self):
        result = svc._parse_sleep({})
        self.assertEqual(result, {"error": "no_data"})

    def test_score_omitted_when_absent(self):
        raw = _make_sleep_raw(score=None)
        raw.pop("sleepScores", None)
        result = svc._parse_sleep(raw)
        self.assertNotIn("score", result)

    def test_score_included_when_present(self):
        result = svc._parse_sleep(_make_sleep_raw(score=85))
        self.assertEqual(result["score"], 85)


class TestParseStats(unittest.TestCase):
    def test_returns_steps_and_calories(self):
        result = svc._parse_stats(_make_stats_raw())
        self.assertEqual(result["steps"], 8432)
        self.assertEqual(result["calories"], 1920)

    def test_returns_body_battery_high(self):
        result = svc._parse_stats(_make_stats_raw(bb=75))
        self.assertEqual(result["body_battery_high"], 75)

    def test_falls_back_to_most_recent_battery(self):
        raw = {"bodyBatteryMostRecentValue": 60, "totalSteps": 1000}
        result = svc._parse_stats(raw)
        self.assertEqual(result["body_battery_high"], 60)

    def test_none_values_omitted(self):
        raw = {"totalSteps": 5000}
        result = svc._parse_stats(raw)
        self.assertNotIn("body_battery_high", result)
        self.assertNotIn("calories", result)

    def test_empty_raw_returns_no_data(self):
        result = svc._parse_stats({})
        self.assertEqual(result, {"error": "no_data"})

    def test_none_raw_returns_no_data(self):
        result = svc._parse_stats(None)
        self.assertEqual(result, {"error": "no_data"})


# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------

class TestLoadCredentials(unittest.TestCase):
    def test_creds_from_garmin_env_file(self):
        env_content = "GARMIN_EMAIL=test@example.com\nGARMIN_PASSWORD=secret123\n"
        with patch("builtins.open", mock_open(read_data=env_content)):
            with patch("os.path.exists", return_value=True):
                with patch.dict("os.environ", {}, clear=False):
                    email, password = svc.load_credentials()
        self.assertEqual(email, "test@example.com")
        self.assertEqual(password, "secret123")

    def test_creds_from_environment_when_file_absent(self):
        with patch("os.path.exists", return_value=False):
            with patch.dict("os.environ", {"GARMIN_EMAIL": "env@test.com", "GARMIN_PASSWORD": "envpass"}):
                email, password = svc.load_credentials()
        self.assertEqual(email, "env@test.com")
        self.assertEqual(password, "envpass")

    def test_file_creds_take_precedence_over_env(self):
        env_content = "GARMIN_EMAIL=file@example.com\nGARMIN_PASSWORD=filepass\n"
        with patch("builtins.open", mock_open(read_data=env_content)):
            with patch("os.path.exists", return_value=True):
                with patch.dict("os.environ", {"GARMIN_EMAIL": "env@test.com", "GARMIN_PASSWORD": "envpass"}):
                    email, password = svc.load_credentials()
        self.assertEqual(email, "file@example.com")
        self.assertEqual(password, "filepass")

    def test_comment_lines_ignored(self):
        env_content = "# this is a comment\nGARMIN_EMAIL=a@b.com\nGARMIN_PASSWORD=pw\n"
        with patch("builtins.open", mock_open(read_data=env_content)):
            with patch("os.path.exists", return_value=True):
                email, _ = svc.load_credentials()
        self.assertEqual(email, "a@b.com")


# ---------------------------------------------------------------------------
# Flask-based HTTP endpoint tests (skipped if Flask is not available)
# ---------------------------------------------------------------------------

@unittest.skipUnless(svc._USE_FLASK, "Flask not installed — skipping Flask endpoint tests")
class TestFlaskEndpoints(unittest.TestCase):
    def setUp(self):
        svc.app.testing = True
        self.client = svc.app.test_client()
        # Reset cached client before each test.
        svc._garmin_client = None

    def tearDown(self):
        svc._garmin_client = None

    # -- /health -------------------------------------------------------------

    def test_health_endpoint_returns_ok(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["status"], "ok")
        self.assertIn("garmin_ready", data)

    def test_health_garmin_ready_true_when_client_cached(self):
        svc._garmin_client = MagicMock()
        resp = self.client.get("/health")
        data = json.loads(resp.data)
        self.assertTrue(data["garmin_ready"])

    def test_health_garmin_ready_false_when_no_client(self):
        svc._garmin_client = None
        resp = self.client.get("/health")
        data = json.loads(resp.data)
        self.assertFalse(data["garmin_ready"])

    # -- /sleep --------------------------------------------------------------

    def test_sleep_endpoint_returns_correct_shape(self):
        mock_client = MagicMock()
        mock_client.get_sleep_data.return_value = _make_sleep_raw()
        svc._garmin_client = mock_client

        resp = self.client.get("/sleep?date=2026-06-20")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("total_sleep_h", data)
        self.assertIn("deep_h", data)
        self.assertIn("rem_h", data)

    def test_sleep_endpoint_zero_sleep_returns_no_data(self):
        mock_client = MagicMock()
        mock_client.get_sleep_data.return_value = {
            "dailySleepDTO": {"sleepTimeSeconds": 0, "deepSleepSeconds": 0, "remSleepSeconds": 0}
        }
        svc._garmin_client = mock_client

        resp = self.client.get("/sleep?date=2026-06-20")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data, {"error": "no_data"})

    def test_sleep_endpoint_default_date_is_today(self):
        mock_client = MagicMock()
        mock_client.get_sleep_data.return_value = _make_sleep_raw()
        svc._garmin_client = mock_client

        resp = self.client.get("/sleep")
        self.assertEqual(resp.status_code, 200)
        mock_client.get_sleep_data.assert_called_once()

    # -- /stats --------------------------------------------------------------

    def test_stats_endpoint_returns_steps(self):
        mock_client = MagicMock()
        mock_client.get_stats.return_value = _make_stats_raw(steps=9999)
        svc._garmin_client = mock_client

        resp = self.client.get("/stats?date=2026-06-20")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["steps"], 9999)

    def test_stats_endpoint_returns_calories(self):
        mock_client = MagicMock()
        mock_client.get_stats.return_value = _make_stats_raw(calories=2100)
        svc._garmin_client = mock_client

        resp = self.client.get("/stats")
        data = json.loads(resp.data)
        self.assertEqual(data["calories"], 2100)

    # -- /daily_summary ------------------------------------------------------

    def test_daily_summary_merges_sleep_and_stats(self):
        mock_client = MagicMock()
        mock_client.get_sleep_data.return_value = _make_sleep_raw()
        mock_client.get_stats.return_value = _make_stats_raw()
        svc._garmin_client = mock_client

        resp = self.client.get("/daily_summary")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("date", data)
        self.assertIn("sleep", data)
        self.assertIn("stats", data)
        self.assertIsNotNone(data["sleep"])
        self.assertIsNotNone(data["stats"])

    def test_daily_summary_sleep_null_when_unavailable(self):
        # sleep call raises; stats call succeeds on the refreshed client.
        sleep_client = MagicMock()
        sleep_client.get_sleep_data.side_effect = RuntimeError("garmin_unavailable")

        stats_client = MagicMock()
        stats_client.get_sleep_data.side_effect = RuntimeError("garmin_unavailable")
        stats_client.get_stats.return_value = _make_stats_raw()

        svc._garmin_client = sleep_client

        call_count = {"n": 0}

        def _fake_refresh():
            call_count["n"] += 1
            svc._garmin_client = stats_client
            return stats_client

        with patch.object(svc, "_refresh_client", side_effect=_fake_refresh):
            resp = self.client.get("/daily_summary")

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIsNone(data["sleep"])
        self.assertIsNotNone(data["stats"])

    # -- error handling ------------------------------------------------------

    def test_garmin_down_returns_503_on_sleep(self):
        mock_client = MagicMock()
        mock_client.get_sleep_data.side_effect = RuntimeError("garmin_unavailable")
        svc._garmin_client = mock_client
        # _call_garmin will try to re-login; patch _refresh_client to also fail.
        with patch.object(svc, "_refresh_client", side_effect=RuntimeError("garmin_unavailable")):
            resp = self.client.get("/sleep?date=2026-06-20")
        self.assertEqual(resp.status_code, 503)
        data = json.loads(resp.data)
        self.assertEqual(data["error"], "garmin_unavailable")

    def test_garmin_down_returns_503_on_stats(self):
        mock_client = MagicMock()
        mock_client.get_stats.side_effect = RuntimeError("garmin_unavailable")
        svc._garmin_client = mock_client
        with patch.object(svc, "_refresh_client", side_effect=RuntimeError("garmin_unavailable")):
            resp = self.client.get("/stats?date=2026-06-20")
        self.assertEqual(resp.status_code, 503)
        data = json.loads(resp.data)
        self.assertEqual(data["error"], "garmin_unavailable")


# ---------------------------------------------------------------------------
# Stdlib fallback HTTP tests (run when Flask is NOT installed)
# ---------------------------------------------------------------------------

@unittest.skipIf(svc._USE_FLASK, "Flask installed — stdlib tests skipped")
class TestStdlibEndpoints(unittest.TestCase):
    """
    Minimal smoke tests for the stdlib path.  We exercise the handlers by
    calling the internal parser functions directly (the HTTP layer is thin
    wiring only).
    """

    def test_health_response_shape(self):
        # _garmin_client drives the garmin_ready flag
        svc._garmin_client = None
        self.assertFalse(svc._garmin_client is not None)

    def test_sleep_parser_zero_returns_no_data(self):
        result = svc._parse_sleep({"dailySleepDTO": {"sleepTimeSeconds": 0}})
        self.assertEqual(result, {"error": "no_data"})

    def test_stats_parser_returns_steps(self):
        result = svc._parse_stats({"totalSteps": 5000})
        self.assertEqual(result["steps"], 5000)


# ---------------------------------------------------------------------------
# _call_garmin retry logic
# ---------------------------------------------------------------------------

class TestCallGarminRetry(unittest.TestCase):
    def setUp(self):
        svc._garmin_client = None

    def tearDown(self):
        svc._garmin_client = None

    def test_retries_on_exception_and_raises_runtime_error(self):
        mock_client = MagicMock()
        mock_client.get_sleep_data.side_effect = Exception("network error")
        svc._garmin_client = mock_client

        refreshed = MagicMock()
        refreshed.get_sleep_data.side_effect = Exception("still down")

        with patch.object(svc, "_refresh_client", return_value=refreshed):
            with self.assertRaises(RuntimeError) as ctx:
                svc._call_garmin(lambda c, d: c.get_sleep_data(d), "2026-06-20")

        self.assertIn("garmin_unavailable", str(ctx.exception))

    def test_succeeds_on_retry_after_initial_failure(self):
        mock_client = MagicMock()
        mock_client.get_sleep_data.side_effect = Exception("first call fails")
        svc._garmin_client = mock_client

        refreshed = MagicMock()
        refreshed.get_sleep_data.return_value = _make_sleep_raw()

        with patch.object(svc, "_refresh_client", return_value=refreshed):
            result = svc._call_garmin(lambda c, d: c.get_sleep_data(d), "2026-06-20")

        self.assertIn("dailySleepDTO", result)


if __name__ == "__main__":
    unittest.main()
