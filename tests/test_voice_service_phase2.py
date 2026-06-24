"""Phase 2 voice service tests.

Tests barge-in signaling, speak() interrupt behavior, error recovery
(empty transcription / too-long recording), and VAD helpers.
No audio hardware or network is required.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

os.environ.setdefault("HERMES_LLM_MOCK", "1")
os.environ.setdefault("JACK_VOICE_BRIDGE_URL", "http://127.0.0.1:19999/voice")  # unreachable
os.environ.setdefault("JACK_BARGE_THRESHOLD", "0.06")
os.environ.setdefault("JACK_BARGE_CHUNKS", "2")
os.environ.setdefault("JACK_SILENCE_DURATION", "0.8")
os.environ.setdefault("JACK_TTS_BACKEND", "say")  # avoid piper in tests

import mac_services.voice_service as vs  # noqa: E402


# ---------------------------------------------------------------------------
# VAD
# ---------------------------------------------------------------------------

class TestRMS(unittest.TestCase):
    def test_silence_gives_zero(self):
        self.assertEqual(vs._rms([0, 0, 0, 0]), 0.0)

    def test_max_signal_gives_one(self):
        import math
        chunk = [32767, -32768, 32767, -32768]
        rms = vs._rms(chunk)
        self.assertAlmostEqual(rms, 1.0, delta=0.01)

    def test_half_amplitude(self):
        chunk = [16384, -16384]
        rms = vs._rms(chunk)
        self.assertAlmostEqual(rms, 0.5, delta=0.01)

    def test_empty_returns_zero(self):
        self.assertEqual(vs._rms([]), 0.0)

    def test_above_silence_threshold(self):
        chunk = [int(32767 * 0.05)] * 100
        self.assertGreater(vs._rms(chunk), vs.SILENCE_THRESHOLD)

    def test_below_silence_threshold(self):
        chunk = [int(32767 * 0.005)] * 100
        self.assertLess(vs._rms(chunk), vs.SILENCE_THRESHOLD)


# ---------------------------------------------------------------------------
# Barge-in signaling
# ---------------------------------------------------------------------------

class TestBargeIn(unittest.TestCase):
    def setUp(self):
        vs._barge_in_event.clear()

    def test_signal_sets_event(self):
        vs.signal_barge_in()
        self.assertTrue(vs._barge_in_event.is_set())

    def test_event_clears_before_speak(self):
        vs._barge_in_event.set()
        # speak() should clear the event on entry; mock the subprocess so we don't actually speak
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = 0  # immediately done
            mock_proc.wait.return_value = None
            mock_popen.return_value = mock_proc
            vs.speak("hello")
        # event is cleared on entry
        self.assertFalse(vs._barge_in_event.is_set())

    def test_speak_returns_false_on_barge_in(self):
        """speak() returns False when barge-in fires during playback."""
        call_count = [0]

        def _fake_poll():
            call_count[0] += 1
            if call_count[0] >= 3:
                vs._barge_in_event.set()  # trigger barge-in
            return None  # still running

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.side_effect = _fake_poll
            mock_proc.terminate.return_value = None
            mock_proc.wait.return_value = None
            mock_popen.return_value = mock_proc

            result = vs.speak("say something long")

        self.assertFalse(result)

    def test_speak_returns_true_when_completed(self):
        """speak() returns True when TTS finishes without barge-in."""
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = 0  # done immediately
            mock_popen.return_value = mock_proc

            result = vs.speak("short reply")

        self.assertTrue(result)

    def test_barge_in_thread_safety(self):
        """signal_barge_in() can be called from any thread."""
        def _fire():
            time.sleep(0.01)
            vs.signal_barge_in()

        t = threading.Thread(target=_fire)
        t.start()
        t.join(timeout=1)
        self.assertTrue(vs._barge_in_event.is_set())


# ---------------------------------------------------------------------------
# Bridge — error recovery
# ---------------------------------------------------------------------------

class TestCallBridge(unittest.TestCase):
    def test_unreachable_returns_friendly_message(self):
        # JACK_VOICE_BRIDGE_URL is set to 127.0.0.1:19999 (nothing listening)
        reply = vs.call_bridge("test message")
        self.assertIn("reach", reply.lower())  # "can't reach the bridge"

    def test_bridge_ok_response_returned(self):
        import json
        import unittest.mock as um

        ok_body = json.dumps({"ok": True, "reply": "Hello from Jack!"}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = ok_body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = vs.call_bridge("hey jack")

        self.assertEqual(result, "Hello from Jack!")

    def test_bridge_error_flag_returns_error_message(self):
        import json

        err_body = json.dumps({"ok": False, "error": "internal error"}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = err_body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = vs.call_bridge("hey jack")

        self.assertIn("error", result.lower())


# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------

class TestStateConstants(unittest.TestCase):
    def test_states_are_distinct(self):
        states = [vs.State.IDLE, vs.State.RECORDING, vs.State.THINKING, vs.State.SPEAKING]
        self.assertEqual(len(set(states)), 4)

    def test_barge_threshold_above_silence(self):
        self.assertGreater(vs.BARGE_IN_THRESHOLD, vs.SILENCE_THRESHOLD)

    def test_silence_duration_reduced_vs_phase1(self):
        # Phase 2 tightens silence window to ≤1.0 s
        self.assertLessEqual(vs.SILENCE_DURATION, 1.0)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig(unittest.TestCase):
    def test_barge_chunks_positive(self):
        self.assertGreater(vs.BARGE_IN_CHUNKS, 0)

    def test_silence_chunks_positive(self):
        needed = int(vs.SILENCE_DURATION * vs.SAMPLE_RATE / vs.CHUNK_SAMPLES)
        self.assertGreater(needed, 0)

    def test_max_record_longer_than_silence(self):
        self.assertGreater(vs.MAX_RECORD_SECS, vs.SILENCE_DURATION)


if __name__ == "__main__":
    unittest.main()
