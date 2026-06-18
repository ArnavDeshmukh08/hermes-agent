# Discord Approval Bot — VPS Deployment Plan (Phase-3)

> **Status: BLUEPRINT ONLY.** This document is a plan. Nothing here has been executed on the box.
> Specialist E — VPS Deployment Requirements. Scope: how the new `discord.py` approval bot is
> installed, run, secured, and cut over on the existing VPS. App-logic design lives in the
> gateway/handler specs; this doc covers deployment, services, dependencies, env, and validation.

## 0. Platform facts (design constraints)

| Fact | Value | Implication |
|------|-------|-------------|
| Host | `167.233.108.213`, Ubuntu | systemd available |
| CPU / RAM | 2 vCPU / **3.7 GB RAM** / **no swap** | a runaway process can OOM-kill; keep RSS small, no swap cushion |
| Disk | ~63 GB free | room for a second venv |
| HERMES_HOME | `~/.hermes` | deploy target root |
| Framework venv | `~/.hermes/hermes-agent/venv` (Python 3.11) | do **not** pollute with app deps |
| Existing service | `systemd --user hermes-gateway.service` | the Telegram/agent gateway; runs as a **user** service (lingering already on) |
| New requirement | persistent `discord.py` WebSocket gateway | needs its **own** long-running service |

**Key difference from Phase-2 Telegram.** Telegram approval reused the *existing* gateway
(`dispatch.py` posts via short-lived HTTPS calls; `approval_handler.py` processes callbacks the
gateway hands it — no persistent connection of its own). Discord is **push/event-driven over a
persistent WebSocket**: the bot must hold an open gateway connection 24/7 to receive button-click
(interaction) events. That is a fundamentally new long-lived process, so it gets a **dedicated
systemd service** rather than riding on the gateway.

---

## 1. New service — `hermes-discord-approval.service`

A `systemd --user` unit running `bin/discord_bot.py` under the Discord venv. User-scoped (not
system) to match the existing gateway and avoid root. Lingering must be enabled so it survives
logout/reboot.

### Unit file

Path: `~/.config/systemd/user/hermes-discord-approval.service`

```ini
[Unit]
Description=Hermes Discord approval bot (persistent gateway, button approvals)
# Network must be up before the WebSocket gateway connection is attempted.
After=network-online.target
Wants=network-online.target
# Soft ordering: nightly chain feeds the queue this bot reads. Not a hard dep.
After=hermes-gateway.service

[Service]
Type=simple
# Absolute path to the DEDICATED Discord venv interpreter (see §2).
ExecStart=%h/.hermes/venv-discord/bin/python %h/.hermes/app/bin/discord_bot.py
WorkingDirectory=%h/.hermes/app
# Secrets + paths live in the env file (chmod 600, never committed). See §4.
EnvironmentFile=%h/.hermes/.env

# Resilience: discord.py reconnects internally, but if the process dies, respawn.
Restart=on-failure
RestartSec=5
# Back off if it crash-loops (e.g. bad token) instead of hammering the API.
StartLimitIntervalSec=300
StartLimitBurst=5

# Memory guardrail — NO SWAP on this box, so cap RSS and let systemd kill+restart
# a leaking process rather than letting the OOM killer pick a victim at random.
MemoryMax=300M
MemoryHigh=200M

# Don't let logs grow unbounded; journald handles rotation.
StandardOutput=journal
StandardError=journal
# Never echo the token into logs (the code already avoids this; belt-and-suspenders).
SyslogIdentifier=hermes-discord

[Install]
WantedBy=default.target
```

### Enable (after the file exists)

```bash
loginctl enable-linger "$USER"          # survive logout/reboot (likely already on for the gateway)
systemctl --user daemon-reload
systemctl --user enable --now hermes-discord-approval.service
systemctl --user status hermes-discord-approval.service
```

### Resource footprint

- Idle `discord.py` 2.x bot: **~50–90 MB RSS** (CPython 3.11 base ~25–35 MB + discord.py +
  aiohttp + the single gateway socket). Negligible CPU when idle — it sleeps on the socket and
  only wakes on a heartbeat (~every 40 s) or an interaction event.
- On 3.7 GB this is **well within budget** even alongside the gateway and the nightly chain.
- **No-swap risk:** the steady state is tiny, so OOM is not a concern in normal operation. The
  `MemoryMax=300M` cap (≈3–5× expected RSS) is a safety net: if a dependency leaks, systemd
  restarts the unit instead of the kernel OOM-killer culling something else. Set the cap, do
  **not** rely on swap (there is none).

---

## 2. Dependency install — `discord.py` in a dedicated venv

`discord.py` is a **new pip dependency** (Phase-2 MVP is stdlib-only; only `lib/llm.py` reaches
out, and it does so over `urllib`, not third-party SDKs). The bot needs it; the rest of the app
stays stdlib-only.

