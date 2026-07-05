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
         SAY_VOICE is configurable via JACK_SAY_VOICE (default: "Reed (English (US))")
Logs:   /tmp/jack-voice-service.log + stdout
"""

from __future__ import annotations

import json
import logging
import os
import re
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
WAKE_PHRASE        = _cfg("JACK_WAKE_PHRASE", "hey jack,hey jarvis,hey jac")  # comma-separated
SPEECH_ONSET_THRESHOLD = float(_cfg("JACK_SPEECH_ONSET_THRESHOLD", "0.04"))  # louder than ambient hum
SPEECH_ONSET_CHUNKS    = int(_cfg("JACK_SPEECH_ONSET_CHUNKS", "5"))           # 400 ms sustained speech
SILENCE_THRESHOLD  = float(_cfg("JACK_SILENCE_THRESHOLD", "0.02"))   # RMS fraction
SILENCE_DURATION   = float(_cfg("JACK_SILENCE_DURATION", "0.8"))     # seconds (Phase 2: 0.8 vs 1.5)
MAX_RECORD_SECS    = float(_cfg("JACK_MAX_RECORD_SECS", "12.0"))
OWW_WAKE_WORD      = _cfg("JACK_WAKE_WORD", "hey_jarvis")
OWW_THRESHOLD      = float(_cfg("JACK_OWW_THRESHOLD", "0.5"))
WHISPER_MODEL      = _cfg("JACK_WHISPER_MODEL", "base.en")
BRIDGE_URL         = _cfg("JACK_VOICE_BRIDGE_URL", "http://100.115.193.64:8766/voice")
BRIDGE_TIMEOUT     = int(_cfg("JACK_VOICE_BRIDGE_TIMEOUT", "30"))
SESSION_ID         = _cfg("JACK_VOICE_SESSION_ID", "voice-mac")
PIPER_MODEL        = _cfg("JACK_PIPER_MODEL", "")
TTS_BACKEND        = _cfg("JACK_TTS_BACKEND", "auto")
SAY_VOICE          = _cfg("JACK_SAY_VOICE", "Reed (English (US))")
BARGE_IN_THRESHOLD = float(_cfg("JACK_BARGE_THRESHOLD", "0.06"))   # louder than silence
BARGE_IN_CHUNKS    = int(_cfg("JACK_BARGE_CHUNKS", "4"))            # consecutive loud chunks to trigger

# Sound effects
LISTEN_CHIME     = "/System/Library/Sounds/Tink.aiff"
BARGE_CHIME      = "/System/Library/Sounds/Pop.aiff"
RECORDING_CHIME  = "/System/Library/Sounds/Pop.aiff"   # immediate onset feedback
DONE_CHIME       = "/System/Library/Sounds/Bottle.aiff" # end-of-turn signal

INT16_MAX = 32768.0

# ---------------------------------------------------------------------------
# Local fast-path: time/date answered from system clock, never from bridge
# ---------------------------------------------------------------------------

_LOCAL_TIME_DATE_RE = re.compile(
    r"\b(?:"
    r"what(?:'?s)?\s+(?:the\s+)?time\b|"
    r"what\s+time\s+is\s+it\b|"
    r"current\s+time\b|"
    r"tell\s+me\s+the\s+time\b|"
    r"what(?:'?s)?\s+(?:today'?s?\s+)?date\b|"
    r"what\s+day\s+is\s+it\b|"
    r"what\s+day\s+(?:is\s+)?today\b|"
    r"today'?s?\s+date\b"
    r")",
    re.IGNORECASE,
)


def _answer_time_date_local() -> str:
    """Read the system clock in IST. Called BEFORE bridge — never fabricated."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    h = now.hour % 12 or 12
    ampm = "AM" if now.hour < 12 else "PM"
    return f"It's {h}:{now.minute:02d} {ampm} IST — {now.strftime('%A, %d %B %Y')}."


def _maybe_answer_locally(text: str) -> str | None:
    """Return a deterministic local answer, or None to proceed with bridge call."""
    if _LOCAL_TIME_DATE_RE.search(text):
        answer = _answer_time_date_local()
        log.info("Local fast-path (time/date): %r", answer)
        return answer
    return None


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

_STATE_FILE = "/tmp/jack-voice-state"


def _set_state(s: str) -> None:
    """Write the current voice-service state to the indicator state file.

    The voice indicator process (mac_services/voice_indicator.py) polls
    this file every 300 ms to update the menu bar icon.  Failures are
    silently swallowed — a missing or unwritable file must never interrupt
    the audio loop.
    """
    global _current_state
    _current_state = s
    try:
        with open(_STATE_FILE, "w") as _fh:
            _fh.write(s)
    except OSError:
        pass


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
    """Compute RMS of an int16 audio chunk, normalized to [0, 1].

    Vectorized with numpy: in production `chunk` is already a numpy array
    (from sounddevice), so iterating it element-by-element in a pure-Python
    generator (the previous implementation) is ~20-25x slower than a
    vectorized op for no benefit — this runs on every 80ms chunk in the
    main loop regardless of state, so the per-call cost matters.
    `np.asarray` also accepts plain lists/tuples (used in unit tests).
    """
    if not len(chunk):
        return 0.0
    import numpy as np
    samples = np.asarray(chunk, dtype=np.float64)
    return float(np.sqrt(np.mean(samples * samples))) / INT16_MAX


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
    if not os.path.exists(path):
        return
    subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# TTS — interruptible (Phase 2 barge-in)
