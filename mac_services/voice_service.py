#!/usr/bin/env python3
"""Jack Voice Service — Mac-side wake-word + STT + TTS pipeline.

Pipeline:
  IDLE     → openwakeword listens for "Hey Jack" on every 80 ms audio chunk
  RECORDING → sounddevice captures until 1.5 s of silence (VAD via RMS)
  THINKING  → faster-whisper transcribes; POST to VPS voice bridge
  SPEAKING  → Piper TTS synthesises reply; afplay speaks it (fallback: macOS say)
  → back to IDLE

Config: ~/.jack_voice_config (KEY=VALUE, or JACK_VOICE_* env vars)
Logs:   /tmp/jack-voice-service.log  +  stdout

Required packages (install once):
  pip install openwakeword faster-whisper sounddevice numpy

Required at runtime:
  - Piper binary + voice model  OR  macOS "say" (fallback, always available)
  - VPS jack-voice-bridge.service running on JACK_VOICE_BRIDGE_URL

launchd: ~/Library/LaunchAgents/com.hermes.voice.plist
"""

from __future__ import annotations

import array
import json
import logging
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
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
# Config  (KEY=VALUE in ~/.jack_voice_config, overridden by env)
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
    _c = _load_config()
    return os.environ.get(key) or _c.get(key) or default


# Sensible defaults — override in ~/.jack_voice_config
SAMPLE_RATE       = int(_cfg("JACK_SAMPLE_RATE", "16000"))
CHUNK_SAMPLES     = int(_cfg("JACK_CHUNK_SAMPLES", "1280"))   # 80 ms at 16 kHz
WAKE_WORD         = _cfg("JACK_WAKE_WORD", "hey_jarvis")       # openwakeword model key
WAKE_THRESHOLD    = float(_cfg("JACK_WAKE_THRESHOLD", "0.5"))
SILENCE_THRESHOLD = float(_cfg("JACK_SILENCE_THRESHOLD", "0.02"))   # RMS fraction of int16 max
SILENCE_DURATION  = float(_cfg("JACK_SILENCE_DURATION", "1.5"))     # seconds of silence → end recording
MAX_RECORD_SECS   = float(_cfg("JACK_MAX_RECORD_SECS", "30.0"))
WHISPER_MODEL     = _cfg("JACK_WHISPER_MODEL", "base.en")
BRIDGE_URL        = _cfg("JACK_VOICE_BRIDGE_URL", "http://100.115.193.64:8766/voice")
BRIDGE_TIMEOUT    = int(_cfg("JACK_VOICE_BRIDGE_TIMEOUT", "30"))
SESSION_ID        = _cfg("JACK_VOICE_SESSION_ID", "voice-mac")
PIPER_BINARY      = _cfg("JACK_PIPER_BINARY", "piper")
PIPER_MODEL       = _cfg("JACK_PIPER_MODEL", "")   # path to .onnx voice model
TTS_BACKEND       = _cfg("JACK_TTS_BACKEND", "auto")  # auto | piper | say

# ---------------------------------------------------------------------------
# Lazy imports (fail clearly if packages missing)
# ---------------------------------------------------------------------------

def _require(package: str, install_hint: str = ""):
    try:
        return __import__(package)
    except ImportError:
        hint = f"  pip install {install_hint or package}"
        log.error("Missing package '%s'. Install with:\n%s", package, hint)
        sys.exit(1)


# ---------------------------------------------------------------------------
# VAD — simple RMS-based silence detection
# ---------------------------------------------------------------------------

INT16_MAX = 32768.0

def _rms(pcm_int16: "list[int] | array.array") -> float:
    if not pcm_int16:
        return 0.0
    ss = sum(s * s for s in pcm_int16)
    return math.sqrt(ss / len(pcm_int16)) / INT16_MAX


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def _write_wav(path: str, pcm_frames: list, sample_rate: int = SAMPLE_RATE) -> None:
    """Write raw int16 PCM frames list-of-lists to a mono WAV file."""
    import numpy as np
    flat = np.concatenate(pcm_frames).astype("int16")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(flat.tobytes())


def _play_wav(path: str) -> None:
    subprocess.run(["afplay", path], check=False)


def _beep() -> None:
    """Short confirmation beep so the user knows Jack is listening."""
    subprocess.run(["afplay", "/System/Library/Sounds/Tink.aiff"], check=False)


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------

def _speak_piper(text: str) -> bool:
    if not PIPER_MODEL or not shutil.which(PIPER_BINARY):
        return False
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            wav_path = fh.name
        subprocess.run(
            [PIPER_BINARY, "--model", PIPER_MODEL, "--output_file", wav_path],
            input=text.encode(),
            check=True,
            timeout=20,
        )
        _play_wav(wav_path)
        os.unlink(wav_path)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Piper TTS failed: %s", exc)
        return False