### Decision: dedicated venv `~/.hermes/venv-discord` (NOT the framework venv)

**Recommendation: create a separate venv just for the Discord bot.** Rationale on this constrained
box:

1. **Isolation / blast radius.** The framework venv (`~/.hermes/hermes-agent/venv`) runs the
   gateway that everything depends on. `discord.py` pulls a transitive tree (`aiohttp`,
   `yarl`, `multidict`, `frozenlist`, `aiosignal`, `attrs`). Installing those into the framework
   venv risks a version conflict that takes down the gateway. A dedicated venv means a bad
   `discord.py` upgrade can only break the Discord bot.
2. **Cheap on disk, free at runtime.** A venv with `discord.py` is ~40–60 MB on disk — trivial
   against 63 GB free. There is **no RAM cost** to having two venvs on disk; only the running
   interpreters consume memory, and the bot would run its own process regardless of which venv it
   used.
3. **Clean rollback.** Cutting the bot back out = stop the service + `rm -rf ~/.hermes/venv-discord`.
   No surgery on the framework venv.

The only downside (a second CPython to keep patched) is minor and worth it for the isolation.

### Create + install

```bash
python3.11 -m venv ~/.hermes/venv-discord
~/.hermes/venv-discord/bin/python -m pip install --upgrade pip
# Pin to a known-good 2.x line. Python 3.11 is fully supported by discord.py 2.x.
~/.hermes/venv-discord/bin/python -m pip install 'discord.py==2.4.0'
# Lock it for reproducibility.
~/.hermes/venv-discord/bin/python -m pip freeze > ~/.hermes/app/requirements-discord.txt
```

- **Version pin:** `discord.py==2.4.0` (2.x is the actively maintained line, supports app commands
  + components/buttons + Python 3.11). Pin exactly so an `apt`/cron upgrade can't silently bump it.
  Re-pin deliberately when upgrading and re-run validation (§8).
- **Python 3.11 compatibility:** confirmed — discord.py 2.x supports 3.8–3.12. Use the box's
  `python3.11` to build the venv so the interpreter matches the framework venv.
- **Keep the rest stdlib-only.** `lib/`, the nightly chain (`research.py`, `cmo.py`), and `store.py`
  must not gain new pip deps. Only `bin/discord_bot.py` imports `discord`. The bot calls into
  `lib.store` / `lib.contracts` (pure stdlib), so those modules run fine under either venv.

---

## 3. Discord setup — one-time, manual, by Arnav

