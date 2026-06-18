# Hermes — Target Architecture (12–24 months)

> The future-state design under [Option B](./ARCHITECTURE-DECISION.md). Grounded in
> [AUDIT.md](./AUDIT.md). Build order is in [ROADMAP.md](./ROADMAP.md).

## Design principles
1. **Lean always-on context.** Every interactive turn must fit comfortably under the
   primary provider's per-request budget (free Groq = 12k TPM). Inject only what the turn
   needs; make everything else on-demand.
2. **Right path for the work.** Deterministic → no LLM. Heavy reasoning → high-capacity
   provider off the rate-limited tier. Interactive → fast primary + fallback.
3. **Fail loud, recover deterministically.** Validate config at boot; never silently
   accept a bad default; never enter a 413→reset loop.
4. **Free + reliable over clever + fragile.** Pay only when a capability earns.
5. **Approval-gated side effects.** Sends, spend, orders, code merges require one-tap
   approval.

## The three execution paths
```
                         ┌──────────────────────────────────────────────┐
  Telegram (Jack/Hamza)  │  INTERACTIVE PATH                             │
  ───────────────────────►  hermes-gateway → agent loop                 │
                         │   context = base + persona + LEAN skills(+memory)  ◄─ < 12k
                         │   primary: Groq 70B   ↘ fallback: local 8B    │
                         └──────────────────────────────────────────────┘
                         ┌──────────────────────────────────────────────┐
  "remind me…",          │  DETERMINISTIC PATH (zero LLM)                │
  scheduled deliveries   │   no_agent cron → script → Telegram          │
  ───────────────────────►  remind.py pattern, generalized              │
                         └──────────────────────────────────────────────┘
                         ┌──────────────────────────────────────────────┐
  Learning Engine,       │  HEAVY-REASONING PATH (off the free tier)    │
  social drafts,         │   per-job provider override OR direct curl   │
  outreach generation    │   → local Ollama (65k ctx, no TPM cap)       │
  ───────────────────────►  results delivered via Telegram on completion │
                         └──────────────────────────────────────────────┘
```

## Layers

### Agents (operating + product roles)
The product personas (Jack, Hamza) live in `SOUL.md` on the box. The **operating**
agents — used by Claude Code to build/run/debug Hermes — live in `.claude/agents/`:
`architect`, `debugger`, `infrastructure`, `memory-manager`, `dispatcher`,
`outreach-manager`. One responsibility each, no overlap (see each file's header).

### Skills / context management (the core fix)
- **On-demand skills.** Replace the always-injected 42.5 KB / ~10.6k-token skills-hub
  prompt with `tools.tool_search`-gated retrieval, and prune installed packs from 18 to
  the few Hermes actually uses. Target fixed overhead **< 8k tokens**.
- **Lean persona.** Keep SOUL.md tight; load only the active persona's section.
- **Budgeted memory.** Keep `memory_char_limit`/`user_char_limit` modest; prefer recall
  over always-on injection.

### Memory layers
1. **Working memory** — current session (`state.db` sessions/messages, FTS+trigram).
2. **Profile/preferences** — `USER.md` + memory store; learns Arnav's preferences;
   asks early to learn faster.
3. **Task state** — `kanban.db` (auto-decompose, dispatcher).
4. **Durable knowledge** — idea vault / lead+bug DB (Hamza), curated periodically by the
   built-in curator.
A `memory-manager` operating agent owns compaction, archival, and growth limits.

### Routing
- **Interactive:** explicit `model.provider` (Groq) → fallback chain (local 8B). Routing
  must honor the explicit provider over `.env`-key presence.
- **Heavy/scheduled:** per-job `provider/model/base_url` → local Ollama, or `no_agent`.
- **Auxiliary tasks:** small/cheap; keep on Groq but ensure they're not bloating turns.

### Tool access
Per-platform `platform_toolsets` stays minimal (Telegram: cronjob, memory, terminal,
web, generate_image). Tools are cheap relative to the skills prompt — the win is skills,
not tools. Side-effecting tools (sends, email, posts) stay approval-gated.

### Logging & observability
- Structured logs at `~/.hermes/logs/{agent,gateway,errors}.log`.
- **Add a turn-size guard**: log estimated request tokens vs the provider budget before
  each call; warn when within 20% of the ceiling. (Future live change.)
- Health via `hermes_cli ... doctor` and the `hermes-doctor` skill.
- Incidents recorded with `.claude/templates/incident-report.md`.

## Capability map (where each product goal lands)
| Capability | Path | Mechanism | State |
|---|---|---|---|
| Second brain + reminders | Deterministic + Interactive | `remind.py`/`no_agent`; memory store | reminders ✅; brain hardening pending |
| Voice (Jarvis) | Interactive | `stt: groq` (Whisper) + `tts: edge` — cloud, re-scoped | re-scope, likely cheaper than local |
| Dev-team dispatcher | Heavy | built-in `delegation` orchestrator → spec → subagents → review | foundation exists |
| Outreach engine | Heavy + approval | `hamza_orchestrator` scripts → local Ollama; sends gated | scaffolded, paused |
| Startup chief-of-staff | Interactive + Task | Hamza persona + kanban | live |

## Non-goals
- No fork of `hermes-agent` internals (that's Option C, rejected).
- No always-on local heavy model on the VPS (3.7 GB RAM, no swap) — heavy reasoning runs
  on the Mac via tunnel.
