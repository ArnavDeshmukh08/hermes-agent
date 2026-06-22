"""Tests for proactive.scheduler.ProactiveScheduler.

No real engine, no network, no file I/O in the main assertions: fakes drive
everything and the clock is injectable, so all assertions are deterministic and
offline.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scheduler(**kwargs):
    """Import and construct ProactiveScheduler with a no-op logger by default."""
    from proactive.scheduler import ProactiveScheduler

    kwargs.setdefault("logger", lambda line: None)
    return ProactiveScheduler(**kwargs)


class _FakeEngine:
    """Minimal stand-in for ProactiveEngine."""

    def __init__(self, return_value: int = 0, raise_exc: Exception | None = None):
        self._return_value = return_value
        self._raise_exc = raise_exc
        self.calls: list[dict] = []

    def run_cycle(self, user_id: str, *, now: datetime | None = None) -> int:
        self.calls.append({"user_id": user_id, "now": now})
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._return_value


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunOnceDisabled(unittest.TestCase):
    """test_run_once_disabled_returns_zero"""

    def test_run_once_disabled_returns_zero(self):
        # Arrange: engine is a strict mock that must never be called.
        engine = _FakeEngine(return_value=5)
        scheduler = _make_scheduler(engine=engine, user_id="42")

        with patch.dict(os.environ, {"JACK_PROACTIVE_ENABLED": "false"}):
            # Act
            result = scheduler.run_once()

        # Assert
        self.assertEqual(result, 0)
        self.assertEqual(engine.calls, [], "engine.run_cycle must not be called when disabled")


class TestRunOnceCallsEngine(unittest.TestCase):
    """test_run_once_calls_engine_run_cycle"""

    def test_run_once_calls_engine_run_cycle(self):
        # Arrange
        engine = _FakeEngine(return_value=2)
        scheduler = _make_scheduler(engine=engine, user_id="42")

        with patch.dict(os.environ, {"JACK_PROACTIVE_ENABLED": "1", "JACK_PROACTIVE_ENGINE": "legacy"}):
            # Act
            result = scheduler.run_once()

        # Assert
        self.assertEqual(result, 2)
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(engine.calls[0]["user_id"], "42")


class TestRunOnceEngineExceptionCaught(unittest.TestCase):
    """test_run_once_engine_exception_caught"""

    def test_run_once_engine_exception_caught(self):
        # Arrange: engine always raises.
        engine = _FakeEngine(raise_exc=RuntimeError("boom"))
        scheduler = _make_scheduler(engine=engine, user_id="42")

        with patch.dict(os.environ, {"JACK_PROACTIVE_ENABLED": "1"}):
            # Act — must not raise.
            result = scheduler.run_once()

        # Assert
        self.assertEqual(result, 0)


class TestEnvPollSeconds(unittest.TestCase):
    """test_env_poll_seconds_default / override / invalid"""

    def test_env_poll_seconds_default(self):
        from proactive.scheduler import _env_poll_seconds

        env = {k: v for k, v in os.environ.items() if k != "JACK_PROACTIVE_POLL_SECONDS"}
        with patch.dict(os.environ, env, clear=True):
            result = _env_poll_seconds()
        self.assertEqual(result, 900)

    def test_env_poll_seconds_override(self):
        from proactive.scheduler import _env_poll_seconds

        with patch.dict(os.environ, {"JACK_PROACTIVE_POLL_SECONDS": "300"}):
            result = _env_poll_seconds()
        self.assertEqual(result, 300)

    def test_env_poll_seconds_invalid_zero(self):
        from proactive.scheduler import _env_poll_seconds

        with patch.dict(os.environ, {"JACK_PROACTIVE_POLL_SECONDS": "0"}):
            result = _env_poll_seconds()
        self.assertEqual(result, 900)

    def test_env_poll_seconds_invalid_text(self):
        from proactive.scheduler import _env_poll_seconds

        with patch.dict(os.environ, {"JACK_PROACTIVE_POLL_SECONDS": "bad"}):
            result = _env_poll_seconds()
        self.assertEqual(result, 900)


class TestRunForeverStopsOnFlag(unittest.TestCase):
    """test_run_forever_stops_on_flag — set _stop=True immediately via a patched run_once."""

    def test_run_forever_stops_on_flag(self):
        # Arrange: a real engine that we confirm is never called.
        engine = _FakeEngine(return_value=0)
        scheduler = _make_scheduler(engine=engine, user_id="42", poll_seconds=900)

        # Patch run_once to flip the stop flag immediately so run_forever exits
        # on the first iteration before polling.
        original_run_once = scheduler.run_once

        def stop_immediately(now=None):
            scheduler._stop = True
            # Still call the real run_once to exercise enabled-check path.
            return original_run_once(now=now)

        scheduler.run_once = stop_immediately  # type: ignore[method-assign]

        with patch.dict(os.environ, {"JACK_PROACTIVE_ENABLED": "1"}):
            with patch("time.sleep"):  # don't actually sleep
                scheduler.run_forever()

        # If run_forever respected the flag, the sleep loop exited immediately.
        # engine was called once (from run_once before stop was detected in sleep loop).
        # The key assertion is that run_forever() returned at all (didn't hang).
        self.assertTrue(scheduler._stop)

    def test_run_forever_engine_never_called_when_flag_preset(self):
        """If _stop is True before run_forever, the main while-loop body never executes."""
        engine = _FakeEngine(return_value=0)
        scheduler = _make_scheduler(engine=engine, user_id="42", poll_seconds=900)

        # Pre-set stop flag — simulates immediate SIGTERM before first poll.
        scheduler._stop = True

        with patch("time.sleep"):
            scheduler.run_forever()

        self.assertEqual(engine.calls, [], "engine must not be called when _stop is preset")


class TestDefaultLoggerWritesLine(unittest.TestCase):
    """test_logger_writes_line — _default_logger appends to an injected tmp log path."""

    def test_logger_writes_line(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "proactive.log"

            # Build a scheduler whose _default_logger writes to our tmp path.
            from proactive.scheduler import ProactiveScheduler, _LOG_PATH as orig_log

            scheduler = ProactiveScheduler(
                engine=_FakeEngine(),
                user_id="42",
            )

            # Patch _LOG_PATH at module level for this call only.
            import proactive.scheduler as sched_mod

            original = sched_mod._LOG_PATH
            sched_mod._LOG_PATH = log_path
            try:
                scheduler._default_logger("hello from test")
            finally:
                sched_mod._LOG_PATH = original

            # Assert: file was created and contains our message.
            self.assertTrue(log_path.exists(), "log file should have been created")
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("hello from test", content)
            # Should also contain a UTC ISO timestamp prefix.
            self.assertIn("T", content)  # ISO format contains 'T' separator


if __name__ == "__main__":
    unittest.main()