# ---------------------------------------------------------------------------

_piper_voice_cache: "Optional[object]" = None


def _synth_wav(text: str, wav_path: str) -> bool:
    """Synthesise text to WAV via piper-tts Python package. Returns True on success."""
    global _piper_voice_cache
    if not PIPER_MODEL:
        return False
    try:
        import io
        from piper import PiperVoice
        if _piper_voice_cache is None:
            log.info("Loading Piper voice model: %s", PIPER_MODEL)
            _piper_voice_cache = PiperVoice.load(PIPER_MODEL)
        voice = _piper_voice_cache
        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wf:
            voice.synthesize_wav(text, wf)
        with open(wav_path, "wb") as fh:
            fh.write(wav_io.getvalue())
        return True
    except ImportError:
        log.warning(
            "DEGRADED TTS: piper-tts Python package not installed. "
            "Install: pip install piper-tts"
        )
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning("Piper TTS failed: %s", exc)
        _piper_voice_cache = None
        return False


def _speak_say(text: str) -> subprocess.Popen:
    """Start macOS say and return the process (so it can be killed)."""
    return subprocess.Popen(["say", "-v", SAY_VOICE, text])


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
            if TTS_BACKEND != "say" and PIPER_MODEL:
                log.warning(
                    "DEGRADED TTS: Piper synthesis failed — falling back to robotic "
                    "macOS 'say'. PIPER_MODEL=%r", PIPER_MODEL,
                )
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
# Wake word — openWakeWord (primary) with fuzzy Whisper fallback
# ---------------------------------------------------------------------------

_WAKE_PHRASES = [p.strip().lower() for p in WAKE_PHRASE.split(",") if p.strip()]
_oww_model = None


def _get_oww():
    global _oww_model
    if _oww_model is None:
        from openwakeword.model import Model
        log.info("Loading openWakeWord model '%s'...", OWW_WAKE_WORD)
        _oww_model = Model(wakeword_models=[OWW_WAKE_WORD], inference_framework="onnx")
        log.info("openWakeWord ready")
    return _oww_model


def _oww_score(chunk) -> float:
    """Return the wake-word score for this 80 ms chunk (0–1)."""
    try:
        scores = _get_oww().predict(chunk)
        return float(scores.get(OWW_WAKE_WORD, 0.0))
    except Exception as exc:  # noqa: BLE001
        log.debug("OWW predict error: %s", exc)
        return 0.0


def _fuzzy_is_wake(text: str) -> bool:
    """Fuzzy fallback: accept Whisper variants like 'hey jak', 'A Jack', 'hey, jack'."""
    import difflib
    t = re.sub(r"[^\w\s]", "", text.lower().strip())
    words = t.split()
    for phrase in _WAKE_PHRASES:
        p_words = phrase.split()
        n = len(p_words)
        for i in range(max(1, len(words) - n + 1)):
            window = " ".join(words[i : i + n])
            if difflib.SequenceMatcher(None, window, phrase).ratio() >= 0.72:
                return True
    return False


# ---------------------------------------------------------------------------
# Main loop (Phase 2 state machine)
# ---------------------------------------------------------------------------

def _select_input_device(sd) -> Optional[int]:
    """Pick a capture device index that avoids the Bluetooth mic HFP downgrade.

    Root cause this works around: opening ANY audio input stream on a
    Bluetooth headset (e.g. AirPods) forces macOS to renegotiate that
    physical device from A2DP (stereo, hi-fi, output-only) to HFP/HSP
    (mono, ~8-16kHz, phone-call quality) — and because the Bluetooth
    profile is per-device, not per-process, that downgrade also hits the
    device's OUTPUT path. That's why system media playback goes
    tinny/mono system-wide for as long as this service's InputStream is
    open, even though this file never requests any voice-processing /
    AEC / communications mode itself (there is no such flag in
    ``sounddevice``'s ``CoreAudioSettings`` to begin with — PortAudio's
    CoreAudio backend always opens plain ``kAudioUnitSubType_HALOutput``,
    never ``kAudioUnitSubType_VoiceProcessingIO``).

    Pinning capture to the built-in microphone means this process never
    touches the Bluetooth device's input path, so the OS has no reason to
    drop it out of A2DP — system playback stays full quality.

    Returns the built-in mic's device index, or ``None`` to fall back to
    the OS default input device if the built-in mic can't be found (e.g.
    a different Mac, or a machine without one).
    """
    try:
        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Could not query audio devices (%s); using OS default input device.",
            exc,
        )
        return None

    for idx, dev in enumerate(devices):
        name = dev.get("name", "")
        if dev.get("max_input_channels", 0) > 0 and "microphone" in name.lower():
            log.info(
                "Pinning mic capture to built-in device '%s' (index %d) to avoid "
                "Bluetooth HFP downgrading system audio while listening.",
                name, idx,
            )
            return idx

    log.warning(
        "Built-in microphone not found among audio devices; falling back to OS "
        "default input. If a Bluetooth headset is connected, system playback may "
        "drop to phone-call quality while Jack is listening — disable that "
        "device's automatic mic switching, or use the built-in mic during "
        "voice-service use, to avoid it."
    )
    return None


