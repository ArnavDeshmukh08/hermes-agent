# Hamza Migration Report — `hamza_orchestrator.py` → `worker/`

> Mission: Option A. The `worker/` architecture is the canonical Hamza engine;
> `hamza_orchestrator.py` is legacy. This report audits the live VPS and maps every
> dependency to its replacement, with risk + rollback. **No cutover is executed.**
> Date: 2026-06-17. VPS: `hermes@167.233.108.213` (`personal-os`, Ubuntu, Python 3.11.15).

---

## 1. Audit — what actually exists on the box

| Item | Finding |
|---|---|
| **Legacy script** | `/home/hermes/.hermes/bin/hamza_orchestrator.py` (11,480 bytes, last modified Jun 13). |
| **Cron jobs** | **None.** No user crontab; nothing in `/etc/cron*` references hamza/hermes. |
| **Systemd services** | **One:** `hermes-gateway.service` (user unit) — the Telegram messaging gateway (`hermes_cli.main gateway run`). It is **not** hamza-specific and does **not** import or exec the orchestrator. No system-level hermes/hamza units. |
| **Timers** | None relevant (only `launchpadlib-cache-clean`). |
| **Programmatic callers** | **None.** No Python `import`, no subprocess reference to `hamza_orchestrator` anywhere except the script itself and config/prompt text (below) and stale `config.yaml.bak.*` backups + one old session dump. |
| **The real "caller"** | The **Hamza agent persona's system prompt.** `config.yaml:55-62` instructs the LLM: *"COMMAND: …/venv/bin/python3 …/bin/hamza_orchestrator.py \"<user command>\" [sheet_id]"*. `SOUL.md:44-54` documents the "VAGS Lead Generation Workflow" pointing at `stealth_scrape.py` / `validator_agent.py` / `sheets_agent.py` / `contextual_writer_agent.py` / `outbound_dispatcher_agent.py`. |
| **Invocation model** | Ad-hoc: when Arnav/Spandan say "scrape N leads in <city>" in the Vytal Telegram group, the agent shells out to the script via its terminal tool. There is **no automated trigger.** |
| **Secrets on box** | `GROQ_API_KEY` ✓, `VYTAL_SHEET_ID` ✓ (production master sheet). No `JINA_API_KEY` (Jina works keyless, rate-limited). **No Discord** of any kind. No `GMAIL/SMTP` (so legacy's email-send step is not even wired). |
| **Deps** | `gspread` 6.2.1 ✓, service-account creds at `~/.hermes/credentials.json` (`vytal-732@vytal-499305.iam`). `ddgs`/`duckduckgo_search` **not installed** in the venv (legacy imports them under try/except and silently skips DDG when absent). |

**Headline finding:** `hamza_orchestrator.py` has **zero machine dependencies** — no cron, no
service, no import. Its only "caller" is **natural-language instructions in `config.yaml` and
`SOUL.md`**. Migration is therefore a *text edit*, and rollback is *reverting that text*. This is the
lowest-risk possible migration surface.

---

## 2. Dependency → replacement map

| # | Caller (depends on legacy) | Current path | Replacement path | Migration risk | Rollback |
|---|---|---|---|---|---|
| 1 | **Hamza system prompt** (`config.yaml:55-62`) — the `COMMAND:` directive | `…/venv/bin/python3 …/bin/hamza_orchestrator.py "<cmd>" [sheet_id]` | `…/venv/bin/python …/hamza_worker/bin/leadgen.py --spec -` (agent emits JSON spec `{target,location,columns,count,outreach}`) | **LOW** — text-only change to one config block; agent already shells out to scripts | Restore the `COMMAND:` line from `config.yaml.bak.<ts>` (auto-backed-up on every config write) or the pre-cutover backup |
| 2 | **SOUL.md VAGS workflow** (`44-54`) — multi-step stealth_scrape→validator→sheets→writer recipe | 5 separate bin scripts driven by prose steps | Single `leadgen.py` call (worker does discover→research→social→outreach→validate→sheet internally) | **LOW** — replace the 5-step recipe with one command; behavior is a superset | Restore `SOUL.md.bak.<ts>` |
| 3 | **`sheets_agent.py`** (Sheets I/O) | called by legacy via subprocess | worker writes Sheets directly via gspread (`worker/sheets.py`, `value_input_option=RAW`) | **LOW** — same creds, same API, additive (RAW + provenance cols) | Legacy script still present and functional; revert prompt |
| 4 | **`stealth_scrape.py` / `validator_agent.py` / `contextual_writer_agent.py`** | called by legacy/SOUL recipe | superseded by worker's internal Discovery/Validation/Outreach workers (Jina + Groq) | **MED** — worker uses Jina (not camoufox) for discovery; heavily bot-walled directories (Practo/Justdial) may yield less than camoufox. Mitigation: worker targets clinic-own-sites via DDG (Jina-readable); camoufox can be re-attached later as an escalation | Scripts remain on box, unchanged; revert prompt to legacy recipe |
| 5 | **`outbound_dispatcher_agent.py`** (Gmail send) | legacy "Send Approved Emails" step | **unchanged** — worker is draft-only (`PENDING REVIEW`), never sends; the send step stays exactly as-is behind manual approval | **NONE** — not touched | n/a |
| 6 | **`VYTAL_SHEET_ID`** (prod master sheet) | legacy default write target | worker writes to a **separate** spec-provided `sheet_tab`/sheet; never auto-writes prod master | **LOW** — isolation enforced by spec | n/a |

No other dependency exists. Items 4/5 are **not deleted** — the legacy scripts remain on disk so a
prompt-revert restores the exact old behavior instantly.

---

## 3. Migration risk summary

- **Overall risk: LOW.** The migration changes *prompt/config text*, not running infrastructure.
  Nothing imports the legacy script; no service restart is required; the gateway is untouched.
- **Only real functional risk (MED):** discovery yield. Legacy used `stealth_scrape.py` (camoufox)
  to bypass anti-bot walls on Practo/Lybrate/Justdial. The worker uses Jina (keyless) + DDG result
  links. For Jina-readable clinic-own-sites this is fine; for hard bot-walled directories it may
  return fewer rows until camoufox is re-attached as the worker's escalation path (already designed
  for in LEAD-ENGINE §2, not yet wired). This is a **quality** risk, not a stability risk.
- **Data-integrity is strictly better:** worker adds mandatory provenance (`source_url`,`fetched_at`)
  on every row, a mandatory validation gate, dedupe, CSV-injection guards, and backoff/throttle for
  the Groq TPM cap — none of which the legacy had.

---

## 4. Rollback method (global)

Because the only "wiring" is prompt text, rollback is a single config revert with **zero downtime**:

```bash
# 1. Restore the pre-cutover config + SOUL backups (taken in the deployment step)
cp ~/.hermes/config.yaml.pre_worker_cutover ~/.hermes/config.yaml
cp ~/.hermes/SOUL.md.pre_worker_cutover     ~/.hermes/SOUL.md
# 2. Reload the gateway so it re-reads the prompt (USR1 = soft reload)
kill -USR1 $(cat ~/.hermes/gateway.pid)     # or: systemctl --user restart hermes-gateway.service
```

The legacy `hamza_orchestrator.py` and all sibling scripts are never deleted during cutover, so a
revert is instantaneous and total. The worker bundle under `~/.hermes/hamza_worker/` can be left in
place (dormant) or removed — it has no effect unless the prompt points at it.
