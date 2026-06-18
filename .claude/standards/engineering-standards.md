# Engineering Standards

> The operating rules every Hermes agent, skill, and command follows. Extends
> `.claude/CLAUDE.md` (reproduce→logs→subsystem→hypothesis→test→fix→validate→update docs).
> Where this file adds detail, `.claude/CLAUDE.md` still governs the dev workflow.

## 1. Root-cause first
- Fix root causes, never patch symptoms. A workaround that hides the failure is a regression.
- Every bug starts with logs (`~/.hermes/logs/agent.log`). No fix without evidence — see §2.
- If you cannot find the cause, say so. Do not ship a guess dressed as a fix.

## 2. Evidence before fix
- Reproduce or capture the symptom before proposing a change.
- Cite the evidence (log line, config key, token count) in the fix proposal.
- Diagnosis + read-only evidence gathering is unrestricted; live edits go through §5.

## 3. Context budget rule (the #1 constraint)
- **Interactive turns MUST stay < 12,000 tokens** (Groq free 12k TPM). Above this → 413 →
  compress → session reset. This is the dominant instability (`docs/AUDIT.md`).
- The fixed per-turn overhead is ~17k today, dominated by the skills-hub prompt (~10.6k).
  Target overhead **< 8k**.
- Any change that grows the always-on prompt must be rejected or made on-demand
  (`tools.tool_search`). New skills/personas/memory are budget spend — justify them.
- Deep dive: `skills/context-compressor.md`.

## 4. Three-path routing rule
Classify every unit of work into exactly one path and justify it (`agents/architect.md`):
- **Deterministic → `no_agent`** (zero LLM). Schedulers, formatters, fixed pipelines.
- **Heavy / heavy-reasoning → local Ollama** (per-job `provider/model/base_url` override or
  a `no_agent` script). Heavy work MUST NOT run on Groq free — it 413s.
- **Interactive → Groq 70B primary + local `llama3.1:8b` fallback.** Keep under the §3 budget.

## 5. Backup before live edit + surgical edits
- ALWAYS back up each touched file before a live change (timestamped `.bak`). Config-corruption
  history makes this non-negotiable (`skills/deployment-validator.md`).
- **Surgical edits only** — single keys / minimal hunks. Never full-file rewrites of
  `config.yaml`, `SOUL.md`, `.env`, or cron.
- One change at a time when feasible so validation can attribute cause→effect.
- Validate after every change (YAML parses → restart → functional check → no new errors).

## 6. Approval gates
These actions require explicit one-tap approval and are never auto-executed:
- Sending messages (Telegram/outreach) · spending money · deployments / live edits ·
  code merges.
- Auto (no approval): capture, reminders, research, drafting, read-only diagnosis.

## 7. Honesty about failures
- Surface broken steps; never fake success. A partial or "unclear" result beats a fabricated one.
- Mark status explicitly: done / partial / blocked / needs-evidence.

## 8. Prefer scripts over agent turns for long jobs
- Long-running or repeated work runs as a `no_agent` script or cron, not as agent turns.
  Cheaper, deterministic, and off the Groq budget.

## Pre-completion checklist
- [ ] Root cause identified with cited evidence (not a symptom patch)
- [ ] Work routed to the correct path; interactive turns < 12k tokens
- [ ] Backup taken; edit is surgical; validation steps included and run
- [ ] Approval obtained for any send / spend / deploy / merge
- [ ] Failures stated honestly; status marked
- [ ] `CONTEXT.md` + `MEMORY.md` updated if the change is notable (see `workflows/doc-sync.md`)
