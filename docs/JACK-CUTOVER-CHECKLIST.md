# Jack Cutover Checklist — retire "Hamza", unify to a single Jack persona on the VPS

> **Status: NOT executed — staged & ready. This is a live deployment → approval-gated.**
> The local repo (code + design docs) already reflects the unified **Jack**. This checklist is the
> remaining **live-VPS** half. Nothing here runs without an explicit go. Legacy artifacts are never
> deleted (instant rollback).
>
> Companion history (factual, keep the "Hamza" name): `docs/HAMZA-{MIGRATION,DEPLOYMENT,CUTOVER}-*.md`.

## What changes (live box `hermes@167.233.108.213`)
| # | Target | From | To |
|---|--------|------|----|
| 1 | Worker bundle dir | `~/.hermes/hamza_worker/` | `~/.hermes/jack_worker/` |
| 2 | Router hook dir + module | `~/.hermes/hooks/hamza_router/` (`hamza_intent_router.py`) | `~/.hermes/hooks/jack_router/` (`jack_intent_router.py`) |
| 3 | Env (if set in `.env`/unit) | `HAMZA_WORKER_ROOT`, `HAMZA_ROUTER_MAX_CONCURRENT` | `JACK_WORKER_ROOT`, `JACK_ROUTER_MAX_CONCURRENT` |
| 4 | `~/.hermes/config.yaml` | Hamza persona `system_prompt`; `COMMAND:` path `…/hamza_worker/bin/leadgen.py`; group binding | single **Jack** persona; `…/jack_worker/bin/leadgen.py`; group `-1003797274797` |
| 5 | `~/.hermes/SOUL.md` | two-persona split; Hamza bound to stale group `-5439847434` | one **Jack** persona across DMs + Vytal group; group `-1003797274797` |
| 6 | Live chat trigger | `@Hamza …` | `@Jack …` (notify the Vytal group of the new name) |
| 7 | Google Sheet tab | `Hamza_Leads` | `Jack_Leads` (or leave + repoint `_SHEET_TAB`; pick one) |

> Note (#1/#2 ordering): the redeployed `jack_router/handler.py` defaults `WORKER_ROOT` to
> `~/.hermes/jack_worker` and imports `jack_intent_router`. Rename the worker dir (#1) **before**
> redeploying the hook (#2) so the new default resolves, or set `JACK_WORKER_ROOT` explicitly (#3).

## Pre-flight
```bash
ssh -i ~/.ssh/hermes_vps hermes@167.233.108.213
ts=$(date +%Y%m%d_%H%M%S)
cp ~/.hermes/config.yaml ~/.hermes/config.yaml.pre_jack_$ts
cp ~/.hermes/SOUL.md      ~/.hermes/SOUL.md.pre_jack_$ts
ls -la ~/.hermes/hamza_worker ~/.hermes/hooks/hamza_router   # confirm current state
```

## Execution
1. **Rename worker dir:** `mv ~/.hermes/hamza_worker ~/.hermes/jack_worker`
2. **Redeploy router hook** from the local repo's `hooks/jack_router/` to `~/.hermes/hooks/jack_router/`
   (scp/rsync), then `rm -rf ~/.hermes/hooks/hamza_router`. Confirm `jack_intent_router.py` is present.
3. **Env (only if previously set):** update `~/.hermes/.env` / unit `Environment=` to `JACK_WORKER_ROOT`,
   `JACK_ROUTER_MAX_CONCURRENT`. (If unset, code defaults already point at `jack_worker`.)
4. **`config.yaml`:** rename the persona to Jack in `agent.system_prompt`; repoint the `COMMAND:`
   directive to `…/jack_worker/bin/leadgen.py --spec -`; verify `telegram.allowed_chats` includes
   `-1003797274797`.
5. **`SOUL.md`:** collapse to one **Jack** persona covering both surfaces (DMs + Vytal group); fix the
   bound group ID `-5439847434` → `-1003797274797`.
6. **Sheet tab:** rename `Hamza_Leads` → `Jack_Leads` in the Vytal sheet (the redeployed hook writes
   to `Jack_Leads`). If you'd rather not rename live data, instead leave the tab and revert
   `_SHEET_TAB` in the deployed hook — do not leave them mismatched.
7. **Reload:** `systemctl --user restart hermes-gateway` (or `kill -USR1 $(cat ~/.hermes/gateway.pid)`).
   Confirm the journal logs `[jack_router] installed router on DiscordAdapter._handle_message`.

## Validation (in the Vytal group)
- [ ] `@Jack find 3 psychiatry clinics in Mumbai` → routes → worker → rows land in tab `Jack_Leads`,
      provenance present, completion summary posted by the live bot, **no 413, no agent loop**.
- [ ] `@Jack status` → queue counts.
- [ ] A plain conversational message → falls through to the agent (unchanged).
- [ ] `@Jack generate outreach for row 2` → drafts a pitch (PENDING REVIEW, **no send**).
- [ ] Old `@Hamza …` no longer triggers routing (expected — announce the rename to the group).

## Rollback (instant, total)
```bash
mv ~/.hermes/jack_worker ~/.hermes/hamza_worker
# redeploy the old hooks/hamza_router (or: git checkout the pre-cutover hook) ; rm -rf ~/.hermes/hooks/jack_router
cp ~/.hermes/config.yaml.pre_jack_$ts ~/.hermes/config.yaml
cp ~/.hermes/SOUL.md.pre_jack_$ts      ~/.hermes/SOUL.md
# rename the sheet tab back to Hamza_Leads if it was changed
systemctl --user restart hermes-gateway
```
The dormant legacy `~/.hermes/bin/hamza_orchestrator.py` is untouched throughout — it remains as-is.
