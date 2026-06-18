# Deployment Report — <short title>

> Skeleton for `/deploy` (`skills/deployment-validator.md` + `agents/infrastructure.md`).
> Live changes are approval-gated, backed up, validated, and rollback-ready.
> Fill every `<...>`; delete this quote block when filing.

- **Change:** <single surgical change, e.g. set `agent.coding_context off`>
- **Approver:** <who approved — required for config/SOUL/.env/cron/service edits>
- **Date:** <YYYY-MM-DD>

## Files touched + backups
| File | Change | Backup path |
|------|--------|-------------|
| `<path>` | <what changed> | `<path>.<timestamp>.bak` |

## Pre-change state
<the relevant value/behavior before the edit — what we expect to restore on rollback>

## Validation results
- [ ] YAML parses (`<command/result>`)
- [ ] Service restarted clean (`systemctl --user restart hermes-gateway.service`)
- [ ] Functional check tied to the change (`<expected outcome — e.g. real reply, request < 12k>`)
- [ ] No new errors in `~/.hermes/logs/errors.log`

## Rollback status
<NOT NEEDED (validated clean) | ROLLED BACK to `<.bak>` — service restarted, prior behavior confirmed>

## Docs updated
- [ ] `MEMORY.md`
- [ ] `CONTEXT.md` (if notable)
