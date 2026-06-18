# Hermes — System Audit

> Status: **verified** against the live VPS (`167.233.108.213`) read-only on 2026-06-15.
> Figures marked ✅ were confirmed against live files/source/logs this session.
> Figures marked 🗒 are "as recorded" in MEMORY.md and not independently re-measured.
> Companion docs: [ARCHITECTURE-DECISION.md](./ARCHITECTURE-DECISION.md),
> [ARCHITECTURE.md](./ARCHITECTURE.md), [ROADMAP.md](./ROADMAP.md).

---

## 0. TL;DR — why Hermes is unstable

**Every substantive turn exceeds the Groq free-tier budget and fails.** Hermes injects
a **fixed ~17k-token overhead** into every agent turn. Groq's free tier allows **12,000
tokens per minute (TPM)**. 17k > 12k, so the request returns **HTTP 413 "request too
large"** *before the model ever runs*. The 413 is routed to the compressor, but the
overhead is **fixed system context, not conversation** — there is nothing to compress —
so the framework gives up, **auto-resets the session**, and replies with a 218-char
error. This happens on **interactive chat too**, not just heavy cron jobs.

The dominant contributor to that overhead is the **skills hub prompt**, not tool schemas
and not AGENTS.md.

**Live proof (✅ gateway log, 2026-06-15 04:36):** user sent `het` (3 chars) in the
Vytal group → `HTTP 413 ... Limit 12000, Requested 17047 ... please reduce your message
size` → `413 payload too large. Cannot compress further.` → `Auto-resetting session
... after compression exhaustion.`

---

## 1. What Hermes is (system map)

