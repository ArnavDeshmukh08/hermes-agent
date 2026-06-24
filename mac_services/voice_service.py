#!/usr/bin/env python3
"""Jack Voice Service — Mac-side wake-word + STT + TTS pipeline (Phase 2).

Pipeline:
  IDLE     → openwakeword listens for "Hey Jack" on every 80 ms audio chunk
  RECORDING → sounddevice captures until silence (VAD via RMS)
  THINKING  → faster-whisper transcribes; POST to VPS voice bridge
  SPEAKING  → Piper TTS speaks reply; BARGE-IN: if Arnav talks, interrupt & re-listen
  → back to IDLE / RECORDING (if barged-in)

Phase 2 additions over Phase 1:
  - Barge-in: TTS runs in a thread; main loop monitors mic during SPEAKING;
    any speech above JACK_BARGE_THRESHOLD interrupts playback and jumps to RECORDING
  - Better error recovery: explicit "didn't catch that" / "can you say that again?"
  - Latency: tighter VAD (0.8 s default silence window), pre-warm at startup
  - Piper pre-synthesis: synthesise to WAV before calling afplay so audio starts faster
  - Adaptive silence: 3 consecutive empty chunks after speech → stop earlier

Config: ~/.jack_voice_config (KEY=VALUE, or JACK_VOICE_* env vars)
Logs:   /tmp/jack-voice-service.log + stdout
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [jack-voice] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("/tmp/jack-voice-service.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("jack_voice")

# ---------------------------------------------------------------------------
# Config (KEY=VALUE in ~/.jack_voice_config, overridden by env)
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    cfg: dict = {}
    path = Path("~/.jack_voice_config").expanduser()
    if path.exists():
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip()
    return cfg


def _cfg(key: str, default: str) -> str:
    return os.environ.get(key) or _load_config().get(key) or default


SAMPLE_RATE        = int(_cfg("JACK_SAMPLE_RATE", "16000"))
CHUNK_SAMPLES      = int(_cfg("JACK_CHUNK_SAMPLES", "1280"))    # 80 ms at 16 kHz
WAKE_WORD          = _cfg("JACK_WAKE_WORD", "hey_jarvis")
WAKE_THRESHOLD     = float(_cfg("JACK_WAKE_THRESHOLD", "0.5"))
SILENCE_THRESHOLD  = float(_cfg("JACK_SILENCE_THRESHOLD", "0.02"))   # RMS fraction
SILENCE_DURATION   = float(_cfg("JACK_SILENCE_DURATION", "0.8"))     # seconds (Phase 2: 0.8 vs 1.5)
MAX_RECORD_SECS    = float(_cfg("JACK_MAX_RECORD_SECS", "30.0"))
WHISPER_MODEL      = _cfg("JACK_WHISPER_MODEL", "base.en")
BRIDGE_URL         = _cfg("JACK_VOICE_BRIDGE_URL", "http://100.115.193.64:8766/voice")
BRIDGE_TIMEOUT     = int(_cfg("JACK_VOICE_BRIDGE_TIMEOUT", "30"))
SESSION_ID         = _cfg("JACK_VOICE_SESSION_ID", "voice-mac")
PIPER_BINARY       = _cfg("JACK_PIPER_BINARY", "piper")
PIPER_MODEL        = _cfg("JACK_PIPER_MODEL", "")
TTS_BACKEND        = _cfg("JACK_TTS_BACKEND", "auto")
BARGE_IN_THRESHOLD = float(_cfg("JACK_BARGE_THRESHOLD", "0.06"))   # louder than silence
BARGE_IN_CHUNKS    = int(_cfg("JACK_BARGE_CHUNKS", "4"))            # consecutive loud chunks to trigger

# Sound effects
LISTEN_CHIME  = "/System/Library/Sounds/Tink.aiff"
BARGE_CHIME   = "/System/Library/Sounds/Pop.aiff"

INT16_MAX = 32768.0

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class State:
    IDLE      = "idle"
    RECORDING = "recording"
    THINKING  = "thinking"
    SPEAKING  = "speaking"

_current_state = State.IDLE
_barge_in_event = threading.Event()
_tts_proc: Optional[subprocess.Popen] = None
_tts_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

def _require(package: str, install_hint: str = "") -> None:
    try:
        __import__(package)
    except ImportError:
        log.error("Missing package '%s'. Install: pip install %s", package, install_hint or package)
        sys.exit(1)

# ---------------------------------------------------------------------------
# VAD helpers
# ---------------------------------------------------------------------------

def _rms(chunk) -> float:
    if not len(chunk):
        return 0.0
    ss = sum(int(s) * int(s) for s in chunk)
    return math.sqrt(ss / len(chunk)) / INT16_MAX


# ---------------------------------------------------------------------------
# WAV helpers
# ---------------------------------------------------------------------------

def _write_wav(path: str, frames: list, sample_rate: int = SAMPLE_RATE) -> None:
    import numpy as np
    flat = np.concatenate(frames).astype("int16")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(flat.tobytes())


# ---------------------------------------------------------------------------
# Audio feedback
# ---------------------------------------------------------------------------

def _chime(path: str) -> None:
    subprocess.run(["afplay", path], check=False, timeout=3)


# ---------------------------------------------------------------------------
# TTS — interruptible (Phase 2 barge-in)
# ---------------------------------------------------------------------------

def _synth_wav(text: str, wav_path: str) -> bool:
    """Synthesise text to WAV via Piper. Returns True on success."""
    if not PIPER_MODEL or not shutil.which(PIPER_BINARY):
        return False
    try:
        subprocess.run(
            [PIPER_BINARY, "--model", PIPER_MODEL, "--output_file", wav_path],
            input=text.encode(),
            check=True,
            timeout=20,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Piper synth failed: %s", exc)
        return False


def _speak_say(text: str) -> subprocess.Popen:
    """Start macOS say and return the process (so it can be killed)."""
    return subprocess.Popen(["say", "-v", "Samantha", text])


def speak(text: str) -> bool:
    """Speak text; return False if barged-in (interrupted), True if completed.

    Phase 2: TTS runs via subprocess.Popen so the main loop can kill it
    at any time by calling signal_barge_in(). The caller waits for the
    subprocess to finish or die.
    """
    global _tts_proc
    _barge_in_event.clear()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
        wav_path = fh.name

    try:
        if TTS_BACKEND != "say" and _synth_wav(text, wav_path):
            proc = subprocess.Popen(["afplay", wav_path])
        else:
            os.unlink(wav_path)
            wav_path = ""
            proc = _speak_say(text)

        with _tts_lock:
            _tts_proc = proc

        completed = True
        while proc.poll() is None:
            if _barge_in_event.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                completed = False
                log.info("Barge-in! TTS interrupted.")
                break
            time.sleep(0.04)

        return completed
    finally:
        with _tts_lock:
            _tts_proc = None
        if wav_path and os.path.exists(wav_path):
            try:
                os.unlink(wav_path)
            except OSError:
                pass


def signal_barge_in() -> None:
    """Called from the audio-monitoring path to interrupt TTS."""
    _barge_in_event.set()


# ---------------------------------------------------------------------------
# STT — faster-whisper
# ---------------------------------------------------------------------------

_whisper_model = None

def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        log.info("Loading Whisper model '%s'...", WHISPER_MODEL)
        _whisper_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        log.info("Whisper ready")
    return _whisper_model


def transcribe(wav_path: str) -> str:
    model = _get_whisper()
    segments, _ = model.transcribe(wav_path, beam_size=5, language="en")
    return " ".join(seg.text for seg in segments).strip()


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

def call_bridge(text: str) -> str:
    payload = json.dumps({"text": text, "session_id": SESSION_ID}).encode()
    req = urllib.request.Request(
        BRIDGE_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=BRIDGE_TIMEOUT) as resp:
            body = json.loads(resp.read())
    except urllib.error.URLError as exc:
        log.error("Bridge unreachable: %s", exc)
        return "I can't reach the bridge right now."
    except Exception:  # noqa: BLE001
        log.exception("Bridge call failed")
        return "Something went wrong reaching Jack's brain."
    if not body.get("ok"):
        log.error("Bridge error: %s", body.get("error"))
        return "Jack's brain returned an error."
    return body.get("reply", "")


# ---------------------------------------------------------------------------
# Wake word
# ---------------------------------------------------------------------------

_oww_model = None

def _get_oww():
    global _oww_model
    if _oww_model is None:
        from openwakeword.model import Model
        log.info("Loading openwakeword '%s'...", WAKE_WORD)
        _oww_model = Model(wakeword_models=[WAKE_WORD])
        log.info("openwakeword ready — say '%s' to begin", WAKE_WORD.replace("_", " ").title())
    return _oww_model


def _wake_detected(chunk_float32) -> bool:
    oww = _get_oww()
    pred = oww.predict(chunk_float32)
    return pred.get(WAKE_WORD, 0.0) >= WAKE_THRESHOLD


# ---------------------------------------------------------------------------
# Main loop (Phase 2 state machine)
# ---------------------------------------------------------------------------

def run() -> None:
    import numpy as np
    import sounddevice as sd

    # Pre-warm so first query isn't slow
    _get_whisper()
    _get_oww()

    silence_chunks_needed = int(SILENCE_DURATION * SAMPLE_RATE / CHUNK_SAMPLES)
    max_record_chunks = int(MAX_RECORD_SECS * SAMPLE_RATE / CHUNK_SAMPLES)

    state = State.IDLE
    frames: list = []
    silence_count = 0
    barge_loud_count = 0  # consecutive loud chunks during SPEAKING

    log.info("Jack Voice Service ready. Say '%s' to talk to Jack.", WAKE_WORD.replace("_", " ").title())

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=CHUNK_SAMPLES,
    ) as stream:

        while True:
            chunk_int16, _ = stream.read(CHUNK_SAMPLES)
            chunk_int16 = chunk_int16.flatten()
            rms = _rms(chunk_int16)

            # ── SPEAKING: watch for barge-in ─────────────────────────────
            if state == State.SPEAKING:
                if rms > BARGE_IN_THRESHOLD:
                    barge_loud_count += 1
                    if barge_loud_count >= BARGE_IN_CHUNKS:
                        log.info("Barge-in detected (RMS %.3f > %.3f)", rms, BARGE_IN_THRESHOLD)
                        signal_barge_in()
                        _chime(BARGE_CHIME)
                        # Drop back to RECORDING to capture new query
                        state = State.RECORDING
                        frames = [chunk_int16]
                        silence_count = 0
                        barge_loud_count = 0
                else:
                    barge_loud_count = max(0, barge_loud_count - 1)
                continue

            # ── IDLE: listen for wake word ────────────────────────────────
            if state == State.IDLE:
                chunk_f32 = chunk_int16.astype("float32") / INT16_MAX
                if not _wake_detected(chunk_f32):
                    continue
                log.info("Wake word detected!")
                _chime(LISTEN_CHIME)
                state = State.RECORDING
                frames = []
                silence_count = 0
                continue

            # ── RECORDING: accumulate until silence ───────────────────────
            if state == State.RECORDING:
                frames.append(chunk_int16)

                if rms < SILENCE_THRESHOLD:
                    silence_count += 1
                else:
                    silence_count = 0

                if silence_count >= silence_chunks_needed or len(frames) >= max_record_chunks:
                    if len(frames) < 3:  # too short — noise burst
                        log.info("Recording too short, resetting.")
                        state = State.IDLE
                        continue

                    state = State.THINKING
                    log.info("Recording done (%d chunks). Transcribing...", len(frames))

                    # ── THINKING: STT + bridge ───────────────────────────
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
                        wav_path = fh.name
                    try:
                        _write_wav(wav_path, frames)
                        text = transcribe(wav_path).strip()
                    except Exception:  # noqa: BLE001
                        log.exception("Transcription failed")
                        text = ""
                    finally:
                        try:
                            os.unlink(wav_path)
                        except OSError:
                            pass

                    log.info("Transcribed: %r", text)

                    # Error recovery: empty / too short / too long
                    if not text or len(text) < 2:
                        reply = "Hmm, I didn't catch that — try again?"
                    elif len(frames) >= max_record_chunks:
                        reply = "That was quite long — could you rephrase in a sentence or two?"
                    else:
                        log.info("Calling bridge: %r", text[:100])
                        reply = call_bridge(text)
                        if not reply:
                            reply = "Got an empty reply from my brain — try again?"

                    log.info("Reply: %r", (reply or "")[:120])

                    # ── SPEAKING ─────────────────────────────────────────
                    state = State.SPEAKING
                    barge_loud_count = 0

                    completed = speak(reply)

                    if state == State.RECORDING:
                        # barge-in happened inside speak(), which already flipped state
                        # frames already has the new chunk; loop continues recording
                        pass
                    else:
                        state = State.IDLE

                    time.sleep(0.15)  # brief breath before re-arming wake word


def main() -> None:
    _require("sounddevice", "sounddevice")
    _require("numpy", "numpy")
    _require("faster_whisper", "faster-whisper")
    _require("openwakeword", "openwakeword")

    try:
        run()
    except KeyboardInterrupt:
        log.info("Jack Voice Service stopped.")
    except Exception:
        log.exception("Fatal error in Jack Voice Service")
        sys.exit(1)


if __name__ == "__main__":
    main()