All in the [Discord Developer Portal](https://discord.com/developers/applications) + Arnav's
server. Done once; produces the IDs/token for §4. **Least privilege throughout.**

1. **Create the application + bot.**
   - Developer Portal → *New Application* → name it (e.g. "Hermes").
   - *Bot* tab → *Add Bot* → *Reset Token* → copy the **bot token** (this is `DISCORD_BOT_TOKEN`;
     treat as a secret, store only in `.env`).

2. **Intents — minimal.**
   - In the *Bot* tab, **leave all Privileged Gateway Intents OFF**: do **not** enable
     *Message Content*, *Server Members*, or *Presence*. Button approvals arrive as **interaction
     events**, which need no privileged intent.
   - In code, request only the default/non-privileged intents (effectively `Intents.none()` plus
     `guilds`). Interactions are delivered regardless of message-content intent.

3. **Invite the bot (OAuth2 URL Generator) — least-privilege permissions.**
   - Scopes: `bot` and `applications.commands` (the latter only if slash commands are used to
     trigger a manual repost; buttons alone don't require it, but it's harmless and useful).
   - Bot permissions (tick only these): **View Channel**, **Send Messages**, **Embed Links**.
     Optionally **Read Message History** so the bot can edit its own prior approval messages.
   - **No** Administrator, **no** Manage Server/Channels/Roles, **no** Mention Everyone.
   - Open the generated URL, select Arnav's server, authorize.

4. **Create a private approval channel.**
   - New text channel, e.g. `#hermes-approvals`.
   - Permissions: deny `@everyone` *View Channel*; allow **only** Arnav and the bot's role
     *View Channel + Send Messages*. This is the channel-privacy control validated in §8.

5. **Collect IDs** (enable *Developer Mode* in Discord → User Settings → Advanced, then
   right-click → *Copy ID*):
   - **Guild (server) ID** → `DISCORD_GUILD_ID`
   - **Approval channel ID** (`#hermes-approvals`) → `DISCORD_APPROVAL_CHANNEL_ID`
   - **Arnav's user ID** → `DISCORD_ALLOWED_USERS`

---

## 4. Env / secrets

Append to `~/.hermes/.env` (gitignored; `chmod 600`; loaded by the unit's `EnvironmentFile`).

```dotenv
# --- Discord approval bot (Phase-3) ---
DISCORD_BOT_TOKEN=xxxxxxxx.xxxxxx.xxxxxxxxxxxxxxxxxxxxxxxxxxx   # secret — bot token
DISCORD_ALLOWED_USERS=123456789012345678                       # Arnav's user id (comma-sep; EMPTY ⇒ deny-all, fail-closed)
DISCORD_APPROVAL_CHANNEL_ID=234567890123456789                 # the private #hermes-approvals channel
DISCORD_GUILD_ID=345678901234567890                            # Arnav's server

# --- Shared (already present / required) ---
HERMES_MEMORY_ROOT=/home/<user>/.hermes/memory                 # the REAL store (see §5)
GROQ_API_KEY=...                                               # brain (unchanged)
```

Secret-handling rules (mirror the Telegram handler's invariants, now applied to Discord):

- **Fail-closed auth:** `DISCORD_ALLOWED_USERS` empty ⇒ the bot rejects every interaction (same
  pattern as `_allowed_users()` in `bin/approval_handler.py`). Every button click is checked against
  this set; clicks from anyone else are ignored/ephemerally refused.
- **Token never logged.** The unit sets `SyslogIdentifier=hermes-discord` and the code must never
  print the token (matching the existing "bot token is never logged" invariant).
- **`chmod 600 ~/.hermes/.env`**, owned by the run user. Keep it out of git (the repo's
  `.gitignore` already excludes `secrets/`; `.env` lives only on the box).
- **Telegram envs** (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`, `TELEGRAM_DM_CHAT_ID`,
  `HERMES_TG_DRYRUN`) can be **removed after cutover** (§7) — keep them until the Discord path is
  proven so rollback stays one step.

---

## 5. Memory store path

Set **`HERMES_MEMORY_ROOT=~/.hermes/memory`** — the real, app-owned store, distinct from the
framework's own `~/.hermes/memories/` (note: different directory). `lib/store.py:memory_root()`
honors `HERMES_MEMORY_ROOT` and falls back to `<repo>/memory` only when unset, so the env var is
what binds the deployed bot to the live store.

- The Discord bot, the nightly `cmo`, and `research` all resolve the **same root** via this one env
  var, so a draft written by `cmo` lands in `memory/content/`, gets queued, the bot posts it, and an
  Approve writes `memory/approved/` — one consistent tree.
- `store.ensure_tree()` idempotently creates `research/ content/ approvals/ voice/ approved/` and the
  queue + `approvals/decisions.jsonl`. Safe to call on bot startup.
- Deployment copies app code to `~/.hermes/app/` (`lib/ bin/`), but **not** the memory tree — point
  `HERMES_MEMORY_ROOT` at the persistent `~/.hermes/memory` so redeploys never clobber data.

---

## 6. Cron / scheduling — how a draft reaches Discord

**Unchanged nightly chain.** `research` → `cmo` still run as the `no_agent` nightly chain (cron/
timer, exactly as today). They produce drafts and enqueue pending items into the store. **This plan
does not change that scheduling.**

**Posting trigger — bot loop (recommended), not a cron dispatch step.** Because the Discord bot is
already a persistent process, the cleanest design is a `discord.py` background task
(`@tasks.loop(seconds=…)` or an on-ready scheduled loop) that:

1. Polls the store for pending items not yet posted to Discord (the queue already tracks a per-item
   `dispatched_at`/posted flag — see `dispatch.py:_already_dispatched` and `store.find_pending`; the
   Discord bot uses the analogous "already posted to Discord" stamp).
2. For each new pending draft, posts an embed + an `[Approve | Reject | Revise]` button row to
   `DISCORD_APPROVAL_CHANNEL_ID` and stamps it posted (idempotent — a restart re-reads the queue and
   skips already-posted items).
3. Receives button clicks as interaction events and runs the same write-ahead decision logic the
   Telegram handler uses (append decision → only Approve writes `approved/` → idempotent on
   re-clicks).

This replaces the Telegram model where a separate `dispatch.py` step pushed via HTTP. With a
persistent bot, **no extra cron entry is needed for posting** — the in-process loop covers it, and
it self-heals on restart by reconciling against the queue. (If preferred, the nightly chain could
instead drop a "ready" marker and the bot's loop just reacts to it; either way the loop owns
posting.)

---

## 7. Coexistence & cutover

Both interfaces can run **simultaneously** during migration — they read the **same store**, and the
decision logic is write-ahead + idempotent, so whichever interface Arnav taps first resolves the
item and the other simply sees it already resolved.

### Cutover steps

1. **Stage** (Discord live, Telegram still primary):
   - Install venv (§2), drop unit file (§1), add Discord env (§4), keep Telegram env intact.
   - `systemctl --user enable --now hermes-discord-approval.service`.
   - Run validation (§8). Approve a test draft from Discord; confirm `approved/` written.
2. **Soak** (both live for 1–2 nightly cycles): let real nightly drafts post to **both** channels.
   Confirm Discord approvals land correctly and nothing double-fires (idempotency holds).
3. **Cut Telegram off:** stop Telegram posting (set `HERMES_TG_DRYRUN=1` or disable the gateway's
   dispatch step) so new drafts go only to Discord. Discord is now primary.
4. **Decommission Telegram:** once confident, remove the Telegram envs from `.env` (§4) and remove/
   disable the Telegram dispatch path. The gateway service itself stays if it does other work;
   otherwise stop it.

### Rollback (any time during soak/cut)

1. `systemctl --user disable --now hermes-discord-approval.service` (stops the bot; no new Discord
   posts).
2. Re-enable Telegram posting (clear `HERMES_TG_DRYRUN`, restore Telegram envs if removed, re-enable
   the gateway dispatch step).
3. The store is untouched — pending items are still pending and now flow back through Telegram.
4. To fully remove: `rm -rf ~/.hermes/venv-discord` and the unit file, then `daemon-reload`.

Rollback is clean because the two interfaces share one store and neither owns state the other can't
read.

---

## 8. Validation on the box (read-only / health checks)

Run after staging. **No autonomous publishing** — the only "send" is one deliberate test draft that
Arnav approves by hand.

1. **Service up & connected.**
   ```bash
   systemctl --user status hermes-discord-approval.service        # active (running)
   journalctl --user -u hermes-discord-approval.service -n 50 --no-pager
   ```
   Expect a "logged in as <bot>#xxxx" / "gateway connected" line and **no token in the logs**.

2. **Memory binding correct.**
   ```bash
   systemctl --user show hermes-discord-approval.service -p Environment   # HERMES_MEMORY_ROOT present
   ls -la ~/.hermes/memory                                                # research/ content/ approvals/ approved/ exist
   ```

3. **Channel is private (manual, in Discord).** As a non-Arnav account (or check perms),
   `#hermes-approvals` must be invisible. Confirm only Arnav + bot role have *View Channel*.

4. **Resource footprint sane (no-swap check).**
   ```bash
   systemctl --user status hermes-discord-approval.service | grep Memory   # expect ~50–90M, under MemoryMax=300M
   free -m                                                                 # confirm headroom; swap = 0 expected
   ```

5. **Test draft posts with buttons.** Drop one synthetic pending item into the store (or run the
   nightly chain in a test mode) and confirm an embed with `[Approve | Reject | Revise]` appears in
   `#hermes-approvals`.

6. **Approve writes `approved/`.** Click **Approve** on the test item, then:
   ```bash
   ls -la ~/.hermes/memory/approved/                              # new approved record present
   tail -n 5 ~/.hermes/memory/approvals/decisions.jsonl          # decision appended (write-ahead) before the approved/ write
   ```

7. **Auth fail-closed.** Confirm a click from a non-allow-listed user is refused (and that an empty
   `DISCORD_ALLOWED_USERS` denies all) — same invariant as the Telegram handler.

8. **Restart resilience.** `systemctl --user restart hermes-discord-approval.service`; confirm it
   reconnects and does **not** re-post already-posted items (idempotent reconciliation against the
   queue).

---

## Summary of deployment requirements

- **New service:** `systemd --user` unit `hermes-discord-approval.service` running
  `bin/discord_bot.py` under the Discord venv; `Restart=on-failure`, lingering on,
  `After=network-online.target`, `MemoryMax=300M` (no-swap safety net). ~50–90 MB RSS — fine on 3.7 GB.
- **venv decision:** dedicated `~/.hermes/venv-discord` with `discord.py==2.4.0` (Python 3.11),
  isolated from the framework venv for blast-radius/rollback; rest of app stays stdlib-only.
- **One-time Discord setup (Arnav):** create app+bot, copy token, **no privileged intents**, invite
  with least-privilege perms (View/Send/Embed), private `#hermes-approvals` locked to Arnav+bot,
  collect guild/channel/user IDs.
- **Env additions** to `~/.hermes/.env` (chmod 600, never logged): `DISCORD_BOT_TOKEN`,
  `DISCORD_ALLOWED_USERS`, `DISCORD_APPROVAL_CHANNEL_ID`, `DISCORD_GUILD_ID`; `HERMES_MEMORY_ROOT=~/.hermes/memory`.
  Telegram envs removable post-cutover.
- **Cutover:** stage → soak (both live, shared store, idempotent) → cut Telegram off → decommission.
  **Rollback:** disable the Discord service + re-enable Telegram; store is shared and untouched.
