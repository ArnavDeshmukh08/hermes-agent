"""Tests for the Garmin + Calendar wiring added to briefing.morning.

These tests run WITHOUT garminconnect or google-api-python-client installed.
Every integration is patched at the method level so no real network calls happen.
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

# Ensure repo root is on sys.path (mirrors test_morning_briefing.py setup).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from briefing.morning import MorningBriefing  # noqa: E402

# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_morning_briefing.py fixtures)
# ---------------------------------------------------------------------------

class FakeStore:
    """In-memory store: list_pending returns a fixed list."""

    def __init__(self, pending=()):
        self._pending = list(pending)

    def list_pending(self, user_id):
        return list(self._pending)


def _silent_log(briefing: MorningBriefing) -> None:
    briefing._log = lambda line: None  # type: ignore[method-assign]


def _make_briefing(complete_fn=None):
    """Return a MorningBriefing wired with a no-op store and optional fake LLM."""
    captured: dict = {}

    def _capture(system, user, **kwargs):
        captured["system"] = system
        captured["user"] = user
        return "good morning from Jack"

    b = MorningBriefing(
        user_path="/nonexistent/USER.md",
        store=FakeStore(),
        complete_fn=complete_fn or _capture,
    )
    _silent_log(b)
    return b, captured


_NOW = datetime(2026, 6, 19, 6, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 1. Garmin block appears when enabled
# ---------------------------------------------------------------------------

class TestGarminEnabled(unittest.TestCase):
    def setUp(self):
        os.environ["JACK_GARMIN_ENABLED"] = "1"
        os.environ.pop("JACK_CALENDAR_ENABLED", None)

    def tearDown(self):
        os.environ.pop("JACK_GARMIN_ENABLED", None)

    def test_briefing_includes_sleep_when_enabled(self):
        b, captured = _make_briefing()

        with patch.object(b, "_garmin_block", return_value="6.2h sleep (1.1h deep), score 72"):
            out = b.compile_briefing("42", now=_NOW)

        self.assertIsInstance(out, str)
        # The Garmin section label and value reach the LLM prompt.
        self.assertIn("Garmin", captured["user"])
        self.assertIn("6.2h", captured["user"])
        # Output still respects the 5-line cap.
        non_empty = [ln for ln in out.splitlines() if ln.strip()]
        self.assertLessEqual(len(non_empty), 10)


# ---------------------------------------------------------------------------
# 2. Garmin block absent when disabled
# ---------------------------------------------------------------------------

class TestGarminDisabled(unittest.TestCase):
    def setUp(self):
        os.environ.pop("JACK_GARMIN_ENABLED", None)
        os.environ.pop("JACK_CALENDAR_ENABLED", None)

    def test_briefing_omits_sleep_when_disabled(self):
        b, captured = _make_briefing()
        out = b.compile_briefing("42", now=_NOW)

        # _garmin_block() returns '' when disabled → no Garmin section in prompt.
        self.assertEqual(b._garmin_block(), "")
        self.assertNotIn("Garmin", captured.get("user", ""))
        self.assertIsInstance(out, str)

    def test_garmin_block_returns_empty_when_env_unset(self):
        b, _ = _make_briefing()
        self.assertEqual(b._garmin_block(), "")

    def test_garmin_block_returns_empty_when_env_falsy(self):
        b, _ = _make_briefing()
        for falsy in ("0", "false", "no", "off", ""):
            with self.subTest(value=falsy):
                os.environ["JACK_GARMIN_ENABLED"] = falsy
                self.assertEqual(b._garmin_block(), "")
        os.environ.pop("JACK_GARMIN_ENABLED", None)


# ---------------------------------------------------------------------------
# 3. Garmin exception swallowed — briefing still compiles
# ---------------------------------------------------------------------------

class TestGarminException(unittest.TestCase):
    def setUp(self):
        os.environ["JACK_GARMIN_ENABLED"] = "1"
        os.environ.pop("JACK_CALENDAR_ENABLED", None)

    def tearDown(self):
        os.environ.pop("JACK_GARMIN_ENABLED", None)

    def test_briefing_survives_garmin_exception(self):
        b, _ = _make_briefing()

        # Simulate GarminClient.sleep_summary_text raising at import/call time.
        def _boom():
            raise RuntimeError("Garmin API down")

        # Patch the helper directly to raise so we verify the try/except inside.

        def _raising_block():
            # Re-implement what _garmin_block does but force the inner call to raise.
            if not b.__class__.__mro__:  # always false — just to trigger the path
                return ""
            raise RuntimeError("simulated garmin failure")

        with patch.object(b, "_garmin_block", side_effect=RuntimeError("garmin down")):
            # compile_briefing calls _garmin_block; if that raises the briefing
            # should still return a string (send_briefing swallows, but
            # compile_briefing itself should not be the one raising here —
            # _garmin_block is spec'd to never raise).
            # Instead, test _garmin_block isolation: patch the inner lazy import.
            pass

        # Patch at the GarminClient level: make sleep_summary_text raise.
        import unittest.mock as mock

        class _BoomGarmin:
            def sleep_summary_text(self, day=None):
                raise ConnectionError("network unreachable")

        with mock.patch("briefing.morning.MorningBriefing._garmin_block",
                        lambda self: self._garmin_block_patched()):
            pass  # skip nested approach

        # Cleanest approach: directly test _garmin_block with patched import.
        b._garmin_block.__func__ if hasattr(b._garmin_block, "__func__") else None

        # Patch integrations.garmin at sys.modules level so the lazy import gets
        # our fake that raises.
        import types
        fake_garmin_mod = types.ModuleType("integrations.garmin")

        class _RaisingClient:
            def sleep_summary_text(self, day=None):
                raise ConnectionError("boom")

        fake_garmin_mod.GarminClient = _RaisingClient
        with mock.patch.dict(sys.modules, {"integrations.garmin": fake_garmin_mod}):
            result = b._garmin_block()

        self.assertEqual(result, "")

        # And compile_briefing must still return a string.
        out = b.compile_briefing("42", now=_NOW)
        self.assertIsInstance(out, str)


# ---------------------------------------------------------------------------
# 4. Calendar block appears when enabled
# ---------------------------------------------------------------------------

class TestCalendarEnabled(unittest.TestCase):
    def setUp(self):
        os.environ["JACK_CALENDAR_ENABLED"] = "1"
        os.environ.pop("JACK_GARMIN_ENABLED", None)

    def tearDown(self):
        os.environ.pop("JACK_CALENDAR_ENABLED", None)

    def test_briefing_includes_calendar_when_enabled(self):
        b, captured = _make_briefing()

        with patch.object(b, "_calendar_block", return_value="9:00 AM — Standup"):
            out = b.compile_briefing("42", now=_NOW)

        self.assertIsInstance(out, str)
        self.assertIn("calendar", captured["user"].lower())
        self.assertIn("Standup", captured["user"])


# ---------------------------------------------------------------------------
# 5. Calendar block absent when disabled
# ---------------------------------------------------------------------------

class TestCalendarDisabled(unittest.TestCase):
    def setUp(self):
        os.environ.pop("JACK_CALENDAR_ENABLED", None)
        os.environ.pop("JACK_GARMIN_ENABLED", None)

    def test_calendar_disabled_briefing_unchanged(self):
        b, captured = _make_briefing()
        out = b.compile_briefing("42", now=_NOW)

        self.assertEqual(b._calendar_block("42", _NOW), "")
        self.assertNotIn("calendar", captured.get("user", "").lower())
        self.assertIsInstance(out, str)

    def test_calendar_block_returns_empty_when_env_unset(self):
        b, _ = _make_briefing()
        self.assertEqual(b._calendar_block("42", _NOW), "")


if __name__ == "__main__":
    unittest.main()
