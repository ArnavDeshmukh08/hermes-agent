# Hermes Agent — Full Project Context

> **Purpose of this file:** a complete, standalone briefing on what this project is, why it
> exists, how it's built, and where it stands. Hand it to any human or AI and they should be able
> to understand the whole picture without prior context.
>
> **Maintenance rule:** update this file whenever a *notable* change happens (new capability,
> architecture change, model/provider change, new integration). Skip trivial changes (bug fixes,
> typos, log tweaks). See `CLAUDE.md`.
>
> Companion files: `CLAUDE.md` (goals + rules of engagement), `MEMORY.md` (chronological work
> log), `skills/` (operational runbooks), `secrets/` (credentials, gitignored).
> Last meaningful update: 2026-06-20. (**New integrations built locally: Garmin sleep + Google
> Calendar + Claude session-bridge — 21 new tests, 191 total green, lint-clean. Garmin/Calendar
> pull into the morning briefing; Calendar adds a Discord `calendar` intent (add/list); the bridge
> summarizes a pasted Claude session into USER.md + Discord. NOT yet deployed — gated on: enable
> Calendar API on `vytal-499305`, share Arnav's calendar with `vytal-732@vytal-499305.iam.gserviceaccount.com`,
> add `GARMIN_EMAIL`/`GARMIN_PASSWORD` to `.env`. See §10.**) Prior 2026-06-18: (**Hamza → Jack unification — DONE, incl. live VPS.** The
> two-persona model (Jack in DMs / Hamza in the Vytal group) is retired → **one unified `Jack`
> persona on both surfaces**. Local repo + git baseline done (13 intent tests pass). **Live-VPS
> cutover EXECUTED** on `personal-os`: renamed `~/.hermes/{hamza_worker→jack_worker,
> hooks/hamza_router→jack_router}`, `config.yaml`+`SOUL.md` now one Jack persona (Mode A DMs / Mode B
> Vytal group), Google Sheet tab `hamza_leads`→`Jack_Leads`, gateway restarted clean. Backups in
> `~/.hermes/jack_cutover_bak_20260618_090941/`. ⚠️ One manual step left: rename the Discord **bot
> display name** `@Hamza`→`@Jack` in the Discord portal (not SSH-able). Legacy `hamza_orchestrator.py`
> + historical reports keep the "Hamza" name on purpose.
> **413 chat FIXED — Jack conversation brain (`hooks/jack_router/conversation.py`).** The
> framework agent loop is now **fully bypassed for Discord**: `jack_router.classify()` returns
> `conversational` for anything non-operational (never `None`), and a lean handler answers directly —
> personality from `SOUL.md` (operational tool blocks stripped), profile from `USER.md`, 6-turn
> sliding window, ~10k token budget, calls `lib/llm.py` (`prefer=groq`, `max_tokens=400`) off-thread.
> **Live-verified on the box: a real turn is ~632 tokens (vs 29.6k) and Groq replies correctly with
> Arnav's name/startups — no 413.** Provider/history switchable via **`JACK_CHAT_PROVIDER`** in `.env`
> (`groq` free | `paid_groq` 20-turn no-cap | `ollama` local) — the paid-tier upgrade is one env value.
> 87 tests pass. `reminder` intent added as a stub; image-gen + social + a durable Discord reminder
> scheduler are the next router intents. *(Earlier same-day stopgap — `max_tokens`→1024 + memory-only
> tool gating — is now superseded but left in place as a harmless safety net.)*
> Prior entries below.)
>
> Earlier 2026-06-18: (**Intent Router** live: a user-space hook
> `~/.hermes/hooks/hamza_router/` wraps the Discord adapter's `_handle_message` (zero framework-file
> edits) so operational messages — `find/scrape leads`, `status`, `outreach for row N` — bypass the
> 29.6k-token agent loop and run the `worker/` in-process, replying via the live bot. Conversational
> messages fall through to the agent unchanged. No LLM in routing (regex). 13 tests, Ruff clean,
> ECC-reviewed. Proven live: "find 5 physiotherapy clinics in Mumbai" → 5 real leads, no 413; "Hey" →
> agent (413, expected). Deliverable: `docs/intent-routing-discovery.md`. Prior entries below.)
>
> Earlier 2026-06-18: Adopted **Option A**: `worker/` is the canonical Hamza
> engine; `hamza_orchestrator.py` is legacy. Worker **shadow-deployed to the VPS** at
> `~/.hermes/hamza_worker/` and **validated on REAL data** — physiotherapy/India → 5 real leads in a
> live Google Sheet tab `worker_shadow_test` (prod tabs untouched), real Discord notification (HTTP
> 204), 5/5 provenance, 0 crashes. Beat legacy on runtime (37s vs slower+nulls), data quality, and
> maintainability. Deliverables: `docs/HAMZA-{MIGRATION-REPORT,DEPLOYMENT-REPORT,CUTOVER-CHECKLIST}.md`.
> **Cutover NOT executed — staged & ready.** Audit finding: legacy has no cron/service/import; its
> only caller is prompt text in `config.yaml`+`SOUL.md`, so cutover = 2 text edits + soft reload,
> instant rollback. Prior entry below.)
>
> Prior (2026-06-17): BUILT the event-driven lead worker system — `worker/`
> package + `bin/leadgen.py`: Hermes Prime creates a task → spawns a concurrent worker pool
> (Discovery/Research/Social/Outreach/Validation) over a filesystem task queue
> (tasks/{pending,running,completed,failed}) → aggregates → emits `task_complete` → Discord
> summary. Dynamic specs `{target,location,columns}` (no code per task). 61 tests pass, Ruff clean,
> ECC-reviewed (4 reviewers). Acceptance: physiotherapy/India → 12 leads, 12/12 provenance, pitches
> PENDING REVIEW, **4.37× parallel speedup**. Built LOCAL + offline-validated; VPS deploy is next.)

