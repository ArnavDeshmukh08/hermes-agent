# Hermes Agent — Living Memory / Status Log

> Update this every working session. Newest entries on top.
> Goals & rules live in [CLAUDE.md](./CLAUDE.md). Credentials in `secrets/` (gitignored).

## System facts (the box)
- VPS: `167.233.108.213`, user `hermes`, Ubuntu 26.04, **2 vCPU / 3.7 GB RAM / no GPU / no swap**, 63 GB free.
- Python 3.14 system / **venv uses Python 3.11.15**, Node 22. No Docker.
- The assistant is the open-source **hermes-agent** framework (v0.16.0, config schema v29),
  installed at `~/.hermes/hermes-agent`, data/config in `~/.hermes/`.
- Supervised by a **systemd --user** unit: `hermes-gateway.service` (enabled, lingering on).
  - Control: `systemctl --user {status|restart|stop} hermes-gateway.service`
  - Or framework CLI: `~/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway ...`
- Brain: **Groq** (free) via OpenAI-compatible endpoint. Model `llama-3.3-70b-versatile`.
- Interface: **Telegram** (polling mode). Active group: **"Vytal"** `-1003797274797`.
- Memory: `~/.hermes/state.db` (~8.8 MB, 28 sessions), `kanban.db`, `memories/`, `sessions/`.
- Persona/context files: `~/.hermes/SOUL.md`, `~/.hermes/USER.md`.
- Custom agents already built by Arnav in `~/.hermes/bin/`: `hamza_orchestrator.py`
  (outreach/scraper persona "Hamza"), `browser_agent.py`, `contextual_writer_agent.py`,
  `generate_and_send_image.py`. Stealth browser stack present (camoufox / agent-browser).

## 2026-06-14 — Session 1: diagnosed & fixed the dead brain
**Symptom reported:** "fails a lot", "Telegram group chat isn't working."

**Root cause (NOT Telegram — the brain was dead):**
1. `~/.hermes/.env` had an **active `GOOGLE_API_KEY=AQ.Ab8RN6...`** (malformed — real
   AI Studio keys start with `AIza`). Hermes auto-selects its provider by scanning env
   keys, so it routed every LLM call to Google's endpoint
   (`generativelanguage.googleapis.com`) while requesting a **Groq** model →
   `HTTP 404 model not found` on every call. The bot received messages but couldn't reply.
2. After disabling the Google key, it surfaced a second latent bug: the top-level
   `model:` block in `config.yaml` had **no `base_url`** (the Groq URL lived only in a
   separate `custom:` block, which bare-`custom` resolution doesn't read). With an empty
   `model.base_url`, provider resolution returned "No LLM provider configured."

**Fixes applied (with backups):**
- `.env`: commented out the hijacking `GOOGLE_API_KEY` (backup `.env.bak.*`).
- `config.yaml`: added `base_url: https://api.groq.com/openai/v1` under the `model:` block
  (backups `config.yaml.bak.*`).
- Restarted `hermes-gateway.service`.

**Verification:**
- Direct Groq curl: ✅ `llama-3.3-70b-versatile` returns output.
- Framework resolver `resolve_runtime_provider()`: ✅ now returns
  `provider=custom, base_url=api.groq.com, api_mode=chat_completions` (no more Google).
- Real LLM call through framework client: ✅ replied `BRAIN_OK`.
- Gateway restart: ✅ active, Telegram connected, **"Channel directory built: 1 target(s)"**
  (Vytal group registered; was 0 before), cron ticker running, no new 404s.

**Confirmed healthy / NOT the problem:**
- Telegram connection, bot commands, and `telegram.allowed_chats` (includes Vytal group).
- Groq API key (in `.env`, len 56) is valid.
- `hermes doctor`: all green except intentionally-disabled Gemini OAuth.

## ✅ LIVE TEST PASSED (2026-06-14 13:52 UTC)
- Arnav sent "Hello hamza, are you there" in the Vytal group → **response ready in 2.1s,
  api_calls=1** (real Groq call), 225 chars sent back. Brain confirmed working on Telegram.
- Side note: an old bloated session (`20260613_182004`, full of failed-retry history) hit
  context-overflow and the framework **auto-reset it** — self-healed, expected.

## 2026-06-14 — Session 1b: auxiliary providers wired to Groq
- Routed ALL `auxiliary:` tasks (approval, curator, kanban_decomposer, mcp,
  profile_describer, skills_hub, tts_audio_tags, vision, web_extract, + the already-set
  compression/title_generation/triage_specifier) to `provider: custom` + Groq base_url +
  key, via a scoped YAML edit (backup `config.yaml.bak.*`, YAML validated, v29 intact).
  No more OpenRouter/Nous "payment/no-auth" fallback errors for background tasks.
- ⚠ Caveat: `vision` + `tts_audio_tags` now point at `llama-3.3-70b-versatile` (text-only).
  If image/vision input is used, switch those to a Groq vision model
  (e.g. `meta-llama/llama-4-scout-17b-16e-instruct`). Not needed for current text use.
- Investigated two scary-looking errors from the 13:52 test — BOTH were on the old corrupt
  session `20260613_182004` only (max_tokens>32768 400 hit once; "cannot compress 3,550
  tokens"). That session auto-reset; normal sessions are unaffected. Resolved.

## ⚠ DISCOVERED BUG — persona/group-ID mismatch (not yet fixed)
- `~/.hermes/SOUL.md` defines a dual identity: **Jack** (personal chief-of-staff in Arnav's
  DMs) and **Hamza** (Vytal ops manager). Hamza is bound to group **`-5439847434` ("Vytal HQ")**.
- BUT the live registered group is **`-1003797274797` ("Vytal")** — different ID. So Hamza's
  persona/context likely never activates in the real group → matches the `vytal_bugs.md` note
  "Hamza was not answering." Fix: reconcile the group ID in SOUL.md (and `telegram.allowed_chats`)
  with the actual group. NEEDS Arnav's OK before changing persona behavior. Confirm which group
  is canonical (did he make a new "Vytal" group and abandon "Vytal HQ"?).

## System capabilities already built (discovered 2026-06-14)
- 17 skill packs installed (apple, autonomous-ai-agents, email, github, productivity,
  research, smart-home, social-media, software-development, note-taking, media, etc.).
- Custom scripts in `~/.hermes/bin/`: hamza_orchestrator, stealth_scrape, validator_agent,
  sheets_agent, contextual_writer_agent, outbound_dispatcher_agent (Gmail SMTP),
  send_linkedin_post, send_x_post, generate_and_send_image, switch_model{,_groq,_custom},
  test_llm. (Maps directly to CLAUDE.md goals: outreach engine, social, image gen.)
- USER.md + SOUL.md already encode Arnav's profile, tone/banned-words, data guardrails.

## 2026-06-14 — Session 1d: reminder/cron fixes + Groq free-tier TPM wall
- Fixed `model.max_tokens` (was unset → framework reserved a huge output cap → both
  "max_tokens>32768" 400 AND "context exceeded at 5,263 tokens"). Set `model.max_tokens: 8192`
  in config.yaml (backup taken, YAML validated, restarted). Those two errors are GONE.
- Verified reminder pipeline end-to-end: cron job creates, fires on tick, routes to Groq,
  delivers to Telegram DM (telegram:6022105089). Mechanism works.
- **NEW WALL — Groq free tier = 12,000 TPM (tokens/minute).** A full agent turn is ~21–26k
  tokens (tool schemas 42.9 KB/21 tools ≈ 11k tok; system prompt 40.8 KB ≈ 10k tok, incl. 18 KB
  workdir/AGENTS.md context on cron jobs). So heavy cron *agent* jobs 413 ("Request too large …
  TPM Limit 12000, Requested 25975"). Interactive DM chats are smaller and DO work.
- Options to resolve (DECISION PENDING with Arnav — see below):
  A) FREE-lean: trim toolsets (`agent.disabled_toolsets`) + skills + no workdir on cron to fit
     <12k/req; use `--no-agent` script reminders for plain "remind me" (zero LLM tokens).
  B) FREE-alt: get a VALID Google Gemini key (`AIza…`, aistudio.google.com) — Gemini free tier
     has far higher limits than Groq; route heavy jobs (or all) there. (The old key was invalid.)
  C) PAID-small: Groq Dev tier lifts TPM a lot ("pay when it earns").
