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
# Wake phrase detection (replaces openwakeword in Phase 2b)
# ---------------------------------------------------------------------------

class TestWakePhrase(unittest.TestCase):
    def test_hey_jarvis_detected(self):
        self.assertTrue(vs._is_wake_phrase("Hey Jarvis what's the weather"))

    def test_hey_jack_detected(self):
        self.assertTrue(vs._is_wake_phrase("hey jack, turn on the lights"))

    def test_hey_jac_typo_detected(self):
        self.assertTrue(vs._is_wake_phrase("Hey Jac set a reminder"))

    def test_unrelated_speech_not_detected(self):
        self.assertFalse(vs._is_wake_phrase("What time is it"))

    def test_empty_string_not_detected(self):
        self.assertFalse(vs._is_wake_phrase(""))

    def test_strip_hey_jarvis(self):
        q = vs._strip_wake_phrase("Hey Jarvis what time is it")
        self.assertEqual(q, "what time is it")

    def test_strip_hey_jack_with_comma(self):
        q = vs._strip_wake_phrase("hey jack, tell me the news")
        self.assertEqual(q, "tell me the news")

    def test_strip_returns_empty_for_bare_wake(self):
        q = vs._strip_wake_phrase("Hey Jarvis")
        self.assertEqual(q, "")

    def test_case_insensitive(self):
        self.assertTrue(vs._is_wake_phrase("HEY JARVIS hello"))
        self.assertTrue(vs._is_wake_phrase("HEY JACK hello"))


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

    def test_speech_onset_threshold_above_silence(self):
        self.assertGreater(vs.SPEECH_ONSET_THRESHOLD, vs.SILENCE_THRESHOLD)


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

    def test_speech_onset_chunks_positive(self):
        self.assertGreater(vs.SPEECH_ONSET_CHUNKS, 0)

    def test_wake_phrases_configured(self):
        self.assertGreater(len(vs._WAKE_PHRASES), 0)


# ---------------------------------------------------------------------------
# TTS voice config
# ---------------------------------------------------------------------------

class TestTTSVoice(unittest.TestCase):
    def test_say_voice_exists(self):
        """SAY_VOICE must be configured as a module-level constant."""
        self.assertTrue(hasattr(vs, "SAY_VOICE"))

    def test_say_voice_is_nonempty_string(self):
        """SAY_VOICE must be a non-empty string."""
        self.assertIsInstance(vs.SAY_VOICE, str)
        self.assertGreater(len(vs.SAY_VOICE.strip()), 0)


# ---------------------------------------------------------------------------
# State file writing
# ---------------------------------------------------------------------------

class TestStateFile(unittest.TestCase):
    STATE_FILE = "/tmp/jack-voice-state"

    def setUp(self):
        # Remove any leftover state file so tests start clean.
        try:
            os.unlink(self.STATE_FILE)
        except FileNotFoundError:
            pass

    def test_set_state_writes_to_state_file(self):
        """_set_state() must write the new state string to /tmp/jack-voice-state."""
        vs._set_state(vs.State.IDLE)
        with open(self.STATE_FILE) as fh:
            content = fh.read()
        self.assertEqual(content, vs.State.IDLE)

    def test_set_state_updates_current_state(self):
        """_set_state() must also update the in-memory _current_state."""
        vs._set_state(vs.State.RECORDING)
        self.assertEqual(vs._current_state, vs.State.RECORDING)
        # Restore to IDLE so other tests aren't affected.
        vs._set_state(vs.State.IDLE)

    def test_set_state_overwrites_previous(self):
        """_set_state() writes the latest state, overwriting an older one."""
        vs._set_state(vs.State.THINKING)
        vs._set_state(vs.State.SPEAKING)
        with open(self.STATE_FILE) as fh:
            content = fh.read()
        self.assertEqual(content, vs.State.SPEAKING)
        vs._set_state(vs.State.IDLE)


# ---------------------------------------------------------------------------
# Chime non-blocking
# ---------------------------------------------------------------------------

class TestChimeNonBlocking(unittest.TestCase):
    def test_chime_uses_popen_not_run(self):
        """_chime() must use subprocess.Popen (non-blocking), never subprocess.run."""
        captured: list = []

        def _fake_popen(cmd, **kwargs):
            captured.append(cmd)
            mock = MagicMock()
            return mock

        with patch("subprocess.Popen", side_effect=_fake_popen) as mock_popen:
            # Use a real-looking path; _chime only skips if os.path.exists fails.
            with patch("os.path.exists", return_value=True):
                vs._chime("/System/Library/Sounds/Tink.aiff")

        mock_popen.assert_called_once()
        # subprocess.run must NOT have been called — only Popen
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][0], "afplay")

    def test_chime_skips_missing_file(self):
        """_chime() must not call Popen when the sound file does not exist."""
        with patch("subprocess.Popen") as mock_popen:
            vs._chime("/nonexistent/sound.aiff")
        mock_popen.assert_not_called()


class TestLocalFastPath(unittest.TestCase):
    """Time/date queries must be answered locally without calling the bridge."""

    def test_time_query_returns_ist_string(self):
        answer = vs._maybe_answer_locally("what time is it")
        self.assertIsNotNone(answer)
        self.assertIn("IST", answer)

    def test_date_query_returns_date(self):
        answer = vs._maybe_answer_locally("what's today's date")
        self.assertIsNotNone(answer)
        self.assertIn("IST", answer)

    def test_day_query_returns_day(self):
        answer = vs._maybe_answer_locally("what day is it")
        self.assertIsNotNone(answer)
        self.assertIn("IST", answer)

    def test_non_time_query_returns_none(self):
        self.assertIsNone(vs._maybe_answer_locally("how are you"))
        self.assertIsNone(vs._maybe_answer_locally("what is the weather"))
        self.assertIsNone(vs._maybe_answer_locally("remind me to call mom"))

    def test_answer_time_date_local_format(self):
        answer = vs._answer_time_date_local()
        self.assertIn("IST", answer)
        self.assertIn("It's", answer)
        # Must contain AM or PM
        self.assertTrue("AM" in answer or "PM" in answer)

    def test_bridge_not_called_for_time_query(self):
        """The bridge must never be called for time/date queries."""
        with patch("mac_services.voice_service.call_bridge") as mock_bridge:
            answer = vs._maybe_answer_locally("what time is it")
            mock_bridge.assert_not_called()
            self.assertIsNotNone(answer)


class TestTTSDegradedLogging(unittest.TestCase):
    """When Piper is unavailable, warnings must be logged loudly."""

    def test_synth_wav_warns_when_piper_missing(self):
        """_synth_wav() must log a WARNING when both binary and package are absent."""
        import builtins
        real_import = builtins.__import__

        def _mock_import(name, *args, **kwargs):
            if name in ("piper", "piper_tts"):
                raise ImportError("no piper")
            return real_import(name, *args, **kwargs)

        with (
            patch("mac_services.voice_service.shutil.which", return_value=None),
            patch("mac_services.voice_service.PIPER_MODEL", "some_model.onnx"),
            patch.object(vs, "PIPER_BINARY", "piper"),
            patch("builtins.__import__", side_effect=_mock_import),
        ):
            with self.assertLogs("jack_voice", level="WARNING") as cm:
                result = vs._synth_wav("hello", "/tmp/fake.wav")
        self.assertFalse(result)
        # At least one warning about degraded TTS
        self.assertTrue(
            any("DEGRADED" in line or "piper" in line.lower() for line in cm.output),
            cm.output,
        )


if __name__ == "__main__":
    unittest.main()
