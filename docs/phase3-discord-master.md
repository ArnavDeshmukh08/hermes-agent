# Phase 3 — Master Blueprint: Telegram → Discord Transport Swap

> Hermes Prime integration of the Phase-3 swarm (A gateway · B risk · C migration · D workflow ·
> E deployment) + Review Board (Technical APPROVE-WITH-FIXES · Security 6/6 PASS · Integration
> CONSISTENT-APPROVE). **Design only — no production code shipped.** Replaces the approval
> *transport* (Telegram → Discord) while preserving the architecture, the frozen contracts, and
> every guardrail. The decision core becomes transport-agnostic via a new `lib/approval_core.py`.

## The 5 deliverables (all produced)
1. **Gateway spec** → [`docs/discord-gateway-spec.md`](./discord-gateway-spec.md)
2. **Migration blueprint** → [`docs/discord-migration-blueprint.md`](./discord-migration-blueprint.md)
3. **Updated workflow + diagram** → [`docs/discord-approval-workflow.md`](./discord-approval-workflow.md)
4. **Deployment plan** → [`docs/discord-deployment-plan.md`](./discord-deployment-plan.md)
5. **Risk assessment** → [`docs/discord-risk-assessment.md`](./discord-risk-assessment.md)

## Verified Telegram-dependency change table (the explicit ask)
Audited against the live Phase-2 code. **CORE is untouched; only the transport surface + 2 contract field names change.**

| File · symbol | Telegram (now) | Discord (target) | Action |
|---|---|---|---|
| `bin/dispatch.py` (whole file) | `TELEGRAM_API`, `sendMessage`, `inline_keyboard`+`callback_data`, `TELEGRAM_BOT_TOKEN`/`DM_CHAT_ID`, `HERMES_TG_DRYRUN`, `_deterministic_message_id` | post via discord.py to the private channel with a raw Approve/Reject components payload | **DELETE** → folded into `bin/discord_bot.py` |
| `bin/approval_handler.py` (whole file) | `_tg_call` (answerCallbackQuery/editMessageReplyMarkup), `is_authorized` (chat private/DM id), `handle_callback` (`cb.data`/`cb.from.id`/`cb.message`), `_parse_callback_data`, `handle_revise_reply` | `on_interaction` parse `custom_id`; auth = user-allowlist + channel-match; `interaction.response.defer()`; edit message to disable buttons; Revise → Modal | **DELETE** → transport bits to `bin/discord_bot.py`, decision bits to `lib/approval_core.py` |
| decision core (inside handler L201-273, L348-368) | nonce resolve → idempotency → write-ahead `append_decision` → approve-only `write_approved` → status → dequeue | **identical logic, extracted** | **EXTRACT → `lib/approval_core.py`** |
| `lib/contracts.py:244-245` (`new_draft` approval seed) | `approval{telegram_message_id, chat_id, …}` | `approval{message_id, channel_id, transport:"discord", …}` | **RENAME (seed only; `validate_draft` asserts none of these — safe)** |
| `lib/store.py` queue item (written by dispatch) | `telegram_message_id`, `chat_id` | `discord_message_id`, `channel_id` | **RENAME (store is a dumb dict bag — no code change)** |
| `decided_by` value | `"telegram:<id>"` | `"discord:<id>"` (from the verified `interaction.user.id`) | **RENAME prefix** |
| wire format | `callback_data` `apr:<nonce>:<idx>:<action>` | `custom_id` `apr:<nonce>:<idx>:<action>` (~20 chars ≤ 100 limit) | **KEEP scheme, new field** |
| `bin/research.py:11` | comment "to Telegram" | comment "to Discord" | **EDIT (cosmetic)** |
| `lib/store.py` core, `lib/contracts.py` validators, `bin/cmo.py`, `bin/research.py` logic | — | — | **KEEP — untouched (verified: zero transport literals)** |

**Two distinct `*_message_id` fields (not a conflict):** the *queue item* field = `discord_message_id`; the *draft `approval`* field = generic `message_id` — same split as Phase-1.

## New / deleted files
- **NEW** `lib/approval_core.py` — the transport-agnostic decision core (the "transport-only" guarantee made concrete).
- **NEW** `bin/discord_bot.py` — one long-running discord.py service: scans `memory/content/` for pending+undispatched drafts → posts to the private channel → handles `on_interaction`.
- **DELETE** `bin/dispatch.py`, `bin/approval_handler.py` (greenfield; nothing deployed). Optional brief parallel run = keep them refactored to delegate to `approval_core`.

## Core / transport boundary (`lib/approval_core.py`)
- **CORE (shared, reused verbatim):** `decide(nonce, variant_idx, action, *, decided_by) ` and `record_revise(...)` → resolve nonce→content_id via queue → idempotency (already-resolved no-op) → nonce verify → **write-ahead `append_decision`** → `approve` calls `store.write_approved` (SOLE writer of `approved/`) + status → `reject`/`revise` set status → dequeue.
- **TRANSPORT (Discord-specific):** post components message, parse `custom_id`, authorize (user + channel), 3-s `defer()` ack, edit message, Modal for revise.