---

## 1. What we're building (the one-liner)
**Hermes** is a 24/7 personal AI assistant for **Arnav** — a startup founder + AIML college
student — running on a VPS and reachable from his phone via **Telegram** (text + voice). It's
meant to be his **second brain, chief of staff, and dispatcher to a startup dev team**: capture
ideas, run reminders, learn his preferences, and eventually run autonomous money-making work
(lead-gen/outreach) for his startup while he sleeps.

## 2. Who it's for
**Arnav Deshmukh.** Building **Vytal** — a clinic patient-retention / workflow OS for solo
psychiatrists & dermatologists (WhatsApp-first, AI-powered, aimed at eliminating no-shows). Also
runs an AI-voice-agents side business, an AIML trading bot, and college. Wants a Jarvis-like
assistant. Tone preference: authentic technical-founder voice, minimal emojis; banned words
include "thrilled", "delve", "game-changer". Hard data rule: business-lead data must be 100%
real — zero fake/placeholder contacts.

## 3. Core capabilities (priority order)
1. **Second brain + reminders (FOUNDATION — built first):** capture ideas/tasks/notes via text +
   voice from anywhere; remind & nudge; learn preferences over time (e.g. "dislikes thin-crust
   pizza"), asking follow-up questions early to learn faster.
2. **Voice layer (Jarvis):** natural speech in/out. Planned: Whisper (STT) + Piper (TTS), free,
   local. NOT built yet.
3. **Dev-team dispatcher:** turn idea-dumps into specs, dispatch to Claude Code sub-agents, return
   a reviewable summary/diff. (The paid piece — "pay when it earns".) NOT built yet.
4. **Outreach engine (semi-auto):** overnight lead-finding + message drafting for Vytal; sends are
   approval-gated; compliance-aware (India DPDP, anti-spam). Partially scaffolded (see §7).
5. **Later/maybe:** food ordering via headless browser (fragile, low priority).

## 4. Key decisions (locked)
- **Framework:** the open-source **`hermes-agent`** framework (a full "agent OS" with memory,
  skills, cron, kanban, multi-platform messaging). Model-agnostic.
- **Brain:** **Groq free tier** (`llama-3.3-70b-versatile`) as primary. A **local model on
  Arnav's Mac** serves as fallback + future heavy-job engine (see §6).
- **Budget:** "Pay only when it earns." Everything free now; revisit paid pieces once outreach
  books real meetings.
- **Autonomy:** **mixed by risk.** Auto (no approval): reminders, capture, research, drafting.
  Approval-gated (one-tap Telegram): sends, orders, spending, code merges.
- **Interface:** Telegram bot (text + voice notes), 24/7.

## 5. Infrastructure (the actual deployment)
- **VPS:** `167.233.108.213` (hostname `personal-os`), user `hermes`. **Ubuntu 26.04, 2 vCPU,
  3.7 GB RAM, no GPU, no swap, 63 GB free.** Access is **SSH key-only** (`~/.ssh/hermes_vps`),
  password auth disabled, fail2ban active.
- **The assistant** = `hermes-agent` v0.16.0 (config schema v29) installed at
  `~/.hermes/hermes-agent`, all state/config under `~/.hermes/`.
- **Process:** runs as a **systemd --user** service `hermes-gateway.service` (auto-restart,
  lingering enabled → survives logout). Control: `systemctl --user {status|restart} hermes-gateway`.
- **Key paths on the box:**
  - `~/.hermes/config.yaml` — main config (providers, telegram, agent, auxiliary, cron…)
  - `~/.hermes/.env` — API keys (loaded via python-dotenv)
  - `~/.hermes/SOUL.md` — persona/behavior directives (the **Jack** identity; formerly a Jack/Hamza split, now unified — see §7)
  - `~/.hermes/USER.md` — Arnav's profile, tone rules, data guardrails
  - `~/.hermes/state.db` (memory/sessions), `kanban.db` (tasks), `memories/`, `sessions/`
  - `~/.hermes/skills/` — installed skill packs; `~/.hermes/bin/` — Arnav's custom scripts
  - `~/.hermes/scripts/` — helper scripts (e.g. `remind.py`); `~/.hermes/logs/` — gateway/errors logs
- **Arnav's Mac (M1 Max, 32 GB):** runs **Ollama** (Metal) hosting a local model, exposed to the
  VPS over an **SSH reverse tunnel** (no Tailscale). Two **launchd** agents keep it alive:
  `com.hermes.ollama-serve` and `com.hermes.ollama-tunnel` (auto-start + auto-reconnect).

## 6. The brain architecture (how model routing works — IMPORTANT)
This was hard-won; here's the final design and the reasoning.

- **Primary brain = Groq `llama-3.3-70b-versatile`** (free tier, OpenAI-compatible endpoint
  `https://api.groq.com/openai/v1`). Handles **interactive chat** — fast (~2s), reliable
  tool-calling. Short chat requests fit Groq's **12,000 tokens/minute** free limit.
- **Fallback brain = local `llama3.1:8b`** on Arnav's Mac (128K context, via the SSH tunnel at
  `http://localhost:11434/v1`). Triggers when Groq has a connection/overload error. Provides
  resilience when Groq is down.
