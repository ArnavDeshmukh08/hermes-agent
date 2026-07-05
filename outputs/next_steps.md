# Next Steps — fix-voice-service-audio-ducking (Cycle 1)

## Status: code-side work COMPLETE, pending Arnav's ear-test

## What was fixed

1. **Audio capture mode** (`mac_services/voice_service.py`): Diagnosed that macOS
   was never actually being asked for a voice-processing/AEC/communications
   mode by this code (`sounddevice`/PortAudio's CoreAudio backend has no such
   flag — confirmed by reading `sd.CoreAudioSettings` source). The real
   mechanism matching the reported symptom is a **Bluetooth HFP profile
   downgrade**: opening *any* mic input stream on a Bluetooth headset (e.g.
   AirPods) forces macOS to renegotiate that device from A2DP (hi-fi stereo)
   to HFP (mono, ~8-16kHz phone-call quality) — and since the profile is
   per-device, this also degrades the same device's *output* path, which is
   exactly why system media sounded tinny/mono while Jack was idle-listening.
   Fix: added `_select_input_device()`, which pins mic capture to the
   built-in microphone by name whenever present, so this service never
   touches the Bluetooth device's input path and the OS has no reason to
   drop it out of A2DP. Falls back to OS default (old behavior) if no
   built-in mic is found, with a clear warning log.

2. **CPU sanity check**: Measured live process at **~14.6–17.8% CPU** (fresh
   samples), consistent with the originally reported 19.2%. Root-caused via
   macOS `sample` profiler: this is genuine ONNX Runtime inference time
   (openWakeWord running ~12.5 inferences/sec while idle-listening) — not a
   busy-loop or inefficiency. **Verdict: normal, no fix needed for this.**
   One free, minor optimization was made along the way: `_rms()` (called
   every 80ms chunk regardless of state) was rewritten from a pure-Python
   sum-of-squares generator to vectorized `numpy` (~24x faster per-call,
   ~97µs → ~4-5µs), though this doesn't meaningfully move the 16-19% figure.

## Verification (code-side, all PASS — see outputs/verify_report.md)

- No voice-processing/AEC/communications flag present (there never was one to remove).
- Wake-word + transcription tests: 10 failed / 70 passed — matches pre-existing
  baseline exactly (those 10 failures are unrelated stale mock-patch issues
  from before this cycle — `shutil`/`_is_wake_phrase` references that predate
  Cycle 1 and were explicitly out of scope).
- Full suite collection: 1178 tests (floor maintained, no drop).
- CPU measured with real numbers, verdict documented with evidence.

## REQUIRED: Arnav's ear-test (cannot be automated)

1. Reload the service:
   ```bash
   launchctl unload ~/Library/LaunchAgents/com.hermes.voice.plist && \
   launchctl load ~/Library/LaunchAgents/com.hermes.voice.plist
   ```
2. Play music or video while the service is idle-listening (before saying the wake phrase).
3. Confirm system audio sounds normal — full stereo/hi-fi, not tinny/mono/phone-call quality.
4. If you normally use a Bluetooth headset (AirPods etc.) as your *output* device: this
   fix should keep it on A2DP now, since the input stream no longer touches it. If you
   were *intentionally* using a Bluetooth mic as input (not the built-in mic), this fix
   will not apply to that specific case — the only remedy there is OS-level (disable
   that device's automatic hands-free profile switching, or avoid using its mic while
   Jack is running).

## Pre-existing, out-of-scope issues noticed (not touched this cycle, flagging for later)

- `tests/test_voice_service_phase2.py` has 10 tests (`TestWakePhrase::*`,
  `TestTTSDegradedLogging::test_synth_wav_warns_when_piper_missing`) that
  reference `shutil` / `_is_wake_phrase`, neither of which exists in the
  current module — these predate this cycle (from earlier, uncommitted
  Piper-TTS-migration and wake-phrase-refactor work) and were explicitly out
  of scope per the Jack Rules ("do not modify wake-word sensitivity, Piper
  voice, or state machine logic"). Someone should fix these test/source
  drift issues in a future cycle.
- The repo has a large amount of **uncommitted work** sitting in the working
  tree on `mac_services/voice_service.py` from prior sessions (Piper Python
  package migration, time/date fast-path, voice state indicator, wake-phrase
  fuzzy matching, etc.) — none of that was committed to git before this
  cycle started. Worth a `git add`/commit pass once Arnav confirms everything
  still works end-to-end, so this isn't all sitting fragile and uncommitted.
