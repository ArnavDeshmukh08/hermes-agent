# Phase 2 Fixes Report — Jack Voice Assistant

**Date**: 2026-06-24  
**Status**: Code fixes applied. Piper install required by Arnav (see §2).

---

## §1 — What Was Fixed

### Bug 1: Jack Hallucinated Time (CRITICAL HONESTY BUG)

**Root cause**: "What time is it?" fell through the intent router as `conversational` → hit the Groq LLM with Arnav's USER.md profile (which says he wakes at 8:30 AM and calls Siddhi). The LLM pattern-matched his routine and fabricated "It's around 8:30 am, you just woke up and called Siddhi, right?" at 23:25 IST.

**Fix**: Three-layer interception — all close the LLM path for time/date queries:
- **Mac-side fast path** (`voice_service.py`): Detects time/date queries _before_ the bridge call. Answers directly from `datetime.now(ZoneInfo("Asia/Kolkata"))`. No network round-trip, no LLM.
- **VPS intent router** (`jack_intent_router.py`): New `time_date` intent. All Discord/voice queries matching "what time is it", "what's today's date", etc. route here first.
- **VPS conversation handler** (`conversation.py`): Guard in `JackConversationHandler.respond()` before any LLM call. Even if a time query bypasses the intent router, it is answered from the clock.

**Latency change** (for voice "what time is it"):
- Before: 4–5s (Whisper STT → bridge → Groq LLM round-trip → Piper/say)
- After: < 1s (Whisper STT → local datetime answer → say)
- Improvement: ~4–5x faster for time queries

### Bug 2: Silent TTS Fallback (OBSERVABILITY BUG)

**Root cause**: `_synth_wav()` returned `False` silently with a `DEBUG` log when Piper wasn't available. `speak()` fell back to macOS `say` (robotic voice) with no warning at all.

**Fix**:
- `_synth_wav()`: Upgraded to `WARNING` level when Piper binary and Python package are both absent.
- `speak()`: Logs loud `WARNING` on every call when falling back from Piper to `say`.
- `_check_tts_quality()`: New startup check called from `main()` — emits a bold WARNING at boot if Piper isn't configured/installed. Never silently degrades again.

Check `/tmp/jack-voice-service.log` for TTS status after restart.

---

## §2 — Install Piper TTS (ACTION REQUIRED)

Piper was never successfully installed. Below are the exact commands for macOS arm64 (Apple Silicon, M1/M2/M3).

### Option A — Binary install (recommended, Python-version independent)

```bash
# 1. Download Piper binary for macOS arm64
cd /tmp
curl -L "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_macos_aarch64.tar.gz" -o piper.tar.gz

# 2. Extract and install
tar -xzf piper.tar.gz
sudo cp piper/piper /usr/local/bin/piper
sudo chmod +x /usr/local/bin/piper
rm -rf piper piper.tar.gz

# 3. Verify
piper --version
which piper   # should print /usr/local/bin/piper
```

If the release URL above is outdated, find the latest at:
`https://github.com/rhasspy/piper/releases` → look for `piper_macos_aarch64.tar.gz`

### Option B — Python package (may have arm64 issues on Python 3.13)

```bash
pip install piper-tts
# Test:
python -c "from piper import PiperVoice; print('piper-tts OK')"
```

If you get a `piper-phonemize` build error on arm64/Python 3.13, use Option A (binary) instead.

---

### Download male voice model

```bash
# Create voice model directory
mkdir -p ~/piper-voices

# Download en_US-ryan-high (American male, high quality)
curl -L "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ryan/high/en_US-ryan-high.onnx" \
     -o ~/piper-voices/en_US-ryan-high.onnx

# Download required config file
curl -L "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ryan/high/en_US-ryan-high.onnx.json" \
     -o ~/piper-voices/en_US-ryan-high.onnx.json

# Verify download (both files required)
ls -lh ~/piper-voices/
```

Alternative if ryan is unavailable (also male):
```bash
# en_US-joe-medium (American male, medium quality, smaller file)
curl -L "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/joe/medium/en_US-joe-medium.onnx" \
     -o ~/piper-voices/en_US-joe-medium.onnx
curl -L "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/joe/medium/en_US-joe-medium.onnx.json" \
     -o ~/piper-voices/en_US-joe-medium.onnx.json
```

---

### Configure Jack

```bash
# Add to ~/.jack_voice_config (creates file if missing)
cat >> ~/.jack_voice_config << 'EOF'

# Piper TTS — natural male voice
JACK_TTS_BACKEND=piper
JACK_PIPER_BINARY=/usr/local/bin/piper
JACK_PIPER_MODEL=/Users/arnav/piper-voices/en_US-ryan-high.onnx
EOF

# Verify config
grep PIPER ~/.jack_voice_config
```

---

### Test Piper synthesis

```bash
# Test the voice BEFORE reloading the service
echo "Hello Arnav, I am Jack and this is my new natural male voice." \
  | piper \
    --model ~/piper-voices/en_US-ryan-high.onnx \
    --output_file /tmp/jack-piper-test.wav \
  && afplay /tmp/jack-piper-test.wav

# If you hear natural speech (not robotic), Piper is working.
```

---

### Reload the voice service

```bash
# After confirming piper test above sounds good:
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hermes.voice.plist 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hermes.voice.plist

# Confirm running
launchctl list | grep hermes.voice

# Check logs for TTS startup status
tail -20 /tmp/jack-voice-service.log
# Should see: "TTS: Piper binary found at '/usr/local/bin/piper'"
# Should NOT see: "DEGRADED TTS"
```

---

## §3 — Latency Summary

| Query type | Before (Phase 2) | After fix |
|---|---|---|
| "What time is it?" | 4–5s (Whisper + bridge + Groq LLM) | <1s (Whisper + local datetime) |
| "Remind me to call mom" | 4–5s (bridge + Groq) | 4–5s (unchanged — LLM needed) |
| "How did I sleep?" | 4–5s (bridge + Groq + Garmin) | 4–5s (unchanged) |

Time and date queries are now the fastest possible: Whisper transcription is the only bottleneck.

---

## §4 — Verifying All Fixes

After installing Piper and reloading the service:

1. **Time test** (speak aloud): "Hey Jack, what time is it?"
   - Expected: instant reply with real IST time (e.g. "It's 11:45 PM IST — Tuesday, 24 June 2026")
   - Must NOT be a fabricated time from your routine
   
2. **Voice quality test**: Say anything to Jack
   - Expected: natural male Piper voice (not robotic)
   - Check `/tmp/jack-voice-service.log` — should NOT see "DEGRADED TTS"

3. **Menu bar indicator**: Should animate ◦ → ◉ → ◎ → ● → ◦ during a query

4. **Log check**:
   ```bash
   tail -30 /tmp/jack-voice-service.log
   ```
   Look for:
   - `"TTS: Piper binary found"` — Piper active
   - `"Local fast-path (time/date)"` — time fast-path firing
   - No `"DEGRADED TTS"` lines