def _speak_say(text: str) -> None:
    """macOS built-in TTS — always available, lower quality."""
    subprocess.run(["say", "-v", "Samantha", text], check=False, timeout=60)


def speak(text: str) -> None:
    backend = TTS_BACKEND
    if backend == "piper" or (backend == "auto" and PIPER_MODEL):
        if _speak_piper(text):
            return
    if backend == "say" or backend == "auto":
        _speak_say(text)
    elif not _speak_piper(text):
        log.warning("No TTS backend available; reply not spoken: %r", text[:80])


# ---------------------------------------------------------------------------
# STT — faster-whisper
# ---------------------------------------------------------------------------

_whisper_model = None

def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        log.info("Loading Whisper model '%s' (first run downloads ~150 MB)...", WHISPER_MODEL)
        _whisper_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        log.info("Whisper ready")
    return _whisper_model


def transcribe(wav_path: str) -> str:
    model = _get_whisper()
    segments, _info = model.transcribe(wav_path, beam_size=5, language="en")
    return " ".join(seg.text for seg in segments).strip()


# ---------------------------------------------------------------------------
# Bridge — call VPS Jack Voice Bridge
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
        log.error("Bridge unreachable (%s): %s", BRIDGE_URL, exc)
        return "I can't reach the bridge right now. Check that the VPS voice service is running."
    except Exception as exc:  # noqa: BLE001
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
        log.info("Loading openwakeword model '%s'...", WAKE_WORD)
        _oww_model = Model(wakeword_models=[WAKE_WORD])
        log.info("openwakeword ready — listening for '%s'", WAKE_WORD)
    return _oww_model


def _detected(chunk_float32) -> bool:
    """Feed a chunk to openwakeword, return True if wake word fires."""
    oww = _get_oww()
    pred = oww.predict(chunk_float32)
    score = pred.get(WAKE_WORD, 0.0)
    return score >= WAKE_THRESHOLD


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run() -> None:
    import numpy as np
    import sounddevice as sd

    # Warm up Whisper here so the first query isn't slow
    _get_whisper()
    _get_oww()

    log.info("Jack Voice Service started. Say '%s' to begin.", WAKE_WORD.replace("_", " ").title())

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=CHUNK_SAMPLES,
    ) as stream:
        while True:
            # ── IDLE: scan for wake word ─────────────────────────────────
            chunk_int16, _ = stream.read(CHUNK_SAMPLES)
            chunk_int16 = chunk_int16.flatten()

            # openwakeword expects float32 in [-1, 1]
            chunk_f32 = chunk_int16.astype("float32") / INT16_MAX

            if not _detected(chunk_f32):
                continue

            # ── Wake word fired ──────────────────────────────────────────
            log.info("Wake word detected!")
            _beep()

            # ── RECORDING: capture until silence ────────────────────────
            frames: list = [chunk_int16]
            silence_chunks = 0
            silence_chunks_needed = int(SILENCE_DURATION * SAMPLE_RATE / CHUNK_SAMPLES)
            max_chunks = int(MAX_RECORD_SECS * SAMPLE_RATE / CHUNK_SAMPLES)

            while len(frames) < max_chunks:
                chunk_int16, _ = stream.read(CHUNK_SAMPLES)
                chunk_int16 = chunk_int16.flatten()
                frames.append(chunk_int16)

                if _rms(chunk_int16) < SILENCE_THRESHOLD:
                    silence_chunks += 1
                    if silence_chunks >= silence_chunks_needed:
                        break
                else:
                    silence_chunks = 0

            # ── THINKING: STT + bridge ───────────────────────────────────
            log.info("Recording done (%d chunks). Transcribing...", len(frames))
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
                wav_path = fh.name
            try:
                _write_wav(wav_path, frames)
                text = transcribe(wav_path)
            finally:
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass

            text = text.strip()
            log.info("Transcribed: %r", text)

            if not text or len(text) < 2:
                log.info("Transcription empty or too short — discarding.")
                speak("Hmm, I didn't catch that.")
                continue

            log.info("Sending to bridge: %r", text[:120])
            reply = call_bridge(text)
            log.info("Reply: %r", reply[:120] if reply else "(empty)")

            # ── SPEAKING ─────────────────────────────────────────────────
            if reply:
                speak(reply)
            else:
                speak("I didn't get a response — Jack's brain may be busy.")

            # Brief pause before resuming wake-word listening
            time.sleep(0.3)


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