## Updated flow (transport swapped; everything else identical)
```
research.py → cmo.py → [discord_bot.py: scan pending → post Approve/Reject to #hermes-approvals]
   (cron, unchanged)        │ user taps button
                            ▼
   on_interaction → defer(3s) → AUTH(user∈allowlist AND channel==APPROVAL_CHANNEL, fail-closed)
                            → approval_core.decide → write-ahead decisions.jsonl
                            → approve ⇒ memory/approved/<id>.md   (SOLE human gate)
                            → reject  ⇒ status rejected
                            → edit message (disable buttons)
```

## Binding corrections from the Review Board (must land before implementation)
**HIGH (convergent):**
1. **`decide()` must set `note=""`** in the decision dict — `validate_decision` requires `note` to be a string (today's handler passes `""`). Omitting it makes `append_decision` raise.
2. **`record_revise()` must also set draft `status="revise"`** (+ `approval.decided_at`/`decision`). Today that status-write lives in the callback branch, not in `handle_revise_reply`; the extracted core must include it or a revised draft stays `pending`.
3. **Channel-privacy fail-closed startup self-check** must live in `bin/discord_bot.py` (`on_ready`/pre-dispatch gate) and the cutover gate — **not only** in the risk doc. The bot must call `channel.permissions_for(guild.default_role)` on boot and **refuse to dispatch** if anyone other than {Arnav, bot} can view the approval channel. This is the one guardrail with no Telegram 1:1 (Discord channel privacy is server config, not protocol) and the only CRITICAL-rated new risk.

**MEDIUM (bake into the spec):**
- Render buttons as a **raw components dict, not `discord.ui.View`**, so `on_interaction` routes clicks with no `add_view()` registration → the "survives restart" claim is literally true.
- Provide a **synchronous, gateway-free `post_pending_once()`** seam (pure store I/O + dry-run) so the bot's posting is unit-testable without a live connection.
- **`Restart=on-failure`** + StartLimit backoff (reconcile gateway-spec's "always" to this); `Intents.none()` + `guilds` (least privilege; reconcile from `Intents.default()`).

**LOW:** doc-hygiene only (handler delete-vs-refactor wording; clarify the two `*_message_id` fields).

## Guardrails — preserved (Security: 6/6 PASS)
1. Single human gate — `approval_core.decide` approve branch is the sole `write_approved` caller; the scan loop only enqueues. ✅
2. Fail-closed allowlist — `DISCORD_ALLOWED_USERS` empty ⇒ deny all; checks `interaction.user.id`. ✅
3. Private channel — `interaction.channel_id == DISCORD_APPROVAL_CHANNEL_ID` **+** the new startup self-check (HIGH #3). ✅ (with the self-check landed)
4. Append-only `decisions.jsonl`; `decided_by="discord:<verified id>"`. ✅
5. Write-ahead decision before side effects; `defer()` is a UI ack, never a substitute for the write-ahead. ✅
6. Unguessable `secrets` nonce in `custom_id`, verified vs the queued item; idempotency via on-disk queue (survives restart). ✅

## Deployment (summary — see deployment-plan)
New `systemd --user hermes-discord-approval.service` (long-running; `Restart=on-failure`; `MemoryMax=300M` on the no-swap box; ~50–90 MB RSS). Dedicated `~/.hermes/venv-discord` with `discord.py==2.4.x` pinned (keeps the rest stdlib-only; isolates from the framework venv). One-time manual Discord setup by Arnav (app+bot, no privileged intents, least-privilege perms, private `#hermes-approvals` channel, collect guild/channel/user IDs). Env added to `~/.hermes/.env` (chmod 600): `DISCORD_BOT_TOKEN`, `DISCORD_ALLOWED_USERS`, `DISCORD_APPROVAL_CHANNEL_ID`, `DISCORD_GUILD_ID`. research/cmo cron chain unchanged. Coexistence + rollback: both transports share the idempotent store; cut over by disabling one service.

## Go / No-Go on the design
**GO for implementation (Phase 4)** — the transport swap is clean, the core is provably transport-agnostic, all 6 guardrails are preserved by reuse, and all 5 deliverables are consistent with the real code and frozen §1 contracts. Pre-conditions: land the 3 HIGH fixes above, and the **single most important pre-go security requirement — wire the fail-closed channel-privacy startup self-check into `bin/discord_bot.py` and the cutover gate.**

## Implementation order (for the eventual build — not now)
1. `lib/approval_core.py` (extract + the `note=""` / `record_revise` status fixes) + move the core tests to `tests/test_approval_core.py` (transport-free) — should pass with the existing store.
2. `bin/discord_bot.py` (raw components post + `on_interaction` + auth + the channel-privacy self-check + `post_pending_once()` seam) with a dry-run/mock mode + Discord-interaction tests.
3. Rename the contract fields + queue fields; delete the Telegram files; grep-gate (zero `telegram|callback_data|chat_id` outside docs).
4. Deploy: venv + systemd service + Discord setup + on-box validation (a test draft posts, Approve writes `approved/`, no autonomous publishing).
