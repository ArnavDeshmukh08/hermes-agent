# Hamza Cutover Checklist — legacy → worker (Option A)

> **DO NOT EXECUTE until explicitly approved.** This is the prepared cutover plan referenced by the
> mission ("prepare final cutover plan… do NOT execute final cutover automatically"). It promotes
> `worker/` to the live Hamza engine and demotes `hamza_orchestrator.py` to dormant-legacy, with
> zero downtime and instant rollback. The only live change is **prompt/config text** — no service
> rebuild, no code deletion.

---

## 0. Pre-flight (must all be true before cutover)
- [ ] Worker shadow validated end-to-end (real run produced rows in the test sheet + a real
      notification + provenance on every row + no crashes). See `HAMZA-DEPLOYMENT-REPORT.md`.
- [ ] Comparison reviewed and worker judged the winner (see deployment report §Comparison).
- [ ] Arnav has explicitly approved cutover.

## 1. Backup procedure (run FIRST)
```bash
TS=$(date +%Y%m%d_%H%M%S)
# Config + persona (the only things that change)
cp ~/.hermes/config.yaml  ~/.hermes/config.yaml.pre_worker_cutover
cp ~/.hermes/SOUL.md      ~/.hermes/SOUL.md.pre_worker_cutover
cp ~/.hermes/config.yaml  ~/.hermes/config.yaml.bak.$TS
cp ~/.hermes/SOUL.md      ~/.hermes/SOUL.md.bak.$TS
# Confirm the legacy engine + sibling scripts remain in place (NOT deleted)
ls -la ~/.hermes/bin/hamza_orchestrator.py ~/.hermes/bin/sheets_agent.py
# Confirm the worker bundle is present and smoke-passes (mock)
cd ~/.hermes/hamza_worker && HERMES_LLM_MOCK=1 HERMES_TASKS_ROOT=/tmp/co_t HERMES_OUT_DIR=/tmp/co_o \
  /home/hermes/.hermes/hermes-agent/venv/bin/python bin/leadgen.py \
  --spec - --mode parallel --no-notify <<< '{"target":"t","location":"x","columns":["Clinic Name"],"count":1}'
```

## 2. Exact files changed (cutover = 2 text edits)
| File | Change | From → To |
|---|---|---|
| `~/.hermes/config.yaml` (Hamza `system_prompt`, lines ~55-62) | repoint the `COMMAND:` directive | `…/bin/hamza_orchestrator.py "<cmd>" [sheet_id]` → `…/hamza_worker/bin/leadgen.py --spec -` and tell the agent to emit a JSON spec `{target,location,columns,count,outreach}` on stdin |
| `~/.hermes/SOUL.md` (VAGS workflow, lines ~44-54) | replace the 5-step stealth→validator→sheets→writer recipe | single step: "build a JSON spec and pipe it to `leadgen.py --spec -`; it discovers, enriches, validates, writes the sheet, and notifies. Then stop for approval before any send." |

**New canonical command the agent will run:**
```bash
echo '{"target":"physiotherapy clinics","location":"India",
       "columns":["Clinic Name","City","Phone","Website","Instagram","Personalized Pitch"],
       "count":12,"outreach":true,"sheet_tab":"VAGS_Leads"}' \
| /home/hermes/.hermes/hermes-agent/venv/bin/python \
  /home/hermes/.hermes/hamza_worker/bin/leadgen.py --spec -
```
(Production env adds `HERMES_SHEET_ID=<master>`, `HERMES_GOOGLE_CREDS=~/.hermes/credentials.json`,
and `DISCORD_WEBHOOK_URL` to `.env`; or keeps writing to a dedicated leads sheet.)

## 3. Exact services changed
| Service | Action | Why |
|---|---|---|
| `hermes-gateway.service` | **soft reload only**: `kill -USR1 $(cat ~/.hermes/gateway.pid)` | so the agent re-reads the edited prompt. No rebuild, no new unit, no new process. |
| (none new) | — | The worker runs as an **on-demand subprocess** the agent spawns, exactly like the legacy script. No new systemd unit, no daemon, no cron. |

## 4. Verification after cutover
- [ ] In the Vytal Telegram group, issue a small live request ("find 3 physiotherapy clinics in
      Pune"). Confirm the agent runs `leadgen.py` (not the legacy script), rows land in the sheet,
      a notification arrives, every row has `source_url`+`fetched_at`, and nothing is emailed.
- [ ] `tail` the worker stderr / gateway journal for tracebacks. Expect none.
- [ ] Confirm no write occurred to any unintended (prod-master) tab if a dedicated leads tab is used.

## 5. Rollback procedure (instant, zero downtime)
```bash
cp ~/.hermes/config.yaml.pre_worker_cutover ~/.hermes/config.yaml
cp ~/.hermes/SOUL.md.pre_worker_cutover     ~/.hermes/SOUL.md
kill -USR1 $(cat ~/.hermes/gateway.pid)     # or systemctl --user restart hermes-gateway.service
```
The legacy `hamza_orchestrator.py` was never removed, so the old pipeline is live again the moment
the prompt reverts. Rollback trigger = any of: zero rows on a known-good query, repeated tracebacks,
an unintended write, or operator judgment.

## 6. Post-cutover cleanup (optional, later — NOT part of cutover)
- [ ] After a stable soak (e.g. 1–2 weeks), optionally archive `hamza_orchestrator.py` to
      `~/.hermes/bin/_legacy/`. Keep the sibling agents (`outbound_dispatcher_agent.py` etc.) — the
      worker is draft-only and the send step still relies on the dispatcher.
- [ ] Re-attach camoufox (`stealth_scrape.py`) as the worker's discovery escalation if directory
      yield needs it (LEAD-ENGINE §2 — designed, not yet wired).

---

### Cutover is intentionally tiny
The entire promotion is **two text edits + one soft reload**, fully reversible by **two `cp` + one
reload**. That is the dividend of the audit finding: nothing depends on the legacy script except
words in a prompt.
