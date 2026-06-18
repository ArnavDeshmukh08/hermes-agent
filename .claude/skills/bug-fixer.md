# Skill: Bug Fixer

**Responsibility:** The end-to-end, evidence-first procedure for diagnosing any Hermes
issue and producing a validated fix + prevention. This is the engine behind the `/bug`
command and the `debugger` agent. Subsystem depth lives in the sibling skills
(`log-analyzer`, `telegram-debugger`, `provider-debugger`, `context-compressor`).

## Hard rule
**Never output a fix without evidence.** Every step cites a log line, config value, or
source reference. If evidence is missing, gather it — do not guess.

## Inputs
Error message · screenshot · last action/command · approximate time · which surface
(DM / Vytal group / cron).

## Workflow
1. **Gather evidence** (read-only):
   ```
   systemctl --user status hermes-gateway.service
   tail -n 200 ~/.hermes/logs/agent.log      # main log
   tail -n 100 ~/.hermes/logs/errors.log ~/.hermes/logs/gateway.log
   ```
   For cron: `~/.hermes/cron/jobs.json` (last_status/last_error) +
   `~/.hermes/cron/output/<id>/`.
2. **Identify subsystem** → route to the right skill:
   | Signal | Subsystem | Skill |
   |---|---|---|
   | `413 ... Requested >12000`, `cannot compress further` | context overflow | `context-compressor` |
   | `404 generateContent / v1main`, wrong provider/base_url | LLM provider | `provider-debugger` |
   | no reply, polling errors, wrong chat/persona | Telegram | `telegram-debugger` |
   | session reset loop, db growth | memory | `memory-manager` (agent) |
   | job error/paused, schedule wrong | cron | `provider-debugger` + jobs.json |
   | tool exception / leaked tool JSON | tool failure | `log-analyzer` |
   | OOM, service dead, tunnel down | VPS | `infrastructure` (agent) |
3. **Trace the request path** — gateway → session → agent_init (context assembly) →
   provider → `error_classifier` → recovery (compress/failover).
4. **Hypotheses** — rank by evidence; state what would confirm/refute each.
5. **Validate** each with a read-only check before believing it.
6. **Root cause** — the deepest cause; distinguish immediate vs. architectural.
7. **Fix** — minimal, reversible; back up before any live edit; surgical YAML only.
8. **Prevention** — the guard/config/test that stops recurrence.

## Known-incident library (see `docs/AUDIT.md` §3 for full detail)
- **413 on any turn** → fixed overhead ~17k > 12k TPM; cause is the **skills-hub prompt
  (~10.6k)**, not toolsets, not AGENTS.md. Fix = shrink skills / on-demand via
  `tool_search` / route heavy work off Groq.
- **404 generateContent** → an `.env` key hijacked provider routing; ensure explicit
  `model.provider` + `base_url`.
- **413 → compress → reset loop** → overhead is fixed system context, uncompressible;
  compression is the wrong recovery.

## Output
A filled `.claude/templates/incident-report.md`.
