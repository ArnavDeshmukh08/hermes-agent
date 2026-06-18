# /bug — diagnose and fix a Hermes issue

**Purpose:** Run the evidence-first bug-fixing workflow end to end and produce an incident
report. Wraps the `bug-fixer` skill + `debugger` agent.

## Usage
`/bug <symptom>` — e.g. `/bug Hamza stopped replying in the Vytal group`, or paste an
error / screenshot / log excerpt.

## What it does
1. Invokes `skills/bug-fixer.md` (gather evidence → identify subsystem → trace → hypotheses
   → validate → root cause → fix → prevention).
2. Routes to the subsystem skill (`context-compressor`, `provider-debugger`,
   `telegram-debugger`, `log-analyzer`) as the evidence dictates.
3. Enforces the hard rule: **no fix without evidence.**

## Boundaries
- Diagnosis + read-only evidence gathering: do freely.
- Any **live edit** (config/SOUL/.env/cron): hand to `/deploy` (backup + validate +
  approval). `/bug` proposes the fix; `/deploy` applies it.

## Output
A filled `.claude/templates/incident-report.md`: root cause · evidence · fix · validation
steps · long-term prevention.