- Plain reminders (no reasoning needed) should use `--no-agent` jobs → bypass TPM entirely.

### Session 1e — tried "trim to fit Groq" (Arnav's pick). VERDICT: doesn't work.
- Edited `platform_toolsets.{telegram,cli}` (dropped web/generate_image) and `agent.coding_context: off`.
- Re-tested with real cron reminders: request STILL **25,958 tokens → 413** (basically unchanged).
  Cron agent jobs load a full ~26k-token context regardless of those knobs; `prompt-size` is a
  fixed estimator that didn't reflect the edits either. **Config-trimming cannot get this agent
  under Groq's 12k TPM.**
- Reverted the no-benefit trims (restored full tooling, coding_context=auto). **KEPT** the genuine
  fix `model.max_tokens: 8192`. Removed all ZZ_TEST jobs. Interactive Telegram chat still works.
- **Recommendation back to Arnav:** the free path that actually works is a **valid Gemini key**
  (`AIza…`, far higher free limits) for the agentic jobs; OR `--no-agent` for plain reminders;
  OR small paid Groq Dev tier. "Trim Groq" is ruled out by evidence.

## 2026-06-14/15 — Session 1f: LOCAL BRAIN via Mac + SSH tunnel (solves TPM wall)
Architecture: heavy/agentic jobs run on a local model on Arnav's **M1 Max MacBook (32GB)**;
Groq is the automatic fallback when the Mac is away/asleep.
- **Mac:** official Ollama app (Metal, `/Applications/Ollama.app/.../ollama`). The Homebrew
  *formula* was broken (CPU-only, missing llama-server) — removed it, use the app.
- **Tunnel:** SSH reverse tunnel Mac→VPS (`ssh -R 11434:localhost:11434`, key `~/.ssh/hermes_vps`).
  VPS reaches the Mac's Ollama at its own `localhost:11434`. NO Tailscale needed.
- **Persistence (launchd on Mac):** `~/Library/LaunchAgents/com.hermes.ollama-serve.plist`
  (runs Ollama, Metal, `OLLAMA_CONTEXT_LENGTH=65536`, KEEP_ALIVE 30m) +
  `com.hermes.ollama-tunnel.plist` (ssh -R, KeepAlive auto-reconnect). Both RunAtLoad.
  Logs: `/tmp/ollama_serve.log`, `/tmp/ollama_tunnel.log`.