- **Why this split:** Groq free is capped at 12k tokens/min. A *full agent turn* (system prompt +
  ~21 tool schemas + the long dual-identity SOUL.md) is ~21–26k tokens — too big for Groq on
  heavy/agentic jobs, but normal chat stays small enough to fit. The local model has no token cap
  but an 8B is **slow (~20–30s) and unreliable at tool-calling** in the interactive hot path — so
  it's the *fallback*, not the everyday driver.
- **Heavy autonomous cron jobs** (e.g. "scan web + draft a digest") exceed Groq's 12k TPM and do
  NOT auto-route to local (Groq's 413 "too large" isn't a fallback trigger, and the framework has
  no clean per-job provider override). **These are currently paused.** The plan: run them as
  dedicated `no_agent` scripts that call the local Ollama directly, bypassing the chat-agent path,
  when we build the autonomous/outreach layer.
- **Reminders** don't use the LLM at fire time at all (see §8).

Historical note: the original failure that kicked off this project was a dead brain — a stray
`GOOGLE_API_KEY` in `.env` hijacked routing to Google's endpoint while requesting a Groq model
(404 on every call), compounded by a missing `model.base_url`. Fixed. The assistant had *looked*
like "Telegram is broken" but Telegram was fine the whole time.

## 7. Identity + custom capabilities
`SOUL.md` defines a **single persona — Jack** — operating across both surfaces. Behaviour is still
scoped by surface, but it is one identity, not two. *(Historical note: this was formerly a
two-persona split — **Jack** in DMs and **Hamza** as the Vytal ops manager — retired and unified on
2026-06-18. Older reports/logs keep the "Hamza" name as a factual record.)*
- **As Chief of Staff — Arnav's DMs:** Idea Vault (saves ideas to memory/files), Travel concierge,
  Social engine (LinkedIn/X posting scripts), native image generation, **Reminders** (uses
  `remind.py`, see §8).
- **As Vytal Operations Manager — the "Vytal" Telegram group** (`-1003797274797`): bosses Arnav
  (technical) + Spandan (marketing); lead/bug database (appends to `vytal_leads.md`/`vytal_bugs.md`),
  meeting secretary, growth engine (cold-email drafting, scraping), stealth web scraping.

