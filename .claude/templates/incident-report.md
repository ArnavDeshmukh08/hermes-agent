# Incident Report — <short title>

> Skeleton for `/bug` outcomes (`skills/bug-fixer.md` + `agents/debugger.md`).
> Evidence-first: no fix without evidence. Fill every `<...>`; delete this quote block when filing.

- **Title:** <one-line summary>
- **Date:** <YYYY-MM-DD>
- **Status:** <OPEN | FIXED | MONITORING | WONT-FIX>

## Symptom
<what the user/operator observed>

## Evidence
<cite real sources>
- Log: `~/.hermes/logs/<agent|gateway|errors>.log` — <line / quote>
- Config/SOUL/.env: `<path>` — <key / value>
- Source: `<path:line>` — <snippet>

## Cause
- **Immediate cause:** <the proximate trigger>
- **Root cause:**
  - *Immediate:* <the direct defect>
  - *Architectural:* <the deeper design/structural reason it was possible>

## Fix applied
<the surgical change made (file + what changed), and via which path — /deploy backups, etc.>

## Validation steps
<how the fix was proven — every change must include validation>
- [ ] <e.g. YAML parses>
- [ ] <e.g. service restarted clean>
- [ ] <e.g. functional check tied to the symptom — real reply, request < 12k>
- [ ] <e.g. no new errors in errors.log>

## Long-term prevention
<guard/test/process change so this class of bug can't recur>

## Docs updated
- [ ] `MEMORY.md`
- [ ] `CONTEXT.md` (if notable)
