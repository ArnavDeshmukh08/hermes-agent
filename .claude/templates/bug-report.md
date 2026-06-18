# Bug Report — <short title>

> Skeleton for `agents/bug-hunter.md` and `agents/debugger.md`. **Diagnosis only — no fixes.**
> One report per finding. Fill every `<...>`; delete this quote block when filing.

- **Title:** <one-line summary of the issue>
- **Severity:** <CRITICAL | HIGH | MEDIUM | LOW>
- **Subsystem:** <Telegram | LLM-provider | memory | context-overflow | cron | tool-failure | VPS>
- **Owner:** <agent/skill that should act, e.g. context-compressor + skills-pruner via /context>

## Symptom
<what is observably wrong or at risk — be concrete>

## Evidence
<cite real sources — do not paraphrase from memory>
- Log: `~/.hermes/logs/<agent|gateway|errors>.log` — <line / quote>
- Config/SOUL/.env: `<path>` — <key / value>
- Source: `<path:line>` — <snippet>

## Suspected root cause
<the underlying cause, not the surface symptom>

## Reproduction
1. <step>
2. <step>
3. <observed result>

## Recommended fix (diagnosis only)
<what should change and why — propose, do not apply. Live edits go through /deploy.>

## Status
- [ ] Reported
- [ ] Confirmed by owner
- [ ] Routed to `/bug` for fix