**Custom scripts** in `~/.hermes/bin/` (invoked by the agent via its terminal tool): a Vytal
outreach pipeline (`hamza_orchestrator.py`, `stealth_scrape.py` (camoufox stealth browser),
`validator_agent.py`, `sheets_agent.py`, `contextual_writer_agent.py`,
`outbound_dispatcher_agent.py` (Gmail SMTP)), social posting (`send_linkedin_post.py`,
`send_x_post.py`), image gen (`generate_and_send_image.py`), and model switchers. These map to the
"outreach engine" and "social" capabilities — sends remain approval-gated.

17 skill packs are installed (email, github, productivity, research, social-media,
software-development, etc.).

## 8. Reminders (zero-token, always reliable)
- Helper: `~/.hermes/scripts/remind.py`. Usage:
  `python remind.py "<schedule>" "<message>" [chat_id]` where schedule is a cron expr
  (`0 18 * * *`) or relative (`30m`, `2h`).
- It writes a per-reminder sidecar `.txt` (the message) + a tiny `.sh` (`cat` the txt), then
  creates a **`no_agent` cron job** that delivers the script's stdout to Telegram. **No LLM at
  fire time** → reminders always land even if every model is rate-limited or offline.
- SOUL.md instructs Jack: on "remind me to X at Y", convert the time to a schedule and call
  `remind.py`. (Creation parses the time via the LLM while chatting; firing is LLM-free.)

## 9. Guardrails / hard rules
- Never auto-send outreach, spend money, place orders, or merge code without approval.
- Credentials are secrets: `.env` on the box, `secrets/` locally (gitignored). SSH is key-only.
- Be honest about failures — surface broken steps, don't fake success.
- Cold outreach must respect anti-spam / DPDP; protect sender reputation.
- Prefer free + reliable over clever + fragile.

## 10. Current status (2026-06-15)
> Status corrected against a verified read-only VPS audit on 2026-06-15 — see
> [docs/AUDIT.md](./docs/AUDIT.md). The prior "interactive chat fast + clean" note was stale.

**🔴 Critical instability (verified live):** interactive chat currently **413s on every
substantive turn**. The fixed per-turn overhead is ~17k tokens — dominated by the
**skills-hub prompt** (`.skills_prompt_snapshot.json`, ~10.6k tok / 73 skills) + SOUL.md +
system prompt + tool schemas — which exceeds Groq free tier's 12,000 TPM. The 413 →
compress path can't shrink fixed overhead, so the session auto-resets and the bot replies
with a 218-char error. (Proof: a 3-char `het` message → "Requested 17,047".) Fix =
ROADMAP Phase 0: shrink the skills prompt (`/context` → `skills-pruner`).

**Working:**
- ✅ Zero-token reminders that always deliver (no LLM at fire time).
- ✅ Local Mac brain as fallback (SSH tunnel + launchd persistence); per-job routing exists.

**Built 2026-06-20 (local only — not yet deployed):** three new `integrations/` modules, all with
lazy imports + graceful degradation (they no-op when creds/libs absent), 21 new tests, 191 total green:
- **Garmin** (`integrations/garmin.py`) — `GarminClient.sleep_summary_text()`; pulled into the morning
  briefing when `JACK_GARMIN_ENABLED=1` + `GARMIN_EMAIL`/`GARMIN_PASSWORD` set. (Login is fragile by nature.)
- **Google Calendar** (`integrations/calendar.py`) — service-account auth against `~/.hermes/credentials.json`;
  `add_event`/`list_events`; new Discord `calendar` intent (add/list) wired in `jack_intent_router.py`+`handler.py`;
  pulled into the briefing. **Gated** until: Calendar API enabled on `vytal-499305` + Arnav shares his calendar
  with `vytal-732@vytal-499305.iam.gserviceaccount.com` (else every call 403s → "calendar not connected yet").
- **Claude bridge** (`integrations/claude_bridge.py`) — `python -m integrations.claude_bridge` summarizes a pasted
  claude.ai session → appends to USER.md (reuses `MemoryUpdater.update_user_md`) + Discord notify. Manual-paste
  only this round; automatic Claude Code capture deferred.
- Deps in `integrations/requirements.txt` (garminconnect, google-api-python-client, google-auth). Deferred by
  design: the standalone Garmin daily-sync service + USER.md `[HEALTH & FITNESS]` persistence (briefing-pull instead).
- ✅ SSH hardened (key-only). Gateway service active.

**Paused / deferred:**
- ⏸ Heavy agentic cron jobs ("Learning Engine", "Daily AI Social Drafts") — paused; their last
  recorded error was a pre-fix Google-404, and they would 413 on Groq. Route to local Ollama via
  per-job override / `no_agent` (`skills/cron-router`).
