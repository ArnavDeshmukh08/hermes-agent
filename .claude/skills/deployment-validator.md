# Skill: Deployment Validator

**Responsibility:** Make any live change to Hermes safe — backup, apply surgically,
validate, and provide a rollback. The gate every config/SOUL/.env/cron edit passes
through. Powers the `/deploy` command.

## Why this exists
History of full-rewrite `config.yaml` corruption (a `.corrupt` backup + 18 `.bak`s on
the box). Live edits must be reversible and validated — never blind.

## Pre-change
1. **Approval** — config/SOUL/.env/deploy changes are approval-gated. Confirm intent.
2. **Backup** — timestamped copy of each file being touched:
   `cp ~/.hermes/config.yaml ~/.hermes/config.yaml.bak.$(date +%Y%m%d_%H%M%S)`.
3. **State capture** — `systemctl --user is-active hermes-gateway.service`; note current
   behavior to compare against.

## Apply
- **Surgical edits only** (single keys), never full-file rewrites.
- One change at a time when feasible, so validation attributes cause→effect.

## Validate
1. **YAML parses:** `python -c "import yaml,sys;yaml.safe_load(open('config.yaml'))"`.
2. **Restart:** `systemctl --user restart hermes-gateway.service` → `is-active`.
3. **Functional check** tied to the change, e.g.:
   - context fix → send `het`; log shows `Requested < 12000` + real reply.
   - provider fix → a turn shows the intended `base_url`/`model`, no 404.
   - cron route → job runs on intended provider, `last_status: ok`.
4. **No regressions:** scan `agent.log` for new errors post-restart.

## Rollback
If validation fails: restore the `.bak`, restart, confirm prior behavior, record what
happened. Never leave the service in a worse state than before.

## Output
A filled `.claude/templates/deployment-report.md` (change, backup paths, validation
results, rollback status).
