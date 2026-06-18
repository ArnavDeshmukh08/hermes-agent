# Workflow: Deploy Pipeline

**Purpose:** Apply an approved live change to the VPS Hermes safely — never blind, always
reversible. Maps to `skills/deployment-validator.md` + `agents/infrastructure.md`, driven by
`/deploy`. Exists because of past full-rewrite `config.yaml` corruption.

```
[APPROVAL] ─▶ backup ─▶ surgical edit ─▶ validate ─▶ record
                                            │
                                       fail │─▶ rollback ─▶ confirm prior state
```

## Steps
1. **Approval gate.** Confirm explicit approval for the change. Config / SOUL / .env / cron /
   service edits are ALL gated (`standards/engineering-standards.md` §6). No approval → stop.
2. **Preconditions.** One clear surgical change (no full-file rewrites) + a known expected outcome
   to validate against. Capture current state:
   `systemctl --user is-active hermes-gateway.service`.
3. **Backup.** Timestamped copy of every touched file:
   `cp ~/.hermes/config.yaml ~/.hermes/config.yaml.bak.$(date +%Y%m%d_%H%M%S)`.
4. **Surgical edit.** Change single keys only, one change at a time so cause→effect is attributable.
5. **Validate** (all four, in order):
   - **YAML parses:** `python -c "import yaml;yaml.safe_load(open('config.yaml'))"`
   - **Restart:** `systemctl --user restart hermes-gateway.service` → `is-active`
   - **Functional check** tied to the change (e.g. context fix → send `het`, log shows
     `Requested < 12000` + real reply; provider fix → intended `base_url`/`model`, no 404;
     cron route → job runs on intended provider, `last_status: ok`)
   - **No regressions:** scan `~/.hermes/logs/agent.log` for new errors post-restart
6. **Rollback on failure.** Restore the `.bak`, restart, confirm prior behavior. Never leave the
   service worse than before.
7. **Record.** Fill `.claude/templates/deployment-report.md` (change · backup paths · validation
   results · rollback status). Update `MEMORY.md`; `CONTEXT.md` if notable.

## Gates
- Approval required before backup. · Rollback is mandatory on any failed validation step.

## Output
A filled `deployment-report.md` and a Hermes that is either validated-healthy or rolled-back-clean.