- **Hermes config:** `model:` → primary = local (provider custom, base_url
  `http://localhost:11434/v1`, api_key `ollama`); `fallback_providers:` → Groq
  (llama-3.3-70b-versatile). Fallback triggers on rate-limit/overload/**connection** errors
  (= Mac away). Auxiliary tasks stay on Groq (tiny).
- **Model gotcha:** Hermes requires a **≥64K context** model. Qwen2.5 family is 32K-only
  (Ollama clamps to `n_ctx=32768`) → fails Hermes' check. **Switched to `llama3.1:8b`**
  (native 128K, ~5GB, ~40 tok/s, great tool-use, light on the daily Mac). Qwen2.5:14b kept as
  a spare (only usable if Hermes' 64K min is overridden / for non-Hermes use).
- Local inference verified working end-to-end earlier: VPS→tunnel→Ollama→`/v1` returned output.
- DONE + VALIDATED (2026-06-15): `model.default=llama3.1:8b`, `context_length=65536`,
  `max_context=65536`, `max_tokens=8192`, base_url `http://localhost:11434/v1`.
  - Llama3.1:8b loads at `n_ctx=65536` ✓ (Qwen clamped to 32768 ✗).
  - **Heavy cron job ran on LOCAL** (Ollama log: 13.6k-token prompt @ 344 tok/s, 200 OK,
    truncated=0) — the exact job that 413'd on Groq. **TPM wall solved.**
  - **Fallback validated:** with tunnel down, local→`APIConnectionError`→auto-fell-back to Groq.
    (The heavy *test* job then 413'd on Groq's 12k TPM — expected; light chat falls back fine.)
  - Tunnel restored; both launchd agents healthy.

### Operating model (final)
- **Mac home (most of the time):** everything runs on local Llama 3.1 8B — free, private, no
  rate limits, handles heavy agentic jobs. ~25–40 tok/s, ~14GB RAM while loaded (frees after 30m).
- **Mac at college / asleep:** tunnel drops → Hermes falls back to Groq automatically. Light
  chat + simple reminders work; heavy agentic jobs fail on Groq's 12k TPM (by design) →
  schedule heavy/overnight autonomous jobs for when the Mac is home.
- To switch the local model later: `ollama pull <model>` on the Mac, set `model.default` +
  matching `context_length` in config.yaml, restart gateway. (Spare: qwen2.5:14b, 32K only.)
- Mac control: `launchctl list | grep hermes`; logs `/tmp/ollama_serve.log`, `/tmp/ollama_tunnel.log`.

## When the Mac is at college (expected behavior)
Tunnel drops → primary (local) connection fails → Hermes falls back to Groq for chat + light
reminders. Heavy agentic jobs will hit Groq's 12k TPM and fail/skip until the Mac is back — by
design. Schedule heavy autonomous jobs for overnight when the Mac is home.

## 2026-06-15 — Session 1g: zero-token plain reminders
- Problem: agent-based reminders carry the full ~21k-token agent context → fail on Groq's 12k
  TPM when the Mac is away. Fix = `no_agent` cron jobs (deliver a script's stdout, ZERO LLM).
- Built `~/.hermes/scripts/remind.py`: writes a per-reminder sidecar `.txt` (the message) + a
  tiny `.sh` (`cat` the txt), then creates a `no_agent` cron job delivering to Telegram.
  Usage: `venv/bin/python ~/.hermes/scripts/remind.py "<schedule>" "<message>" [chat_id]`
  (schedule = cron expr `0 18 * * *` or relative `30m`/`2h`). Validated end-to-end: fires,
  delivers, zero LLM, auto-removes.
- SOUL.md: added Jack module #5 "Reminders" instructing him to convert the time → schedule and
  call remind.py (so "remind me to X at Y" always lands, even if the brain is rate-limited/offline).
  Creation parses time via LLM (brain available while chatting); fire-time is LLM-free.
- Final healthy state confirmed: gateway active, Telegram connected, **primary brain resolves to
  local `http://localhost:11434/v1`**, remind.py installed, only the 2 real cron jobs remain.

## 2026-06-15 — Session 1h: brain routing corrected (Groq primary)
- Tested local-8B-primary live: "hey what's up" took **29.7s** and the 8B **leaked a raw
  tool-call as the message** (`{"name":"terminal",...}`). Llama 3.1 8B is too weak/slow for this
  framework's tool-calling in the interactive hot path.
- **Flipped to Groq primary** (`llama-3.3-70b-versatile`) for chat — fast (~2s), reliable tools,
  short requests fit the 12k TPM. Local `llama3.1:8b` is the **fallback** (resilience when Groq
  is down / connection errors).
- ATTEMPTED auto-routing heavy jobs via fallback (Groq 413 → local). **Doesn't work:** Groq's
  413 "request too large" is handled as "compress-and-give-up", NOT a fallback trigger (only
  connection/overload/rate-limit-classified errors trigger fallback). So heavy agent cron jobs
  fail on Groq and do NOT reach local automatically. The framework has no easy per-job provider
  override (cron edit lacks model/provider flags; `providers:` pool empty).
- **Decision:** Groq primary is the daily driver (chat + reminders = the working foundation).
  **Paused** the 2 heavy agentic cron jobs ("Learning Engine", "Daily AI Learning & Social
  Drafts") — they 413 on free Groq. Bring them back later as **dedicated no_agent scripts that
  curl the local Ollama directly** (bypasses Hermes' agent provider-resolution) when we build the
  autonomous/outreach layer. Local Mac brain stays up (launchd) for that future use.

### FINAL working architecture (as of 2026-06-15)
- **Chat (Jack/Hamza on Telegram):** Groq llama-3.3-70b — fast, clean. ✓
- **Reminders:** `remind.py` → no_agent jobs, zero LLM, always deliver. ✓
- **Fallback brain:** local llama3.1:8b on the Mac (via SSH tunnel) for Groq outages. ✓
- **Heavy autonomous jobs:** deferred — will run as direct-to-local scripts when built.

## Known remaining issues / TODO
- [ ] **One-shot `-z` CLI mode** throws `KeyError 'final_response'` — looks like a framework
      bug in this version's one-shot formatter. Does NOT affect the gateway/Telegram path.
- [ ] **No swap + 3.7 GB RAM** — risky if we add local Whisper/embeddings. Add a swapfile
      before the voice layer.
- [ ] History of `config.yaml` corruption (`.corrupt` backup from Jun 13) — always back up
      before editing; prefer surgical edits + YAML validation over full rewrites.

## 2026-06-14 — Session 1c: SSH hardened to key-only
- Generated dedicated key on Arnav's Mac: `~/.ssh/hermes_vps` (ed25519, no passphrase).
  Public half appended to box `authorized_keys` (with Arnav's explicit OK).
- Verified: key login works; **password login now REJECTED** (`Permission denied (publickey)`).
- Disabled SSH password auth via `/etc/ssh/sshd_config.d/00-hardening.conf`
  (named `00-` to beat cloud-init's `50-cloud-init.conf` which set `PasswordAuthentication yes`;
  sshd honors the first value). Validated with `sshd -t` before reload.
- **Connect now:** `ssh -i ~/.ssh/hermes_vps hermes@167.233.108.213`
- Password rotation **deferred** by Arnav (SSH is key-only, so old pw only matters for sudo).

## Security TODO (remaining)
- [ ] (Optional) Rotate the sudo password — old `AmitSarika@31` still valid for sudo only.
- [ ] **Rotate the Groq key** — plaintext in `config.yaml` + appeared in session output
      (`gsk_mZ5x4nYukfOr2Ppp...`). Generate new at console.groq.com → swap into config.
- [ ] The dead `GOOGLE_API_KEY` is disabled (commented), not removed — delete on cleanup.

## How to operate (quick reference)
```bash
ssh hermes@167.233.108.213                      # password in secrets/credentials.md
systemctl --user status hermes-gateway.service  # is it up?
tail -f ~/.hermes/logs/gateway.log              # live activity
tail -f ~/.hermes/logs/errors.log               # errors
~/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main doctor   # health check
# config: ~/.hermes/config.yaml   secrets/env: ~/.hermes/.env
```

## Session 2026-06-15 (evening) — System audit + `.claude` operating framework
**Verified (read-only VPS audit):**
- **Root cause of instability (NEW, verified live):** fixed per-turn overhead ≈17k tokens >
  Groq free 12,000 TPM → **every substantive turn 413s, interactive chat included** (proof: a
  3-char `het` message → "Requested 17,047"). 413 → compress → "cannot compress further" →
  session auto-reset. The dominant contributor is the **skills-hub prompt**
  (`.skills_prompt_snapshot.json`, 42,569 B ≈ 10.6k tok / 73 skills, 18 packs) — NOT tool schemas,
  NOT AGENTS.md (gateway cwd `~/.hermes` has none; coding_context finds nothing). This explains why
  earlier toolset-trimming did nothing (skills load independently of toolsets).
- Per-job `provider/model/base_url` override **works** (`_resolve_model_override` + tests) → heavy
  jobs can route to local Ollama. 413 is classified `payload_too_large` → compress, **never
  failover** (`agent/error_classifier.py:44`), so big requests never reach the fallback chain.
- Config facts: `model.max_context 32768`, `max_tokens 8192`; Groq key in BOTH config.yaml + .env;
  `.env` is 487 lines; voice already cloud (`stt: groq`, `tts: edge`); delegation orchestrator exists.
- Heavy cron jobs (`Learning Engine`, `Daily AI…`) are paused; last error was a pre-fix Google-404,
  so their "413 on Groq" reason is inferred, not freshly observed.

**Built (local-only, no live VPS changes):**
- `docs/`: AUDIT.md, ARCHITECTURE.md, ARCHITECTURE-DECISION.md (Option B locked), ROADMAP.md.
- `.claude/` operating org via Hermes-Prime orchestration (4 specialists → 3-reviewer Review Board
  → integration): 17 agents, 11 skills, 7 commands, 3 standards, 4 workflows, 3 templates + INDEX.md.
- Review Board verdict: APPROVE-WITH-FIXES (0 CRITICAL/HIGH); applied fixes (file-perm checks,
  secret-at-rest `.bak` hygiene, roster + boundary cleanups).
- Corrected CONTEXT.md §10 (prior "interactive fast + clean" was stale).

**Decision:** Option B — keep `hermes-agent`, route deterministic + heavy work around the
monolithic agent loop. Next: ROADMAP Phase 0 — get interactive turns < 12k (`/context` →
`skills-pruner`). All live fixes (skills prune, key rotation, cron routing, group-ID) deferred to a
future approval-gated mission.

## Session 2026-06-16 — Token forensics arc (0.5→0.8) + Phase-1 content-workflow blueprint
**Investigation arc (read-only, evidence-only — all in `reports/`):**
- **0.5 router validation:** the skills-routing fix was NOT proven — `tool_search` confidence 38/100.
- **0.6 falsification (source-level):** **`tool_search` routes MCP/plugin TOOLS, not skills** — the
  skills-router hypothesis is FALSIFIED. Skills reduce via framework `platforms`/`environments`/`conditions`
  frontmatter gating + pack management (config), not `tool_search`.
- **0.7 request-assembly forensics (ground truth via the framework's own `prompt-size` CLI):** a cold
  Telegram turn = **system prompt 22,884 B + tool schemas 42,908 B = 65,792 B ≈ 17k real tok**. **TOOL
  SCHEMAS are the dominant cost (~65%), NOT skills.** The "10.6k skills" of prior phases was the on-disk
  metadata-cache FILE; the RENDERED skills block is only **7,340 B ≈ 1,835 tok (~11%)**. Corrected the record.
- **0.8 profile-gating decision:** **NO-GO** for tool-gating as the 413 fix. Live `state.db` usage (52 sessions):
  terminal 166 (production-critical), delegate_task 2, execute_code/process/patch/search_files/session_search = 0.
  Gating the safe set saves ~2–3k real tok but does NOT clear 12k, and requires framework CODE (no per-tool
  config). The real levers remain: shrink the system prompt/skills, or raise the TPM/tier.

**Phase 1 — first production workflow (BLUEPRINT, not built):**
- 6 parallel architects + 3-reviewer Review Board (all APPROVE-WITH-FIXES) → **`docs/phase1-master-blueprint.md`**
  + 6 component specs (`docs/{research-agent,cmo-agent,telegram-approval,memory-layer,workflow,build-sequence}-spec.md`).
- Pipeline: **Research → CMO → Telegram approval → Approved repo.** Runs as `bin/*.py` scripts + `no_agent`
  cron + filesystem memory (`~/.hermes/memory/{research,content,approvals,voice,approved}/`) — never the agent
  loop (respects 12k). NO vector DB/RAG. **NO autonomous publishing — single human gate: only the gateway
  `approval_handler` approve-branch writes `approved/`.** Native Telegram inline buttons confirmed.
- Blueprint freezes the reconciled JSON contracts, bakes in security fixes (prompt-injection guard +
  URL allowlist, fail-closed auth, DM-only, random nonce), and cuts overengineering. Build order:
  `lib/{contracts,store,llm}` → `research.py` → `cmo.py` → `dispatch.py` + `approval_handler`.
- **No code written, no VPS change** — implementation-ready blueprint only.

**Phase 2 MVP — test harness (Stream E, BUILT):**
- `tests/` now holds a stdlib `unittest` harness (no pytest dep), run via
  `python -m unittest discover -s tests`. Files: `helpers.py` (hermetic env + seed/fixture builders),
  `test_contracts.py`, `test_research.py`, `test_cmo.py`, `test_approval.py`, `__init__.py`.
- Hermetic per-test: fresh temp `HERMES_MEMORY_ROOT` (+ `HERMES_MEMORY_DIR` alias), `HERMES_LLM_MOCK=1`,
  `HERMES_TG_DRYRUN=1`, allowlist/DM chat = 42; seeds copied from repo `memory/`; tearDown removes tmp.
- Covers blueprint Manual Tests #1–#5 + reject + unauthorized + idempotency. **23 tests, all green, offline,
  deterministic.** Maps: #1→test_research, #2→test_cmo, #3→ApprovalApprove, #4→ApprovalReject, #5→Unauthorized.
- Added `bin/__init__.py` so the bin stages import as `bin.<module>` (in-process `main()`/`handle_callback`).
- **Bug fixed (Stream C / `bin/cmo.py`):** `llm.complete(json_only=True)` returns a JSON *string*; cmo read
  `raw.get("variants")` on the string without `json.loads`, so CMO always no-op'd ("llm returned no variants").
  Added a `json.loads` parse (+ `import json`). Manual Test #2 now passes end-to-end.

## Decisions log
- Fix the existing install, do NOT rebuild from scratch — the framework, memory, skills,
  Telegram, and custom agents are all intact; the failure was a 2-line config bug.
- Brain stays on Groq free tier (primary). Auxiliary/fallback providers optional later.
- Foundation = `hermes-agent` (Option B); shrink always-on context + route heavy work off Groq
  rather than fork/rewrite. Evidence: `docs/AUDIT.md`, `docs/ARCHITECTURE-DECISION.md`.

## Session 2026-06-16 (late) — Phase 2: BUILT the content-workflow MVP (working code)
First real code shipped (local repo, not yet deployed to the box). 5 parallel build streams +
3 code reviewers (correctness/security/contract) + integration. **23/23 tests pass on Python 3.9.6;
live end-to-end proven.**
- **Files:** `lib/{contracts,store,llm}.py` (schemas+validators, atomic filesystem store, lean
  LLM caller w/ `HERMES_LLM_MOCK`), `bin/{research,cmo,dispatch,approval_handler}.py`, `tests/` (stdlib
  unittest), `memory/{research,content,approvals,voice,approved}/` + seeds. Stdlib-only, no pip deps,
  no vector DB/RAG.
- **Pipeline (proven live, no manual editing):** `research.py` (→ findings JSON, real source_url
  enforced) → `cmo.py` (reads findings+voice, lean <6k prompt, 2 scored variants 0..1, status `pending`,
  flips `consumed`) → `dispatch.py` (posts to Arnav DM w/ inline Approve/Reject/Revise, dry-runnable) →
  gateway `approval_handler.py` (fail-closed allowlist + DM-only + nonce verify; **approve-branch is the
  SOLE writer of `memory/approved/`**, write-ahead `decisions.jsonl`).
- **Review Board verdict:** Security PASS (all 7 guardrails hold in code — no auto-publish path);
  Contract-compliance PASS (conforms to frozen §1, validators enforce drift); Correctness
  APPROVE-WITH-FIXES (0 crit/high). Applied fixes: dispatch enqueue/stamp robustness, cmo
  validate-draft enforcement, emoji double-count. Stream E also caught+fixed a real cmo `json.loads` bug.
- **Run modes:** mock/offline (`HERMES_LLM_MOCK=1`, `HERMES_TG_DRYRUN=1`) for tests; real mode uses
  Ollama→Groq + Telegram Bot API. **Not deployed to the VPS** — deployment (copy `bin/`+`lib/` to
  `~/.hermes/`, wire the `apr:` branch into the gateway `telegram.py`, register the cron chain) is a
  separate approval-gated step. Memory root is env-configurable (`HERMES_MEMORY_ROOT`).

## Session 2026-06-17 — Phase 3+4: migrated approval transport Telegram → Discord (working code)
Per the approved Phase-3 blueprint set (`docs/discord-*.md` + `phase3-discord-master.md`), built the
Discord transport. **36/36 tests pass on Python 3.9.6; live dry-run e2e proven.**
- **Extracted** the transport-agnostic decision core to **`lib/approval_core.py`** (`decide` + `record_revise`)
  with the Review-Board HIGH fixes: `decide` writes `note=""` (validate_decision needs a str); `record_revise`
  sets `status="revise"`. The core keeps every guardrail: write-ahead `decisions.jsonl`, **only the approve
  branch writes `memory/approved/`**, nonce verify, idempotency.
- **New `bin/discord_bot.py`** (replaces `bin/dispatch.py` + `bin/approval_handler.py`, both DELETED):
  pure testable fns (`is_authorized` user-allowlist **AND** channel-lock fail-closed; `parse_custom_id`;
  `build_components` raw Approve/Reject buttons; `post_pending_once(send_fn=…)`; `handle_interaction`) +
  a lazy-`discord`-import `run()` with the **channel-privacy startup self-check** (refuses to dispatch if
  anyone but Arnav+bot can view the channel) + a CLI (`--post-once`/`--interaction`) for offline test/e2e.
- **Contract renames** (greenfield, hard): `approval.{telegram_message_id→message_id, chat_id→channel_id}`
  + `transport`; queue `discord_message_id`/`channel_id`; `decided_by` → `discord:<id>`; `callback_data` →
  `custom_id` (same `apr:<nonce>:<idx>:<action>`). `validate_*` unchanged (prefix/key-agnostic). research/cmo
  untouched. Grep-gate CLEAN (no `TELEGRAM_`/`callback_data`/`chat_id` in code).
- **Tests:** `tests/test_approval_core.py` (transport-free core) + `tests/test_discord_bot.py` (auth, parse,
  post, approve/reject/unauthorized/wrong-channel/idempotency); deleted `tests/test_approval.py`. e2e:
  research→cmo→`--post-once`→approve `--interaction` (channel 777/user 42) → `approved/<id>.md` + `discord:42`
  decision; a wrong-channel approve writes nothing (fail-closed channel lock holds).
- **Not deployed.** Live run needs `discord.py` in a venv + the `hermes-discord-approval.service` + a private
  channel — the approval-gated deployment in `docs/discord-deployment-plan.md`. Dry-run is fully offline
  (`HERMES_DISCORD_DRYRUN=1`, no token).

## Session 2026-06-17 — Phase 6: BUILT the founder brain-dump capture layer (working code)
The lean Stage-1 from the Phase-5 plan. **44/44 tests pass; live CLI e2e proven; ~422 lines new prod code (<500 target).**
- **Flow:** founder types unstructured text in Discord **#brain-dump** → `lib/knowledge.record_braindump` appends it raw to `memory/knowledge/braindump.jsonl` (zero schema) → nightly `bin/brain.py --parse` classifies+extracts knowledge items (LLM in real mode via `_PARSER_SYSTEM`; deterministic weighted-keyword heuristic in mock/offline/fallback) → stores one JSON per item into `memory/knowledge/{opinions,insights,objections}/` (+ `_review/` for other/low-confidence) → morning `bin/brain.py --report` digests new opinions/insights/objections + a needs-review queue (with full source text). Discord `on_message` hook added to `discord_bot.py` (gated on `DISCORD_BRAINDUMP_CHANNEL_ID`; enables the `message_content` intent only then).
- **Provenance preserved** on every item: source message, timestamp, **discord message id**, extraction confidence, + `extractor:"llm|heuristic"` (flags the degraded path). Conforms to a new `contracts.validate_knowledge_item`. Idempotent (msg-id dedup on capture; `_parsed_cursor.json` on parse). No vector DB/embeddings/RAG/new agents — stdlib + filesystem only.
- **Review Board:** Technical APPROVE-WITH-FIXES (fixed the **1 HIGH bug**: item-id collision on same-second same-text → silent lost-update — now message-id-keyed; added the validator; tightened has-number tag). Content APPROVE-WITH-FIXES (<30s/zero-schema metric **MET**; fixed: morning report now shows full source text in needs-review; flagged extractor path). Devil's Advocate ADJUST.
- **Success metric MET:** founder contributes useful knowledge in <30s by typing in a channel, touching no schemas/files.
- **HONEST GAP (the deliberate Phase-6 boundary + the #1 next step):** captured knowledge is **stored but NOT yet consumed** — the CMO reads `findings`, and the Phase-5 knowledge→findings **projector is not built**, so Phase 6 improves zero posts on its own. Devil's Advocate's strongest point: capture-before-consumption risks the well going dry (no reward for the habit). **Next (Phase 7):** the thin knowledge→CMO consumer/projector + a daily capture nudge (forcing function), so typed knowledge visibly lifts content. Not deployed (needs the `discord.py` bot live per the Phase-3 deployment plan).

## Session 2026-06-17 — Lead Engine: design locked (no code yet) → `docs/LEAD-ENGINE.md`
Mission: "Build Hamza Lead Engine, not another infrastructure layer." Orchestrator → parallel specialists → Review Board → synthesis. **Outcome: design finalized, ~80-line/one-file refactor, ready to code next session.**
- **Pre-work verdicts:** **CodeGraph REJECTED w/ evidence** (works on large local repos like Vytal App 408 files, but lead code is on the VPS — local MCP can't reach it — and is only ~500 LOC; grep/Read wins). **ECC MINIMAL** kept (`rules/ecc/{common,python}` + planner/code-reviewer/python/security review agents; zero context cost unless invoked; full 105-file stack rejected as bloat).
- **Recon:** all 6 requested capabilities (Search/Scrape/Research/Analyze/Write-Sheet/Outreach) **already exist** in `~/.hermes/bin/hamza_orchestrator.py`. ONLY gap = hardcoded to doctors/clinics + fixed 6-field schema. So this is a **generalization, not a build**.
- **3 design specialists (parallel):** C = JSON task-spec + source dict + dynamic extraction; D = dynamic sheet columns (header-once + `lead_to_row` aligning leads to `fields[]`, reuse `sheets_agent.append_bulk` as-is); E = ranked lead sources → **Practo (volume) + clinic-own-site-via-DDG (email)** for v1; Justdial/Maps are camoufox-only, defer.
- **Review Board (parallel):** **Technical** BLOCK — 1 CRITICAL (runaway loop burning Groq TPM on zero-yield scrapes) + HIGHs (dual `lead_to_row`, `_guard` apostrophe corrupts `+91-` phones, near-miss keys → silent empty cells violating no-fake-data, `ensure_header` raise strands work). **Integration** — MUST-FIX: make **JSON spec the entry** (drop in-pipeline Groq parse), **provenance (`source_url`/`fetched_at`) + validator MANDATORY** not optional, keep it one file, durable per-source tabs, structurally draft-only (no send import). Flagged: route heavy extraction to Ollama (locked §10 rule). **Devil's Advocate** — proposed ~165 → **~28-line minimum**; cut `SOURCE_REGISTRY`(4)→2, `outreach_persona`, `fields[]` `{name,desc}`→strings, `_normalize_spec`, separate `dynamic_columns.py`, `_cell`, `_guard`, `count*3`.
- **Synthesis (locked in `docs/LEAD-ENGINE.md`):** spec = plain strings, 2 source builders (practo+generic_web, no dead Maps/Justdial), one `lead_to_row(lead,fields,draft=None)`, **`value_input_option="RAW"` replaces `_guard`** (kills injection AND phone-mangling in 1 line), write-if-empty header (no raise), `MAX_DRY` early-exit (fixes CRITICAL), dedupe key = phone>email>name, exact-key prompt enforcement (0-line fix for silent-null). **Provider:** v1 keeps **Groq+backoff** for extraction (a `no_agent` script carries no 26k agent baseline → a ~2–3k-tok per-URL call fits 12k TPM; the §10 413 was the agent loop), with a **1-line `base_url` switch to Ollama** as the locked fallback. KEPT against DA minimalism (hard rules): provenance cols + mandatory validator + zero-yield early-exit + defined dedupe. Final ≈ **80 lines, one file**; `sheets_agent`/`scrape`/`stealth_scrape`/`validator`/`dispatcher` untouched.
- **Guardrails preserved:** no auto-send (orchestrator never imports the send fn; drafts → `PENDING REVIEW`), no fake data (no-hallucinate clause verbatim + provenance at scrape boundary), DPDP/anti-spam.
- **Next session:** code the 8 steps in §6 of `docs/LEAD-ENGINE.md` directly on the VPS file; acceptance = a spec with a non-default field set (e.g. `instagram`) produces a tab with those exact columns + `source_url|fetched_at`, real rows, empty (not invented) cells, zero emails sent.

## Session 2026-06-17 (eve) — BUILT: Hamza → event-driven worker system (`worker/` package)
Mission: "Convert Hamza into an event-driven worker system." Implementation-only (no new arch docs, no planning phase). **Delivered, offline-validated: 61 tests pass, Ruff clean, ECC-reviewed, acceptance met with 4.37× parallel speedup.**
- **Tooling:** Ruff 0.12.0 configured (`ruff.toml`, py39, E/F/W/I/B/UP/C4/SIM). CodeGraph: local repo is 12,983 LOC (>5k so it *qualifies*) but it's **not initialized** here and the new code is what I authored — skipped as optional (consistent with the prior LEAD-ENGINE verdict); noted, not a blocker.
- **Architecture built (local control repo, NOT the VPS):** `worker/` = `specs.py` (dynamic `{target,location,columns}`, accepts `fields` alias, frozen `LeadSpec`, slug) · `queue.py` (filesystem task queue `tasks/{pending,running,completed,failed}`, atomic `os.rename` claim, reuses `lib/store.atomic_write_json`) · `events.py` (EventBus, `task_complete`) · `leadgen.py` (scrape Jina+mock, 2 source builders practo/generic_web, dynamic extraction, email/social enrich, draft pitch, dedupe, provenance) · `sheets.py` (dynamic-column writer, gspread `value_input_option=RAW` + CSV fallback, provenance cols always) · `discord.py` (webhook summary, dry-run) · `workers.py` (5 worker types A–E) · `prime.py` (HermesPrime: create task → spawn concurrent pool → monitor → aggregate → emit → notify; `run_parallel`/`run_sequential`/`compare`). CLI `bin/leadgen.py --spec … --mode parallel|sequential|compare`. Reuses `lib/llm.py` (mock/Ollama/Groq) + `lib/store.py`.
- **Concurrency model:** asyncio. Queue-driven pool (default 8) consumes `discover`+`enrich` tasks independently; discovery fans out across sources; every lead enriched in parallel; B(research)∥C(social) gathered per lead. Termination via an `outstanding` counter + `done` Event with "enqueue children before completing parent" ordering (no early-exit race; safe under asyncio cooperative scheduling — confirmed by Technical reviewer).
- **Guardrails preserved:** outreach is **draft-only** (status `PENDING REVIEW`; no send fn imported anywhere — Security reviewer confirmed structurally). Provenance (`source_url`,`fetched_at`) captured at scrape boundary on every lead; **Validation worker is a mandatory gate** (no provenance → rejected before write). Mock leads use transparent `mock://` provenance (never fake-real data).
- **ECC review (4 parallel reviewers, changed code only) → fixes applied:**
  - **Security (2 HIGH):** CSV formula-injection in the CSV fallback (RAW only guarded gspread) → added `_csv_safe` prefix-guard (`=+-@\t\r`) on every cell; `sheet_tab` path-traversal → routed through `slug()` + out-dir containment assert. Plus SSRF guard `is_safe_url` (blocks non-http(s), private/loopback/link-local, localhost, blocked domains) now enforced in `scrape`; stderr-on-fail; task-id stem validation; webhook error prints type only.
  - **Technical (1 HIGH):** sync Discord `urlopen` blocked the event loop via EventBus → `emit` now runs sync handlers in `asyncio.to_thread`. Plus website-regex trailing-period strip; explicit `failed` count (was meaningless in sequential).
  - **Integration (MUST-FIX):** concurrent pool reintroduced the Groq 12k-TPM burst risk the design avoided, with no backoff and 429s swallowed silently → added **exponential-backoff retry in `lib/llm.py`** (`HERMES_LLM_RETRIES`, transient-only) + a **per-loop LLM semaphore** (`HERMES_LLM_CONCURRENCY=3`) decoupling LLM fan-out from pool/scrape concurrency; extract/draft failures now logged to stderr, never silent. Also reused `store.atomic_write_json` instead of a divergent copy.
  - **Devil's Advocate (cut complexity):** DRY'd the duplicated dedupe loop into `_take_new` (shared by both paths). KEPT (justified, not gold-plating): the 4-state queue + 5 worker types + EventBus + CSV fallback + `run_sequential` (all are explicit user requirements or load-bearing for the runtime comparison / offline acceptance).
- **Acceptance (physiotherapy clinics, India, outreach on, non-default `instagram` column):** 12 leads, dynamic header `name|clinic|address|phone|email|website|instagram|source_url|fetched_at|pitch|status`, **12/12 provenance**, 12 personalized pitches all `PENDING REVIEW` (zero sends), queue drained clean (15 completed = 2 discover + 12 enrich + 1 top, 0 failed). **Runtime: sequential 3.81s vs parallel 0.87s = 4.37× speedup.** instagram cell correctly formula-guarded (`'@…`).
- **HONEST GAP (the deliberate boundary):** built + validated **locally against mock fixtures only** — no real Practo/Jina/Groq/Sheets/Discord call yet, and it does **not** replace the live VPS `~/.hermes/bin/hamza_orchestrator.py`. Two implementations now exist; **deploy decision pending** (ship `worker/` to VPS vs. port logic back into the one file). The user's "worker system" directive supersedes LEAD-ENGINE's "one-file refactor" framing — reconcile on deploy.

## Session 2026-06-18 — Option A: worker/ shadow-deployed to VPS + validated on REAL data (cutover staged, NOT executed)
Mission: promote `worker/` to canonical Hamza engine, deploy shadow to VPS, validate on real production data, prepare cutover — but do NOT execute it. **All deliverables produced; worker validated end-to-end on real leads; stopped at "ready for cutover."**
- **Audit (read-only SSH):** `hamza_orchestrator.py` at `/home/hermes/.hermes/bin/` has **ZERO machine dependencies** — no cron, no systemd unit, no Python import. Its ONLY caller is **prompt text**: `config.yaml:55-62` (the `COMMAND:` directive) + `SOUL.md:44-54` (VAGS workflow). One service exists (`hermes-gateway.service`, Telegram gateway) and it does NOT touch hamza. ⇒ migration = text edit; rollback = revert text. Lowest-risk surface possible.
- **Box facts:** Python 3.11.15, gspread 6.2.1, service-account creds (`vytal-732@…`), `GROQ_API_KEY` + `VYTAL_SHEET_ID` present. **No Discord, no Gmail/SMTP** configured (gateway is Telegram). `ddgs` was NOT installed (legacy imports it under try/except → had been silently skipping DDG).
- **Shadow deploy:** shipped self-contained bundle (`worker/` + `lib/{__init,contracts,store,llm}` + `bin/leadgen.py`) to isolated `~/.hermes/hamza_worker/`, run via the existing venv. Production flow (gateway + legacy) untouched throughout.
- **2 real bugs fixed during bring-up:** (1) **Groq Cloudflare-1010 block** — bare urllib UA got 403-banned; fixed by sending a browser `User-Agent` in `lib/llm.py` (the openai SDK legacy uses sets one). (2) installed `ddgs==9.14.4` for real DDG result links. (3) **Dynamic human-label support** — added a column-role resolver (`specs.column_role/role_map`) + made Research/Social/Outreach/extraction/sheets role-aware so labels like "Clinic Name"/"Instagram"/"Personalized Pitch" populate with no per-label code. 61 offline tests still pass, Ruff clean.
- **Real comparison (both live, target = 3 physiotherapy clinics Mumbai):** LEGACY = 2 leads in 33s, **phone/email/website all null** (Practo blocks Jina), no provenance. WORKER = 3 leads (ReLiva) in 16.5s with **real phone `8655960408` + website + provenance**. Worker wins runtime (~2×), data quality, honesty, maintainability; legacy's only edge (camoufox stealth) not needed (worker sources Jina-readable clinic-own-sites).
- **Full production validation (operator-approved):** physiotherapy/India, count 5, mission's exact columns → wrote 5 real leads to a NEW `worker_shadow_test` tab in the master "Vytal OS Master Database" (the 4 prod tabs `Sheet1/Test Tab/Leads/VAGS Leads` untouched — explicit user authorization to use the master file's new tab after the safety classifier blocked a prod-sheet write). **5/5 provenance, 5 pitches PENDING REVIEW (no sends), 0 failures, 37s, exit 0.** Read-back confirmed 6 rows live. **Discord webhook delivered HTTP 204.** Real sources: freelistingindia.in, tuffclassified.com, thephysiofx.in.
- **Deliverables (local repo):** `docs/HAMZA-MIGRATION-REPORT.md` (dependency→replacement table, risk, rollback), `docs/HAMZA-DEPLOYMENT-REPORT.md` (shadow + real validation + comparison), `docs/HAMZA-CUTOVER-CHECKLIST.md` (exact files/services changed, backup, instant rollback).
- **Cutover (NOT executed):** 2 text edits (`config.yaml` Hamza system_prompt `COMMAND:` line → `hamza_worker/bin/leadgen.py --spec -`; `SOUL.md` VAGS recipe → single leadgen call) + soft reload (`kill -USR1 gateway.pid`). Rollback = restore `*.pre_worker_cutover` backups + reload. Legacy script never deleted. Awaiting explicit go.
- **VPS mutations made (shadow scope):** created `~/.hermes/hamza_worker/`, `pip install ddgs` in venv, created `worker_shadow_test` Sheet tab (deletable). No prod config/prompt/service changed. No emails sent.

## Session 2026-06-18 — Intent Router in front of Hamza (Discord ops bypass the 29k agent loop)
Mission: route operational Discord messages straight to the worker, bypassing the agent loop that 413s (29,625 tok > Groq 12k TPM). Discovery → Review Board → build → ECC review → validate. **Built, ECC-reviewed, proven live; pending only the operator's literal in-Discord post for sign-off.**
- **Discovery (read live framework over SSH):** the ONLY native pre-agent short-circuit is the slash-command `emit_collect` decision hook (run.py:7054); plain `@Hamza` text has none → free-text routing needs a touch at the `_handle_message` seam. The 29k path = `_run_agent` (run.py:8482). Deliverable `docs/intent-routing-discovery.md`.
- **Review Board verdict (synthesis):** don't edit the bundled `adapter.py` (wiped on reinstall). Chosen insertion = a **user-space `~/.hermes/hooks/hamza_router/` hook** that on `gateway:startup` **monkeypatches `DiscordAdapter._handle_message`** — free-text `@Hamza` works, ZERO framework-file edits, survives reinstall. Dispatch = **in-process `asyncio.create_task` → reuse `worker.prime.HermesPrime`**, reply via the **live bot** (fixes the webhook→thread mismatch). Cut to lead+status+outreach; aggressive fall-through.
- **Built** (`hooks/hamza_router/`): `hamza_intent_router.py` (pure regex classifier, NO LLM — intents lead/status/outreach, ambiguous→None→agent) + `handler.py` (the startup hook + wrapper + in-process dispatch, reuses worker) + `HOOK.yaml`. 13 unit tests, Ruff clean.
- **ECC review (4 reviewers) → fixes applied:** blocking gspread on the event loop → `asyncio.to_thread`; unrooted fire-and-forget tasks → tracked set + done-callback; **wrapper signature coupling (biggest risk: a framework change would break ALL Discord chat) → `*args,**kwargs`**; no concurrency cap → `Semaphore(2)`; `update_cell` USER_ENTERED formula-injection → `value_input_option=RAW`; outreach regex false-positives ("write a message" was routing) → tightened; missing-config cryptic error → precondition. Auth confirmed safe (router inherits the upstream allowlist/channel/mention gates that run in on_message BEFORE _handle_message).
- **Live validation (on the patched gateway):** `find 5 physiotherapy clinics in Mumbai` → routed → worker → **5 REAL leads** (PhysioWorld, Physio & Beyond; real Practo source_urls), 5/5 provenance, 12.9s, **NO 413, NO agent loop**. `find 4 dentists in Delhi` → 4 leads, 22.7s. `status` → queue counts. Conversational `Hey` → **fell through to the agent (413, expected — router correctly does not intercept chat)**. Hook install confirmed in journal: `[hamza_router] installed router on DiscordAdapter._handle_message`.
- **Phase 5 (old vs new):** OLD Discord→agent = 413, 29,625 tok requested, ~2.4s to FAIL, 0% success. NEW Discord→router→worker = 0 agent tokens, ~13–23s to SUCCEED, real leads written. Success criterion met.
- **Config/footprint:** zero framework-file edits; everything in `~/.hermes/hooks/hamza_router/` reusing `~/.hermes/hamza_worker/`. Rollback = `rm -rf ~/.hermes/hooks/hamza_router && systemctl --user restart hermes-gateway`. Routed lead-gen writes to a NEW `Hamza_Leads` tab (prod tabs untouched). Outreach is draft-only (writes a pitch cell, no email send). Telegram operational msgs still hit the agent (Discord-only patch — accepted scope boundary).

## Session 2026-06-18 — git baseline + Hamza→Jack unification (local repo) + market-example fix
Mission (as briefed): init git, fix "physiotherapy→psychiatry" targeting, remove "dead" `bin/leadgen.py`, rename "Hamza"→"Jack". **Verified the brief's 3 premises first — all 3 were wrong/oversimplified; surfaced them and got corrected decisions before executing.** Local-repo work done; live-VPS cutover staged (gated).
- **Premise falsification (key outcome):** (1) `bin/leadgen.py` is NOT dead — it's the canonical CLI entry point (imports `worker.prime.HermesPrime`/`worker.specs`; cited in CONTEXT run cmd, the deploy bundle, and the cutover plan). **Did NOT delete it.** A callgraph would falsely flag it dead since its only "caller" is a CLI/config invocation. (2) "physiotherapy" is NOT a hardcoded target — it's a dynamic spec param; appears only in examples, historical logs, and completed-run data. (3) "Hamza" was NOT a wrong name — it was a deliberate **second persona** (Jack=DMs, Hamza=Vytal group). Blind find/replace would have collapsed two personas, falsified records, and desynced docs from the live VPS.
- **Decisions (operator):** fully retire Hamza → **one unified Jack persona on both surfaces** (real coordinated migration, VPS gated); market = **examples only** (don't falsify logs/data); **preserve history**; git init now; execute Layers 1+2 now.
- **git baseline (`4a56c7a`):** `git init` + extended `.gitignore` (kept secret rules; added `memory/`, `tasks/`, `out/`, `reports/`, `.backups/`, caches). 116 files committed; verified zero secrets/personal-data staged.
- **Market examples (`d85f183`):** updated 3 forward-looking docstring example specs (`bin/leadgen.py`, `worker/specs.py`, `hooks/.../handler.py`) physiotherapy→psychiatry. **Left untouched:** MEMORY/CONTEXT historical logs, `tasks/completed/*.json` data, `worker/leadgen.py` mock fixtures (test-coupled; real pitch is LLM-generated + target-agnostic), and a real recorded sales objection (`objections-repo.md`, `verifiable:true`).
- **Layer 1 code rename (`08ec06f`):** `hooks/hamza_router/`→`hooks/jack_router/` (git mv), `hamza_intent_router.py`→`jack_intent_router.py`, env `HAMZA_*`→`JACK_*`, default path `…/hamza_worker`→`…/jack_worker`, tab `Hamza_Leads`→`Jack_Leads`, log `[jack_router]`, `@Hamza`→`@Jack` in docstring, attr `_jack_orig_handle_message`, test import updated. **13 intent tests pass**, py_compile clean.
- **Layer 2 doc unification (`7ddac3c`):** collapsed two-persona→single Jack across CONTEXT.md (keystone, by hand) + 12 living `.claude/` & `docs/` guides (delegated to a sub-agent with strict allow/deny list, grep-verified). Kept legacy filename `hamza_orchestrator.py` and live VPS paths (`hamza_worker`/`hamza_router`) as current reality pending cutover. **Historical records kept the Hamza name:** HAMZA-*.md, AUDIT.md, intent-routing-discovery.md, LEAD-ENGINE.md, MEMORY.md.
- **Deliverable:** `docs/JACK-CUTOVER-CHECKLIST.md` — the gated live-VPS half (rename `~/.hermes/{hamza_worker,hooks/hamza_router}`, edit `config.yaml`+`SOUL.md` to one Jack persona, fix group `-5439847434`→`-1003797274797`, switch `@Hamza`→`@Jack` trigger, rename `Hamza_Leads` tab; backup + instant rollback; legacy `hamza_orchestrator.py` untouched). **NOT executed — awaiting explicit go.**

## Session 2026-06-18 (cont.) — VPS cutover EXECUTED + 413 chat mitigation (lite chat)
Operator gave the go. Cutover run on the live box (`personal-os` 167.233.108.213) over SSH; then chased the long-standing conversational 413.
- **Premise corrections on the cutover brief (verified live first):** `~/.hermes/` is NOT a git repo (per-step "git commit" impossible → used `.bak` backups for rollback); `@Hamza` is NOT a code string (it's the bot's Discord display name — manual portal change, can't SSH it); sheet-tab name lives in `handler.py` not `sheets.py`; SOUL.md already on correct group `-1003797274797` (no stale `-5439847434`). `hamza_orchestrator.py` kept (real legacy file in config `COMMAND:`).
- **Cutover executed (backups in `~/.hermes/jack_cutover_bak_20260618_090941/`):** `hamza_worker`→`jack_worker` (mv); deployed renamed `hooks/jack_router/` from local + moved old hook OUT of `hooks/` (so framework scanner can't double-load a `.bak`); `config.yaml` persona `You are Hamza`→`You are Jack`; `SOUL.md` collapsed 2 identities → one **Jack** with Mode A (DMs/CoS) + Mode B (Vytal group/Ops), zero "Hamza"; worker docstring → "Jack lead pipeline"; live Google Sheet tab `hamza_leads`→`Jack_Leads` (1 row preserved). Restart clean: `[jack_router] installed router on DiscordAdapter`. Zero hamza in live hook/worker/config/SOUL except legacy `hamza_orchestrator.py`. ⚠️ Manual TODO: rename the Discord bot's display name `@Hamza`→`@Jack` in the Discord portal (not SSH-able).
- **413 conversational instability (separate, pre-existing):** root cause = per-turn prompt ~29.6k > Groq free **12k TPM**. `prompt-size` breakdown: tool schemas **45.7 KB/23 tools (~11.7k tok, dominant)** + system prompt 22 KB (incl 7.2 KB skills index) + `max_tokens` 8192 (Groq counts reserved output against TPM).
- **Tried & REJECTED — route agent to local model:** repointed `model:` block → local `llama3.1:8b` (pinned 12 `auxiliary.*` blocks to Groq so empty `model: ''` wouldn't 404). One-shot **timed out (180s)** — local prompt-eval of a 30k prompt is too slow. **Reverted to known-good Groq.** (qwen2.5:14b not viable either: 30k > its 32k ctx, and prompt-eval still slow.)
- **FIX SHIPPED — "lite chat" (operator chose hybrid → lite chat + router reminders):** `max_tokens` 8192→**1024**; gated telegram+discord chat toolsets to **memory-only** (disabled `web`,`cronjob`,`terminal` via `hermes tools disable --platform`). Per-platform gating works at runtime even though static `prompt-size` doesn't reflect it (proved: real turn dropped 29,625→**21,327**, then a fresh memory-only one-shot fit Groq with **no 413**, clean reply "Hi Arnav", ~19s). cli left at full capability. **Honest limit:** Groq free 12k can't robustly hold tool-rich chat + history → fresh msgs work, long sessions still auto-reset. Terminal-based chat caps (reminders, image-gen, LinkedIn/X) now OFF in chat by design.
- **NEXT (planned, not built):** operator wants reminders + image-gen + social as **deterministic Discord router intents** (chat platform = Discord) so they work without the heavy agent. Reminders need a **durable Discord-native scheduler** (remind.py is Telegram-only); social sends stay **approval-gated**. TDD + deploy as a focused next session.

## Session 2026-06-20 — Garmin + Calendar + Claude-bridge integrations (built local, deploy gated)
Orchestrated build (Opus plan → 3 Sonnet implementers in parallel → Sonnet wiring; tests/lint/verify run directly). **Verified the brief's premises first** and corrected three before spawning anything:
- **Path:** brief said `memory/updater.py`; real module is `jack_memory/updater.py` (`memory/` is a data dir).
- **Config:** brief said new keys go in `config.yaml`; reality = Jack flags are **env vars in `~/.hermes/.env`** (`config.yaml` is the framework config, 0 `JACK_*` keys). New flags → `.env`.
- **Calendar auth (the big one):** `credentials.json` is a **service account** (`vytal-732@vytal-499305`). It **cannot** impersonate a personal `@gmail.com` calendar (no Workspace domain → no domain-wide delegation). Operator chose **SA + manual calendar-share** model.
- **Operator decisions:** Garmin = build (accepts fragility, adds creds at deploy); Claude bridge = manual-paste now (claude.ai), auto Claude Code capture deferred.

**Built (all lazy-import + graceful-degradation, mock-only tests, zero network):**
- `integrations/garmin.py` — `GarminClient` (sleep). 6 tests.
- `integrations/calendar.py` — `CalendarClient` (service-account, add/list events, IST/RFC3339). New Discord `calendar` intent wired into `jack_intent_router.py` (ordered before reminder so "schedule on my calendar" ≠ reminder) + `_run_calendar` in `handler.py` (reuses `reminders.parser.parse_time`). 7 tests.
- `integrations/claude_bridge.py` — `ClaudeBridge` (summarize→`MemoryUpdater.update_user_md`→Discord notify), runnable `python -m integrations.claude_bridge`. 8 tests.
- `briefing/morning.py` — `_garmin_block`/`_calendar_block` pulled into `compile_briefing`, gated on `JACK_GARMIN_ENABLED`/`JACK_CALENDAR_ENABLED`, default-off keeps the existing briefing tests unchanged. 8 wiring tests.
- `integrations/requirements.txt` — garminconnect, google-api-python-client, google-auth.

**Plan tightening adopted:** `jack_memory/updater.py` unchanged (bridge reuses its writer); bin/sync_claude.py folded into the module; standalone Garmin daily-sync service + USER.md `[HEALTH & FITNESS]` persistence **dropped by design** (briefing-pull instead — avoids an extra service + daily append-only pollution).

**Result:** **191 tests pass** (162 → +21 features +8 wiring), ruff clean, all new files compile. Zero `hamza`; the 10 `physio` hits are **pre-existing lead-gen content** (`worker/leadgen.py` + tests + lead-intent regex), untouched by this work — flagged: contradicts the earlier physiotherapy→psychiatry intent, separate cleanup.

**NOT deployed.** Deploy blockers (operator's manual steps): enable Calendar API on `vytal-499305`; share `arnavdeshmukh008@gmail.com` calendar with the SA email ("Make changes to events"); add `GARMIN_EMAIL`/`GARMIN_PASSWORD` + `JACK_*` flags to `.env`; `pip install -r integrations/requirements.txt` on VPS; `chmod 600 ~/.hermes/credentials.json` (currently 644). Deploy itself restarts the live gateway → awaiting explicit go.

**DEPLOYED 2026-06-20 (operator completed Google steps + gave Garmin creds, approved restart):** SCP'd `integrations/` + `briefing/morning.py` + 2 hook files to `~/.hermes/`; `pip install` into the service venv (`hermes-agent/venv`); `chmod 600 credentials.json`; appended 5 keys to `.env` (calendar ID/enabled, garmin enabled + creds via stdin, not argv). Backups: `~/.hermes/integrations_deploy_bak_20260619_191020`. **Calendar VERIFIED live** — `CalendarClient.list_events()` → `[]` (empty, no 403) ⇒ Calendar API enabled + SA calendar-share both working. **Garmin 429 rate-limited** on the VPS IP (login blocked) — degrades cleanly. **Bug found + fixed during verification:** a present-but-empty Garmin sleep DTO (what a rate-limited/unsynced fetch returns) produced a misleading `"0.0h sleep"` instead of degrading; added a `total_sleep_h <= 0 → None` guard + 2 regression tests (now **193 pass**), redeployed `garmin.py`. Gateway + briefing restarted clean (all 3 services active, discord connected, hook loaded). **Live write test (add_event) still pending** — operator to run TEST A on Discord. Garmin sleep will stay omitted from the briefing until the 429 clears.
