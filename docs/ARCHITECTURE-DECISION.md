# Architecture Decision — Is `hermes-agent` the right foundation?

> Decision record. Evidence base: [AUDIT.md](./AUDIT.md). Decided with Arnav 2026-06-15.

## Question
Hermes is unstable (every substantive turn 413s on Groq's 12k TPM because of a ~17k
fixed context). Should we keep the `hermes-agent` framework, bypass parts of it, replace
components, or rewrite?

## Decision: **Option B — keep `hermes-agent`, route work around its monolithic agent loop.** ✅ LOCKED

## Options considered

| Option | What it means | Pros | Cons / Risk | Scalability | Maintenance |
|---|---|---|---|---|---|
| **A. Keep unchanged** | Live with the framework as configured | Zero work | Leaves the 17k>12k mismatch → keeps 413-ing; config footguns remain | None | Low effort, high incident rate |
| **B. Keep + bypass parts** ✅ | Use Hermes for gateway/memory/personas/skills/kanban; (1) shrink the always-on context under 12k, (2) run deterministic + heavy work *around* the agent loop via `no_agent`/per-job override/direct-to-Ollama | Lowest risk; builds on proven `remind.py`; preserves everything that works; all levers already exist in-framework (per-job override ✅, no_agent ✅, fallback chain ✅); "pay when it earns" friendly | Requires discipline to keep context lean; some heavy work lives in scripts beside the framework | Good — scales by moving load off the free tier, not by rewriting | Moderate, mostly config + small scripts |
| **C. Infra-only + replace components** | Keep process/Telegram/memory but reimplement context assembly + routing | Full control over the 17k problem | Re-implementing prompt assembly/provider routing is deep surgery on a fast-moving upstream; merge pain on every update | Good if completed | High — you now own forked internals |
| **D. Full rewrite** | Drop `hermes-agent`, build custom | Perfect fit long-term | Throws away working Telegram + memory + personas + skills + cron + delegation; months of work for a solo founder; reintroduces every solved bug | Highest ceiling, lowest near-term | Very high |

## Why B (and not C/D)

1. **The instability is configuration-shaped, not architecture-shaped.** AUDIT §2–3
   show the failure is a fixed ~17k context (62% of it the skills-hub prompt) meeting a
   12k ceiling. That is fixable by **shrinking what's injected** and **moving heavy work
   off the free tier** — neither requires owning the framework internals (C) or
   rewriting (D).
2. **Every lever B needs already exists in-framework** (AUDIT §4): per-job
   `provider/model/base_url` override (tested), `no_agent` script jobs, a fallback chain,
   a delegation/subagent orchestrator, and cloud STT/TTS. We configure, we don't rebuild.
3. **It compounds the one thing already proven to work** — the zero-token `remind.py`
   pattern. B generalizes "deterministic/heavy → bypass the agent loop."
4. **Risk & cost.** Solo founder, "pay when it earns." C/D spend weeks re-earning
   today's working surface. B's worst case is a config rollback (backups exist).

## What B commits us to (the dual-path model)
```
INTERACTIVE   → Hermes agent loop → Groq 70B   (only viable once overhead < 12k)
                                   ↘ local 8B fallback (outages)
DETERMINISTIC → no_agent scripts   (zero LLM)              e.g. reminders ✅
HEAVY REASON  → per-job override / direct curl → local Ollama (65k ctx, no TPM cap)
```

## Hardening guards B implies (documented now; applied in a future *live* mission)
1. **Shrink the always-on context < 12k** — prune skill packs / make the skills hub
   on-demand via `tools.tool_search` (biggest lever: ~10.6k → ~2k).
2. **Per-job routing for heavy/cron jobs** — set `provider/model/base_url` to local
   Ollama, or convert to `no_agent` scripts.
3. **Treat 413 as failover-eligible OR pre-flight size-check** — so oversize requests
   route to a bigger-context provider instead of dying in the compressor.
4. **Config validation at boot** — never allow unset `max_tokens`/`base_url`; warn if
   estimated turn size > primary provider's per-request budget.
5. **Provider routing must honor explicit `model.provider`** over `.env` key presence.

## Out of scope for the current mission
This mission delivers the docs + the `.claude/` operating framework **only**. The live
guards above are deferred to a separate, approval-gated live-change mission
(see [ROADMAP.md](./ROADMAP.md)).

## Revisit criteria
Re-open this decision if: upstream `hermes-agent` churn makes config-level control
impossible; or interactive load outgrows what a lean context + free/paid Groq tier can
serve; or the dev-dispatcher needs orchestration the built-in delegation can't express.
