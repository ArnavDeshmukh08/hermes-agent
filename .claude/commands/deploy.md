# /deploy — apply a live change safely

**Purpose:** Make an approved change to the live VPS Hermes with backup, validation, and
rollback. Wraps the `deployment-validator` skill + `infrastructure` agent.

## Usage
`/deploy <change>` — e.g. `/deploy set agent.coding_context off`, `/deploy route Learning
Engine cron to local Ollama`.

## Preconditions (all required)
- **Explicit approval** for the change (config/SOUL/.env/cron/service are gated).
- A clear, single, surgical change (no full-file rewrites).
- Known expected outcome to validate against.

## What it does
1. Backup each touched file (timestamped `.bak`).
2. Apply the surgical edit.
3. Validate: YAML parses → restart service → functional check tied to the change → scan
   for new errors.
4. On failure: **roll back** to the `.bak`, restart, confirm prior behavior.

## Output
A filled `.claude/templates/deployment-report.md` (change · backups · validation · rollback
status). Update `MEMORY.md`; update `CONTEXT.md` if the change is notable.

## Scope note
Live deploys are **out of scope for the audit mission** — `/deploy` is the tool for the
later, approval-gated Phase-0 stabilization work in `docs/ROADMAP.md`.