Hermes runs the open-source **`hermes-agent` framework v0.16.0** (config schema v29) ✅,
installed as an editable source checkout at `~/.hermes/hermes-agent/` ✅
(Python 3.11 venv; the box's system Python is 3.14 ✅). It is a Telegram-first personal
assistant with two personas (Jack / Hamza), long-term memory, a kanban task system, a
cron scheduler, and a delegation/subagent orchestrator.

### Deployment topology
```
┌─────────────────────────┐        SSH reverse tunnel        ┌────────────────────────┐
│  VPS  167.233.108.213    │  localhost:11434  ◄────────────  │  Arnav's Mac (M1 Max)   │
│  Ubuntu, 2 vCPU, 3.7 GB  │                                  │  Ollama (Metal)         │
│  no swap ✅ (286 MB free) │                                  │  llama3.1:8b  [FALLBACK] │
│                          │                                  │  launchd persistence    │
│  systemd --user:         │                                  └────────────────────────┘
│   hermes-gateway.service │  active+enabled ✅
│   cwd = /home/hermes/.hermes ✅                              ┌────────────────────────┐
│   `python -m hermes_cli.main gateway run` ✅                 │  Groq free tier         │
│                          │  ───────────────────────────────►│  llama-3.3-70b-versatile│
│  Telegram (polling)      │   OpenAI-compatible, 12k TPM      │  [PRIMARY brain]        │
└─────────────────────────┘                                  └────────────────────────┘
```

### Request flow (interactive)
```
Telegram msg → gateway/run.py → session resolve (state.db) → agent_init (assemble
system prompt: base + SOUL.md + skills-hub prompt + tool schemas + memory) →
chat_completion_stream → Groq → [413 here] → error_classifier → compressor →
"cannot compress" → session auto-reset → 218-char error to Telegram
```

### Persona routing
`SOUL.md` (7,800 bytes ✅) defines **Jack** (personal chief-of-staff, Arnav's DMs) and
**Hamza** (Vytal ops, group `-1003797274797`). `agent.system_prompt` in config also
hard-codes a Hamza orchestrator preamble ✅. `telegram.allowed_chats` includes the Vytal
group `-1003797274797` ✅ (so the documented "Hamza bound to wrong group `-5439847434`"
concern is about SOUL.md's text, not the gateway allow-list).

### LLM routing
- **Primary:** `model.provider: custom` → Groq `llama-3.3-70b-versatile`,
  `max_context: 32768`, `max_tokens: 8192` ✅.
- **Fallback chain:** `fallback_providers: [{ provider: custom, model: llama3.1:8b,
  base_url: localhost:11434, context_length: 65536 }]` ✅.
- **Auxiliary tasks** (compression, curator, kanban_decomposer, vision, web_extract,
  approval, title, triage, tts_audio_tags, profile_describer, skills_hub, mcp) — all
  `provider: custom` → Groq ✅.
- **Delegation/subagents:** `delegation.orchestrator_enabled: true`,
  `max_concurrent_children: 3`, model `llama-3.3-70b-versatile` ✅ — a dispatcher
  primitive already exists in the framework.

### Reminder architecture
Zero-LLM by design: `remind.py` writes a per-reminder `.txt`+`.sh` sidecar and registers
a **`no_agent` cron job** that pipes script stdout to Telegram. Fire-time cost = 0 tokens
🗒 (script exists per MEMORY.md; helper not re-read this session).

---

## 2. Context size — what fills every turn

Measured/derived token budget for a single agent turn (~4 chars/token):

| Component | Source | Bytes | ~Tokens | Note |
|---|---|---:|---:|---|
| **Skills hub prompt** | `.skills_prompt_snapshot.json` ✅ | 42,569 | **~10,642** | **73 skills, 18 packs, 16 categories — DOMINANT** |
| Base + inline system prompt | framework + `agent.system_prompt` ✅ | — | ~2,000–3,000 | Hamza orchestrator preamble + tool-use scaffolding |
| Tool schemas | 5 toolsets (cronjob, memory, terminal, web, generate_image) ✅ | — | ~1,500–3,000 | loaded per platform_toolsets |
| SOUL.md | `~/.hermes/SOUL.md` ✅ | 7,800 | ~1,950 | dual-identity directives |
| USER.md | `~/.hermes/USER.md` ✅ | 1,177 | ~294 | Arnav profile |
| Memory injection | `memory_char_limit 2200` + `user 1375` ✅ | ~3,575 | ~900 | rolling |
| **Fixed overhead subtotal** | | | **~17,000** | **matches live 413: "Requested 17,047" ✅** |
| Groq free-tier ceiling | live error ✅ | | **12,000** | hard per-minute cap |

**Largest consumer: the skills hub prompt (~10.6k tokens / 62% of overhead).** Reducing
installed/injected skills is the single highest-leverage lever.

### Ruled out (with evidence)
- **AGENTS.md is NOT loaded** ✅. The framework's own `AGENTS.md` is 69,824 bytes
  (~17,456 tokens), but it lives at `~/.hermes/hermes-agent/AGENTS.md`. The gateway cwd
  is `/home/hermes/.hermes` ✅, which has **no** AGENTS.md/CLAUDE.md/.cursorrules, so
  `coding_context: auto` injects nothing. Earlier notes blaming AGENTS.md were wrong.
- **Toolset trimming is NOT effective** ✅. Trimming `platform_toolsets` had ~0 effect
  (25,975 → 25,958 🗒) because the **skills hub prompt loads independently of toolset
  selection**. The lever is skills, not toolsets.

---

## 3. Failure analysis (root-cause ladder)

| # | Symptom | Immediate cause | Root cause | Architectural cause | Status |
|---|---|---|---|---|---|
| 1 | All LLM calls → 404 ("dead brain") | `.env` had malformed `GOOGLE_API_KEY=AQ.…`; provider auto-selected Google while requesting a Groq model; `model.base_url` unset | Env-key auto-routing with no guard + missing base_url fallback | Provider resolution trusts `.env` presence over explicit `model.provider`; no startup validation | **FIXED** (key disabled, base_url set) ✅ |
| 2 | Old session auto-reset | Failed-retry history overflowed context | Self-healing reset | Intended behavior | OK |
| 3 | 400 errors + compression fail | `model.max_tokens` unset → huge default cap | Required field had no safe default | Config footgun: silent bad default instead of validate-at-boot | **FIXED** (`max_tokens: 8192`) ✅ |
| 4 | **413 on every substantive turn** (incl. interactive) | Fixed overhead ~17k > Groq 12k TPM | **Skills hub prompt ~10.6k + SOUL + system + schemas** injected every turn | Monolithic always-on context assembly sized for paid tiers, not a 12k free tier | **ACTIVE — primary instability** ✅ |
| 5 | 413 → "cannot compress further" → session reset | Compressor can't shrink fixed system context | Overhead is non-conversational, so compression has nothing to act on | 413 path assumes oversize = conversation; false for system overhead | **ACTIVE** ✅ |
| 6 | Heavy jobs never fail over to local | 413 classified `payload_too_large` → compress, **not** failover (`error_classifier.py:44`; failover advances only on `rate_limit`/`billing`, `chat_completion_helpers.py:1057`) | By design 413 ≠ failover trigger | No "request too large → bigger-context provider" route | **ACTIVE** (mitigated by pausing jobs) ✅ |
| 7 | Local 8B unusable for chat | 8B slow (29.7s 🗒) + leaked raw tool-call JSON | Model too weak for tool-calling hot path | Capacity mismatch | **MITIGATED** (8B = fallback only) 🗒 |
| 8 | Cron jobs show stale Google-404 | Both heavy jobs (`Learning Engine`, `Daily AI…`) last ran 06-14 07:00 *before* the 08:08 fix, then paused | Never re-ran post-fix | The "they 413 on Groq" reason is **inferred**, not freshly observed for these jobs ✅ | **PAUSED** |

**Convergent root cause (4+5+6):** an always-on ~17k context meets a 12k ceiling, and
*neither* recovery path can help — compression can't shrink fixed overhead, and 413
doesn't trigger failover. The only real fixes are **shrink the overhead** or **route to a
higher-capacity provider**.

---

## 4. Capabilities that already exist in the framework (don't rebuild)

Verified in config/source — relevant to the roadmap:
- **Per-job LLM override** ✅ — `jobs.json` entries carry `provider`/`model`/`base_url`/
  `no_agent`/`script`/`context_from`; `_resolve_model_override` resolves them (tested in
  `tests/tools/test_cronjob_tools.py`). Heavy jobs *can* be pinned to local Ollama today.
- **`no_agent` cron** ✅ — runs a script with zero LLM (the reminder pattern).
- **Delegation/subagents** ✅ — `delegation.orchestrator_enabled` with child spawn depth
  — a foundation for the dev-dispatcher.
- **STT/TTS are cloud + free-ish** ✅ — `stt.provider: groq` (Whisper), `tts.provider:
  edge`. Voice-in/out may not require local Whisper/Piper or a VPS swap as previously
  assumed — re-scope the voice layer accordingly.
- **Compression engine, kanban auto-decompose, model catalog, fallback chain** — all
  present and configured.

---

## 5. Security & hygiene findings

- ⚠️ **Groq key exposed in two places** ✅ — `model.api_key`/`environment.GROQ_API_KEY`
  in `config.yaml` *and* `GROQ_API_KEY` in `.env`. Rotate + single-source it.
- ⚠️ **`.env` is 487 lines / 24 KB** ✅ — far larger than the ~20 real keys; carries X
  (Twitter) keys, Vytal Gmail app password, sheet IDs, browser/stealth flags. Audit and
  prune; ensure none leak into prompts/logs.
- 🧹 Dead `GOOGLE_API_KEY` still present (disabled) 🗒 — remove on cleanup.
- 🧹 `context_length_cache.yaml` references stale `qwen2.5:14b` ✅ — harmless, tidy later.
- 🧹 18 config.yaml backups incl. a 63 KB `.bak` and a `.corrupt` from 06-13 ✅ — history
  of full-rewrite corruption; **always surgical-edit + validate**.
- ✅ SSH is key-only; sudo password rotation deferred (MEMORY.md).
- ✅ `state.db` = 8.96 MB, 52 sessions / 788 messages; FTS + trigram tables present.

---

## 6. What "verified" leaves open

- The exact split between base system prompt vs tool schemas (the ~2–3k "system" line)
  was not itemized — the skills-hub dominance is unambiguous regardless.
- The two heavy cron jobs were never observed running post-fix; their Groq behavior is
  inferred from the turn-size math.
- `remind.py` internals were not re-read this session (assumed per MEMORY.md).