- ⚠️ Live SOUL.md still defines the legacy **Hamza** group persona bound to stale group
  `-5439847434` (live group is `-1003797274797`). The cutover unifies it to **Jack** and fixes the group ID.

**Built locally, offline-validated (NOT yet on the VPS):**
- 🟢 **Lead Engine — event-driven worker system** (`worker/` + `bin/leadgen.py`). Hermes Prime
  orchestrates a concurrent asyncio worker pool over a filesystem task queue
  (`tasks/{pending,running,completed,failed}`, atomic `os.rename` claims). 5 worker types:
  **Discovery** (scrape+extract per source), **Research** (email from clinic site), **Social**
  (instagram), **Outreach** (draft pitch, draft-only), **Validation** (mandatory provenance gate).
  B(research)∥C(social) run concurrently per lead; all leads in parallel. On completion Prime emits
  `task_complete` → Discord summary (webhook, dry-run aware). Dynamic specs `{target,location,
  columns}` accept the mission's `columns` and LEAD-ENGINE's `fields`. Dynamic-column sheet writer
  (gspread `value_input_option=RAW`, CSV fallback offline) with provenance cols always injected.
  Run: `python3 bin/leadgen.py --spec spec.json [--mode parallel|sequential|compare]`.
  - **Env:** `HERMES_TASKS_ROOT`, `HERMES_OUT_DIR`, `HERMES_CONCURRENCY` (pool, default 8),
    `HERMES_LLM_CONCURRENCY` (Groq throttle, default 3), `HERMES_LLM_RETRIES` (backoff, default 3),
    `HERMES_SIM_LATENCY` (mock), `HERMES_SHEET_ID`/`HERMES_GOOGLE_CREDS` (gspread),
    `DISCORD_WEBHOOK_URL` (+ shared `HERMES_LLM_MOCK`, `HERMES_DISCORD_DRYRUN`).
  - **HONEST GAP / next step:** this is a *local* implementation generalizing the LEAD-ENGINE design;
    it does NOT replace the live VPS `~/.hermes/bin/hamza_orchestrator.py` yet, and has only run
    against mock fixtures (`mock://` provenance) — no real Practo/Jina/Groq/Sheets call. **Deploy
    decision pending:** ship `worker/` to the VPS (replace the script) vs. port the validated logic
    back into the one file. The locked LEAD-ENGINE "one-file refactor" framing is superseded by the
    user's explicit "event-driven worker system" directive — reconcile the two on deploy.
  - **Backoff/throttle added** (Groq free 12k-TPM): LLM calls go through a per-loop semaphore +
    exponential-backoff retry in `lib/llm.py`, so the concurrent pool can't burst past the cap.

**Not built yet (roadmap):**
- Voice layer — **re-scoped:** `stt: groq` (Whisper) + `tts: edge` are already configured (cloud,
  free-ish), so a VPS swapfile may not be needed. (Original plan was local Whisper/Piper.)
- Dev-team dispatcher (Claude Code) — the paid piece; built-in `delegation` orchestrator is the base.
- Outreach engine wired into the autonomous local-model path.
- Idea-vault polish; preference-learning depth.

**Open security TODO:**
- Rotate the Groq API key (exposed plaintext in **both** config.yaml and .env — single-source it).
- Audit/prune the 487-line `.env`; verify `.env`/SSH-key perms are `600`.
- Optional: rotate the VPS sudo password (SSH is already key-only).

**Operating framework:** a full `.claude/` org now exists for building/running/debugging Hermes —
17 agents, 11 skills, 7 commands, 3 standards, 4 workflows, 3 templates. Start at
[.claude/INDEX.md](./.claude/INDEX.md). Audit + architecture in [docs/](./docs/) (AUDIT,
ARCHITECTURE, ARCHITECTURE-DECISION, ROADMAP). Locked direction: **Option B** (keep
`hermes-agent`, route deterministic + heavy work around the agent loop).

## 11. How to operate (quick reference)
```bash
ssh -i ~/.ssh/hermes_vps hermes@167.233.108.213          # connect (key-only)
systemctl --user status hermes-gateway.service           # is it up?
tail -f ~/.hermes/logs/gateway.log                       # live activity
tail -f ~/.hermes/logs/errors.log                        # errors
~/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main doctor   # health check
# config: ~/.hermes/config.yaml   secrets: ~/.hermes/.env
# Mac local brain: launchctl list | grep hermes ; logs /tmp/ollama_serve.log, /tmp/ollama_tunnel.log
```
