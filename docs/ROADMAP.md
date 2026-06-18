# Hermes — Roadmap

> Build order toward [ARCHITECTURE.md](./ARCHITECTURE.md). Evidence: [AUDIT.md](./AUDIT.md).
> Phase 0 fixes are the live-change work this mission deliberately deferred — they are
> approval-gated and must each be backed up + validated.

## Phase 0 — Stabilize (highest priority, live changes)
Goal: interactive chat stops 413-ing. Success = a normal Telegram turn returns a real
reply, request size < 12k.

1. **Cut the skills-hub overhead** (biggest lever, ~10.6k → ~2k tokens).
   - Prune installed skill packs from 18 to the few in use; and/or gate the skills hub
     behind `tools.tool_search` so skills load on demand, not every turn.
   - Validate: re-send `het`; confirm log shows `Requested < 12000` and a real reply.
2. **Single-source + rotate the Groq key** — remove from one of config/`.env`; rotate.
3. **Confirm provider routing** honors explicit `model.provider` (re-test after key work).
4. **Fix Hamza's group ID in SOUL.md** (text says `-5439847434`; live group is
   `-1003797274797`) — confirm with Arnav first.
5. **Prune `.env`** (487 lines) to real, used keys; ensure none reach prompts/logs.

## Phase 1 — Harden the second brain (foundation)
6. **Generalize the deterministic path** beyond reminders (recurring digests,
   nudges) using `no_agent` jobs.
7. **Re-enable heavy cron via local Ollama** — set per-job `provider/model/base_url` to
   the Mac tunnel (proven supported), or convert to `no_agent` scripts. Un-pause
   `Learning Engine` + `Daily AI…` once routed off Groq.
8. **Preference learning** — wire early follow-up questions + memory writes; keep within
   `memory_char_limit`.
9. **Config-validation guard** — fail/warn at boot on unset `max_tokens`/`base_url` and
   on estimated turn size > provider budget.

## Phase 2 — Resilience & observability
10. **413 → failover (or pre-flight size route)** so oversize requests reach a
    bigger-context provider instead of the compressor dead-end.
11. **Turn-size telemetry** in logs (estimated tokens vs budget; warn within 20%).
12. **`hermes-doctor`** routine as a scheduled health check.

## Phase 3 — Voice (re-scoped)
13. Use cloud STT/TTS already configured (`stt: groq` Whisper, `tts: edge`) for
    voice-in/out — likely avoids local Whisper/Piper and the VPS-swap blocker. Validate
    memory headroom before any local model.

## Phase 4 — Dev-team dispatcher (the paid piece)
14. Build on the built-in `delegation` orchestrator: idea-dump → spec (`dispatcher`
    agent) → subagents → reviewable diff/summary. Merges stay approval-gated. Spend only
    once it earns.

## Phase 5 — Outreach engine (semi-auto, approval-gated)
15. Route `hamza_orchestrator` generation to local Ollama; lead-find + draft overnight;
    **sends one-tap approved**; DPDP/anti-spam compliant.

## Deferred / maybe
- Food ordering via headless browser (fragile, low priority).
- Always-on local heavy model on the VPS (blocked by 3.7 GB RAM / no swap).

## Sequencing rule
Do **Phase 0 first** — until interactive turns fit under 12k, everything above the
deterministic path is unreliable.
