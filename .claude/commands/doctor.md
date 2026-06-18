# /doctor — full Hermes health check

**Purpose:** Fast, read-only triage of the whole system. Wraps the `hermes-doctor` skill.

## Usage
`/doctor` — runs the full checklist and prints the status board.

## What it does
Runs `skills/hermes-doctor.md`: service · resources (RAM/disk) · primary-brain routing ·
budget health (recent 413s, last turn size vs 12k) · fallback tunnel · cron status ·
memory size · secrets hygiene.

## Output
The `hermes-doctor` status board with a verdict (HEALTHY / DEGRADED / DOWN) and, for any
non-OK line, the owning skill/agent to escalate to via `/bug`.

## Note
Read-only. `/doctor` never changes anything — it tells you *what* and *where*, then you
run `/bug` to fix and `/deploy` to apply.
