# Workflow: Doc Sync

**Purpose:** Keep `CONTEXT.md` and `MEMORY.md` accurate and consistent after every notable change,
per the project `CLAUDE.md` "MANDATORY: keep CONTEXT.md updated" rule. Owned by the `documentation`
agent (works alongside `agents/memory-manager.md` for memory-store hygiene).

```
change lands ─▶ notable? ──no──▶ skip (still log session in MEMORY.md)
                   │ yes
                   ▼
        update MEMORY.md (log) ─▶ update CONTEXT.md (briefing) ─▶ consistency check
```

## The notable-vs-trivial rule (from project `CLAUDE.md`)
- **Notable → DO update CONTEXT.md:** new feature/capability, architecture or model/provider
  change, new integration, infra change, a new persona/skill/script, a locked decision, status
  changes (built/paused/removed).
- **Not notable → skip CONTEXT.md:** bug fixes, typos, log/format tweaks, exploratory checks,
  trivial config.

## Steps
1. **Classify** the change as notable or trivial using the rule above.
2. **MEMORY.md (always).** Append a chronological work-log entry for the session — what was done,
   findings, next step. This is the running log; update it every session, notable or not.
3. **CONTEXT.md (if notable).** Update the curated big-picture briefing: keep §10 (Current status)
   accurate and bump the "Last meaningful update" date. CONTEXT.md must read as always-current and
   be shareable standalone with any human/AI.
4. **Consistency check.** MEMORY.md (chronological) and CONTEXT.md (curated) must not contradict
   each other or `.claude/CLAUDE.md`. Reconcile token numbers, model names, version, and status.
5. **Size guard.** Keep context files lean (`.claude/CLAUDE.md`: under 5k lines; archive old
   conversations/memories). Hand bloated memory stores to `agents/memory-manager.md`.

## Gates
- No approval needed (docs are auto-allowed). · A notable change is NOT complete until CONTEXT.md
  is updated in the same session.

## Output
Updated `MEMORY.md` (every session) and `CONTEXT.md` (when notable), mutually consistent, with the
"Last meaningful update" date current.
