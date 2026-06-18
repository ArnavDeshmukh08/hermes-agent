# /review — run the Review Board over a change or file

**Purpose:** Put a proposed change or file through three independent reviewers and get a
clear pass/fail from each before it ships. Wraps the Review Board agents.

## Usage
`/review <target>` — e.g. `/review skills/context-compressor.md`, or `/review` to review
the current pending change / latest `/deploy` plan.

## What it does
Runs three reviewers in parallel over the target:
1. `agents/technical-reviewer.md` — correctness, root-cause vs symptom, validation steps,
   matches the "every change includes validation" rule.
2. `agents/security-reviewer.md` — secrets hygiene (no keys in prompts/logs), approval
   gates honored (sends/spend/deploys), `.env` exposure.
3. `agents/integration-reviewer.md` — fit with the live system: Groq 12k budget impact,
   provider routing, cron/Telegram/memory side-effects.

## Output
A pass/fail verdict per reviewer with itemized issues (severity-tagged). Any reviewer
failing on a CRITICAL blocks the change.

## Boundaries / Note
Read-only — reviews and reports, never edits. Apply approved changes via `/deploy`
(backup + validate + approval). Use after `/bug` proposes a fix and before `/deploy`.
