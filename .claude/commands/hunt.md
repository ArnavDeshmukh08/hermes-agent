# /hunt — proactively scan for latent bugs (diagnose only)

**Purpose:** Run a forward-looking sweep of logs, config, and source to surface latent
issues *before* they break a turn — without fixing anything. Wraps the `bug-hunter` agent.

## Usage
`/hunt` — full sweep. `/hunt <area>` — scope to a subsystem (e.g. `/hunt context`,
`/hunt cron`, `/hunt provider`).

## What it does
1. Invokes `agents/bug-hunter.md` to scan `~/.hermes/logs/{agent,gateway,errors}.log`,
   the live config/SOUL/`.env`, and source for latent issues (budget creep toward 12k,
   unset `max_tokens`/`base_url`, stale IDs, paused crons, swallowed errors).
2. Cross-references findings against `docs/AUDIT.md` so known issues aren't re-reported
   as new.
3. Emits one bug report per finding, ranked by severity.

## Output
One filled `.claude/templates/bug-report.md` per finding (severity · subsystem · evidence
· suspected root cause · recommended fix · owner). **Diagnosis only.**

## Boundaries / Note
Read-only — `bug-hunter` never fixes. Confirmed findings go to `/bug` to fix and `/deploy`
to apply (backup + validate + approval).
