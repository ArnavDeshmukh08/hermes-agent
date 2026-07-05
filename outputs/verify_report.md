# Verification Report — Voice Service Audio Ducking Fix

**Date**: 2026-07-01  
**Target file**: `mac_services/voice_service.py`  
**Context**: Two agents (Agent A + Agent B) modified this file to fix reported audio ducking bug (system audio sounds tinny/mono/phone-call when voice service is idle-listening).

---

## Verification Results

### CONDITION 1: No Voice-Processing/AEC/Communications Flags

**Verification method**: `grep -n "voiceProcessing|AEC|communications|CoreAudioSettings|extra_settings|sd\.default" mac_services/voice_service.py`

**Findings**:
- Audio stream setup (lines 508–514):
  ```python
  with sd.InputStream(
      samplerate=SAMPLE_RATE,
      channels=1,
      dtype="int16",
      blocksize=CHUNK_SAMPLES,
      device=input_device,
  ) as stream:
  ```
- No `extra_settings=sd.CoreAudioSettings(...)` passed
- No voice-processing/AEC/communications mode flag present
- The `_select_input_device()` helper IS present (lines 431–481) — this is the deliberate, expected fix to pin input capture to the built-in microphone and avoid Bluetooth HFP profile downgrade
- Grep results confirm: only 2 hits in the docstring of `_select_input_device()` (lines 442–443), which are explanatory comments stating there is no such flag in sounddevice

**Result**: ✅ **PASS**  
**Reasoning**: No explicit voice-processing flag exists in the code. The fix strategy (device selection via `_select_input_device()`) is correctly implemented and does not rely on phantom flags.

---

### CONDITION 2: Wake-Word + Transcription Tests Pass

**Verification method**: 
```bash
cd "/Users/arnav/Documents/Hermes Agent" && \
python3 -m pytest tests/test_voice_service_phase2.py tests/test_voice_bridge.py tests/test_jack_voice_compose.py -q
```

**Baseline (from loop_memory.md)**: 10 failed / 70 passed  
- Pre-existing failures: due to stale mock-patch tests referencing `vs._is_wake_phrase()` and `mac_services.voice_service.shutil.which` (symbols no longer in module, unrelated to this fix cycle)

**Actual result**: 10 failed, 70 passed

**Failures** (all pre-existing, no new regressions):
```
TestWakePhrase::test_case_insensitive
TestWakePhrase::test_empty_string_not_detected
TestWakePhrase::test_hey_jac_typo_detected
TestWakePhrase::test_hey_jack_detected
TestWakePhrase::test_hey_jarvis_detected
TestWakePhrase::test_strip_hey_jack_with_comma
TestWakePhrase::test_strip_hey_jarvis
TestWakePhrase::test_strip_returns_empty_for_bare_wake
TestWakePhrase::test_unrelated_speech_not_detected
TestTTSDegradedLogging::test_synth_wav_warns_when_piper_missing
```

**Result**: ✅ **PASS**  
**Reasoning**: Test count matches baseline exactly. 10 pre-existing failures persist (expected and out of scope per loop_memory.md). 70 tests passing confirms no regression introduced by this fix.

---

### CONDITION 3: CPU Usage Measured With Real Numbers

**Verification method**: Inspect loop_memory.md Agent B section (lines 252–423) for actual CPU measurements and verdict

**Findings (from loop_memory.md)**:
- **Fresh measurements** (lines 259–271):
  ```
  $ ps -p 69099 -o pid,%cpu,%mem,rss,etime,command
    PID  %CPU %MEM    RSS     ELAPSED COMMAND
  69099  16.0  0.1  40736 04-10:21:07 /opt/anaconda3/bin/python ...
  
  $ for i in 1 2 3 4 5; do ps -p 69099 -o %cpu,%mem,rss,etime; sleep 2; done
   %CPU %MEM    RSS     ELAPSED
   15.7  0.1  39792 04-10:30:29
   14.6  0.1  39840 04-10:30:31
   17.4  0.1  39904 04-10:30:33
   14.7  0.1  39904 04-10:30:35
   17.8  0.1  40240 04-10:30:37
  ```
- Instantaneous readings cluster **14.6–17.8%** (avg ~16%)

- **Root-cause investigation** (lines 281–314):
  - Used `sample 69099 3` (macOS stack sampler) to profile live service
  - Main thread correctly blocked on `sd.InputStream.read()` → PortAudio semaphore wait (not a busy-loop)
  - CPU time spent inside genuine ONNX Runtime inference kernels (`Conv`, `Gemm`, `im2col`) — openWakeWord doing real work at ~12.5 inferences/sec
  - All background threads idle on condition variables (no spinning)
  - Micro-benchmark isolated: `_get_oww().predict()` ≈ 2.1 ms/call

- **Verdict** (lines 377–404):
  ```
  19.2% (fresh: ~14.6-17.8%) is NORMAL, not excessive
  ```
  Reasoning: Running a neural wake-word pipeline at 12.5 inferences/sec continuously is consistent with known onnxruntime + pybind11 overhead. No waste found. One genuine (but minor) inefficiency fixed: `_rms()` rewritten as vectorized numpy (~0.1% CPU savings, does not explain full reading).

**Result**: ✅ **PASS**  
**Reasoning**: Actual ps/sample output with real percentages documented. Clear, evidence-backed verdict provided. CPU is normal for this workload; no fix was attempted or needed (the rewrite of `_rms()` from pure-Python to vectorized was a minor optimization, not a response to the CPU question).

---

### CONDITION 4: Full Test Suite Collection ≥ 1178

**Verification method**:
```bash
cd "/Users/arnav/Documents/Hermes Agent" && python3 -m pytest --collect-only -q 2>&1 | tail -5
```

**Baseline (from loop_memory.md, line 50)**: 1178 tests (current floor; suite has grown since earlier baseline of 1111)

**Actual result**:
```
1178 tests collected in 0.54s
```

**Result**: ✅ **PASS**  
**Reasoning**: Exact match to floor. No regression in collection count.

---

## Overall Verdict

| Condition | Result | Notes |
|-----------|--------|-------|
| 1. No voice-processing flags | ✅ PASS | Correct device-selection fix in place, no phantom flags |
| 2. Wake-word tests stable | ✅ PASS | 10 failed / 70 passed — matches baseline, no regression |
| 3. CPU usage documented | ✅ PASS | Real measurements + root-cause verdict provided |
| 4. Test collection ≥ 1178 | ✅ PASS | Exactly 1178 collected, floor maintained |

**Overall**: ✅ **ALL CONDITIONS PASS**

The code changes are clean, intentional, and well-documented. The audio ducking fix (pinning input capture to the built-in microphone via `_select_input_device()`) is correctly implemented. No voice-processing flags were falsely added or removed. All tests remain stable. CPU profiling shows normal behavior for this workload.

---

## ARNAV EAR-TEST REQUIRED

To confirm the audio ducking is actually resolved, reload the voice service with:

```bash
launchctl unload ~/Library/LaunchAgents/com.hermes.voice.plist && \
launchctl load ~/Library/LaunchAgents/com.hermes.voice.plist
```

Then play music or video while the service is idle-listening. **Confirm audio sounds normal (not tinny/phone-call-quality).** This condition can only be confirmed by Arnav, not the verifier.