def run() -> None:
    import numpy as np
    import sounddevice as sd

    # Pre-warm Whisper and openWakeWord so first query isn't slow
    _get_whisper()
    _get_oww()

    silence_chunks_needed = int(SILENCE_DURATION * SAMPLE_RATE / CHUNK_SAMPLES)
    max_record_chunks = int(MAX_RECORD_SECS * SAMPLE_RATE / CHUNK_SAMPLES)

    state = State.IDLE
    _set_state(State.IDLE)
    frames: list = []
    silence_count = 0
    barge_loud_count = 0  # consecutive loud chunks during SPEAKING

    log.info(
        "Jack Voice Service ready. Say %s to talk to Jack.",
        " / ".join(f'"{p.title()}"' for p in _WAKE_PHRASES),
    )

    input_device = _select_input_device(sd)

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=CHUNK_SAMPLES,
        device=input_device,
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
                        log.info("Barge-in detected (RMS %.3f)", rms)
                        signal_barge_in()
                        _chime(BARGE_CHIME)
                        state = State.RECORDING
                        _set_state(State.RECORDING)
                        frames = [chunk_int16]
                        silence_count = 0
                        barge_loud_count = 0
                else:
                    barge_loud_count = max(0, barge_loud_count - 1)
                continue

            # ── IDLE: openWakeWord listening (80 ms chunks, no 30 s capture) ──
            if state == State.IDLE:
                score = _oww_score(chunk_int16)
                if score >= OWW_THRESHOLD:
                    log.info("Wake word '%s' detected (score=%.3f). Listening for command...", OWW_WAKE_WORD, score)
                    _chime(LISTEN_CHIME)   # immediate feedback — Jack is listening
                    frames = []
                    silence_count = 0
                    state = State.RECORDING
                    _set_state(State.RECORDING)
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
                        _set_state(State.IDLE)
                        continue

                    state = State.THINKING
                    _set_state(State.THINKING)
                    log.info("Recording done (%d chunks). Transcribing...", len(frames))

                    # ── THINKING: STT + wake-phrase check + bridge ────────
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
                        wav_path = fh.name
                    try:
                        _write_wav(wav_path, frames)
                        raw_text = transcribe(wav_path).strip()
                    except Exception:  # noqa: BLE001
                        log.exception("Transcription failed")
                        raw_text = ""
                    finally:
                        try:
                            os.unlink(wav_path)
                        except OSError:
                            pass

                    log.info("Transcribed: %r", raw_text)
                    text = raw_text
                    log.info("Command: %r", text)

                    # Fast path: time/date answered locally (no bridge, no LLM,
                    # prevents hallucination from routine-pattern-matching).
                    local_reply = _maybe_answer_locally(text)
                    if local_reply is not None:
                        reply = local_reply
                    elif not text or len(text) < 2:
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
                    _set_state(State.SPEAKING)
                    barge_loud_count = 0

                    completed = speak(reply)

                    if state == State.RECORDING:
                        # barge-in already flipped state; keep going
                        pass
                    else:
                        if completed:
                            _chime(DONE_CHIME)
                        state = State.IDLE
                        _set_state(State.IDLE)

                    time.sleep(0.15)


def _check_tts_quality() -> None:
    """Log loud WARNINGs at startup if Piper TTS is not properly installed.

    Called once from main() so the degraded state is visible in the log
    before any queries arrive.
    """
    if TTS_BACKEND == "say":
        log.info("TTS mode: macOS 'say' (configured explicitly via JACK_TTS_BACKEND=say).")
        return
    if not PIPER_MODEL:
        log.warning(
            "DEGRADED TTS: JACK_PIPER_MODEL not set. Jack will use robotic macOS 'say'. "
            "Set JACK_PIPER_MODEL=/path/to/model.onnx in ~/.jack_voice_config."
        )
        return
    try:
        import piper  # noqa: F401
        log.info("TTS: piper-tts Python package available. Model: %s", PIPER_MODEL)
    except ImportError:
        log.warning(
            "DEGRADED TTS: piper-tts Python package not installed. "
            "Jack will use robotic macOS 'say'. Install: pip install piper-tts"
        )


def main() -> None:
    _require("sounddevice", "sounddevice")
    _require("numpy", "numpy")
    _require("faster_whisper", "faster-whisper")

    _check_tts_quality()
    try:
        run()
    except KeyboardInterrupt:
        log.info("Jack Voice Service stopped.")
    except Exception:
        log.exception("Fatal error in Jack Voice Service")
        sys.exit(1)


if __name__ == "__main__":
    main()
