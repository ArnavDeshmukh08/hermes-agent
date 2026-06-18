# Workflow: Incident Response

**Purpose:** Take a Hermes symptom from report to validated fix and recorded docs, evidence-first.
Implements `.claude/CLAUDE.md` (reproduce→logs→subsystem→hypothesis→test→fix→validate→update docs).

```
symptom ─▶ /doctor ─▶ /bug ──▶ propose fix ──▶ [APPROVAL] ──▶ /deploy ──▶ update docs
        (triage)   (diagnose)   (no live edit)              (apply+validate)
```

## Steps
1. **Symptom in.** Capture the report verbatim (Telegram glitch, no reply, 413, crash). Paste any
   error / screenshot / log excerpt.
2. **Triage — `/doctor`** (`skills/hermes-doctor.md`). Full health check: service active, recent
   errors, token budget, provider reachability. Decide if this is a known instability (413/budget)
   or a new fault.
3. **Diagnose — `/bug <symptom>`** (`skills/bug-fixer.md` + `agents/debugger.md`). Gather evidence
   from `~/.hermes/logs/agent.log` → identify subsystem → trace → hypotheses → confirm root cause.
   Routes to the matching subsystem skill as evidence dictates:
   - context / 413 / TPM → `skills/context-compressor.md` (or `/context`)
   - provider / 404 / model / base_url → `skills/provider-debugger.md`
   - Telegram delivery / webhook → `skills/telegram-debugger.md`
   - log forensics → `skills/log-analyzer.md`
   - **Hard gate: no fix without evidence.**
4. **Propose fix.** `/bug` outputs a fix proposal only — it does NOT touch live files. Fill
   `.claude/templates/incident-report.md` (root cause · evidence · fix · validation · prevention).
5. **Approval gate.** Any live edit (config/SOUL/.env/cron/service) requires explicit approval.
6. **Apply — `/deploy <change>`** (`skills/deployment-validator.md` + `agents/infrastructure.md`):
   backup → surgical edit → validate (YAML parse → restart → functional check → no new errors) →
   roll back on failure.
7. **Record.** Update `MEMORY.md`; update `CONTEXT.md` if notable (`workflows/doc-sync.md`).

## Gates
- Diagnosis/read-only: free. · Live edit: approval + `/deploy`. · Block ship if root cause unproven.

## Output
A filled `incident-report.md`, a validated (or rolled-back) system, and synced docs.
