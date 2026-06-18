# Discord Migration Blueprint — Phase-3 File-by-File Change Map (BLUEPRINT ONLY)

> **Specialist C — Migration Plan.** This is the precise, grounded change map for swapping the
> Telegram transport for Discord. No production code ships from this document.
>
> **Companion docs:** `docs/discord-gateway-spec.md` (Specialist A — architecture/process model)
> and `docs/discord-risk-assessment.md` / `docs/discord-deployment-plan.md`. This file is the
> *verification ledger*: every Telegram dependency that must change, traced to the live source.
>
> **Greenfield premise (load-bearing):** nothing is deployed yet. There is no production queue,
> no live drafts, no message IDs in the wild. Therefore **every contract rename is a HARD rename
> — no back-compat shim, no dual-read fallback.** This is called out explicitly wherever it
> applies. (If a parallel Telegram+Discord run is desired during cutover, see §6's opt-in note,
> but the default and recommended path is a clean swap.)

Source files audited (line numbers cited against the live tree as of this writing):
`bin/dispatch.py`, `bin/approval_handler.py`, `lib/contracts.py`, `lib/store.py`,
`tests/test_approval.py`, `tests/helpers.py`.

---

## 1. Dependency change table — every Telegram touchpoint → Discord replacement

Legend for **action**: `REPLACE` (Telegram mechanism swapped for a Discord one),
`RENAME` (same concept, new identifier), `DELETE` (no Discord equivalent / folded elsewhere),
`KEEP` (transport-agnostic, survives unchanged), `MOVE` (logic relocates into `lib/approval_core.py`).

### 1a. `bin/dispatch.py` — Telegram REST poster (ENTIRE FILE is transport)

| file:symbol | Telegram | Discord | action |
|---|---|---|---|
| `dispatch.py` (whole module) | Telegram REST poster script | folds into `bin/discord_bot.py`'s `post_pending` scan loop (gateway poster) | **DELETE** (logic re-homed) |
| `TELEGRAM_API` (L25) | `https://api.telegram.org/bot{token}/{method}` | discord.py owns the REST/gateway URL; no manual base URL | **DELETE** |
| `NONCE_BYTES = 8` (L26) | nonce entropy | identical — `secrets.token_urlsafe(8)` (11 chars, fits 100-char `custom_id`, per gateway-spec §3) | **KEEP** (move into bot) |
| `_env_truthy` (L29) | env parse helper | identical helper in bot | **KEEP** (move) |
| `is_dry_run()` (L34) | dry-run if `HERMES_TG_DRYRUN` truthy OR no `TELEGRAM_BOT_TOKEN` | dry-run if `HERMES_DISCORD_DRYRUN` truthy OR no `DISCORD_BOT_TOKEN` | **REPLACE** (env names) |
| `HERMES_TG_DRYRUN` env | TG dry-run flag | `HERMES_DISCORD_DRYRUN` | **RENAME** |
| `TELEGRAM_BOT_TOKEN` env | bot auth / dry-run trigger | `DISCORD_BOT_TOKEN` | **RENAME** |
| `TELEGRAM_DM_CHAT_ID` env (L198) | target DM chat to post to | `DISCORD_APPROVAL_CHANNEL_ID` | **RENAME** |
| `_deterministic_message_id` (L41) | stable fake `telegram_message_id` for dry-run | stable fake `discord_message_id` (identical hash logic) | **KEEP** (rename return-field only) |
| `_build_keyboard(nonce, idx)` (L50) | `inline_keyboard` rows, `callback_data="apr:<nonce>:<idx>:<action>"` | Discord components: persistent `discord.ui.View(timeout=None)` (or raw components dict), buttons carry `custom_id="apr:<nonce>:<idx>:<action>"` | **REPLACE** (same data string, different envelope) |
| `_build_message_text(variant)` (L68) | variant text + score + flagged-links warning | identical (transport-neutral text) | **KEEP** (move) |
| `_top_variant(draft)` (L89) | pick variant idx 0 | identical | **KEEP** (move) |
| `_already_dispatched(draft)` (L97) | checks `approval.dispatched_at` | identical guard | **KEEP** (move) |
| `_send_message(token, chat_id, text, reply_markup)` (L102) | urllib POST `sendMessage`, returns `message_id` | `await channel.send(content=text, view=...)`, returns `msg.id` | **REPLACE** |
| `dispatch_draft(...)` (L121) | per-draft dispatch orchestration | becomes the body of `post_pending`'s per-draft loop | **REPLACE** (re-homed into bot loop) |
| enqueue payload `chat_id` (L164) | `chat_id` queue field | `channel_id` | **RENAME** |
| enqueue payload `telegram_message_id` (L165) | `telegram_message_id` queue field | `discord_message_id` | **RENAME** |
| `_stamp(d)` writes `approval["chat_id"]` (L172) | draft stamp `chat_id` | `approval["channel_id"]` | **RENAME** |
| `_stamp(d)` writes `approval["telegram_message_id"]` (L173) | draft stamp `telegram_message_id` | `approval["message_id"]` (+ set `approval["transport"]="discord"`) | **RENAME** |
| `store.enqueue` / `store.update_draft` / `store.find_pending` / `store.iter_drafts` | store calls | identical — store is transport-agnostic | **KEEP** |
| `contracts.now_iso()` (L158) | IST timestamp | identical | **KEEP** |
| `main()` loop over `iter_drafts(status="pending")` (L207) | one-shot CLI scan | recurs as `discord.ext.tasks.loop(seconds=...)` in the bot | **REPLACE** (loop primitive) |

### 1b. `bin/approval_handler.py` — Telegram callback handler (ENTIRE FILE is transport)

| file:symbol | Telegram | Discord | action |
|---|---|---|---|
| `approval_handler.py` (whole module) | Telegram callback handler script | replaced by `bin/discord_bot.py`'s `on_interaction` | **DELETE** (decision logic → `approval_core`) |
| `TELEGRAM_API` (L29) | TG REST base | discord.py owns it | **DELETE** |
| `ACTION_MAP` (L31) `{a,r,v}` | action short-code map | identical (reused verbatim, lives in bot + core) | **KEEP** (move) |
| `_env_truthy` / `_is_dry_run` (L38/L43) | TG dry-run | Discord dry-run (env-renamed, §1a) | **REPLACE** |
| `_allowed_users()` (L49) | parse `TELEGRAM_ALLOWED_USERS`, empty ⇒ deny-all | parse `DISCORD_ALLOWED_USERS`, empty ⇒ deny-all | **REPLACE** (env name) |
| `TELEGRAM_ALLOWED_USERS` env | allow-list | `DISCORD_ALLOWED_USERS` | **RENAME** |
| `is_authorized(user_id, chat)` (L59) | allow-list + (chat private OR `chat.id == TELEGRAM_DM_CHAT_ID`) | allow-list + (`channel_id == DISCORD_APPROVAL_CHANNEL_ID`); signature → `is_authorized(user_id, channel_id)` | **REPLACE** (channel-locked, not DM-type) |
| `_tg_call(method, payload)` (L85) | urllib `answerCallbackQuery` / `editMessageReplyMarkup` | discord.py `interaction.response.defer()` + `interaction.message.edit(view=disabled)` | **REPLACE** |
| `_ack_callback(cb, text)` (L103) | answer + clear inline keyboard | `defer()` (≤3s) + edit message to disabled buttons + optional ephemeral `followup` | **REPLACE** |
| `_parse_callback_data(data)` (L128) | parses `cb.data` → `(nonce, idx, action)` | parses `interaction.data["custom_id"]` → `(nonce, idx, action)` — **same grammar `apr:<nonce>:<idx>:<action>`** | **REPLACE** (input field) / grammar **KEEP** |
| `_resolve_by_nonce(nonce)` (L148) | queue scan by nonce | identical — transport-free | **MOVE → `approval_core`** |
| `_draft_variant_text` / `_draft_variant_score` (L157/L164) | draft reads | identical — transport-free | **MOVE → `approval_core`** |
| `handle_callback(cb)` (L175) | full decision pipeline keyed off a TG callback dict | split: **auth+parse+ack stay in bot**; **decision pipeline → `approval_core.decide`** | **MOVE (core) + REPLACE (transport shell)** |
| auth read `cb.from.id` / `cb.message.chat` (L185-188) | callback dict fields | `interaction.user.id` / `interaction.channel_id` | **REPLACE** |
| nonce-verify + idempotency block (L201-216) | `_resolve_by_nonce` → `find_pending` → bad-nonce / already-resolved | identical logic | **MOVE → `approval_core.decide`** |
| `decided_by = "telegram:{user_id}"` (L218, L345) | ledger attribution prefix | `decided_by = "discord:{user_id}"` | **RENAME (prefix)** — set in transport, passed into core |
| `store.append_decision` write-ahead (L222) | decision before side effects | identical | **MOVE → core** (unchanged) |
| approve branch: `approved_exists` → `write_approved` → `update_draft("approved")` → `dequeue` (L233-258) | core decision | identical | **MOVE → core** |
| reject branch: `update_draft("rejected")` → `dequeue` (L260-273) | core decision | identical | **MOVE → core** |
| revise branch: `_draft_status` + `_set_revise_queue` + `_mutate_queue_item` (L275-310) | sets `awaiting_revise_note`, awaits reply | **REPLACE** with native Discord Modal (`send_modal`) → `approval_core.record_revise`; the `awaiting_revise_note` queue state is **DELETED** | **REPLACE** (mechanism) — see §3 |
| `handle_revise_reply(msg)` (L317) | scans `awaiting_revise_note`, grabs next message text, `record_revise`-equivalent | the *recording* logic → `approval_core.record_revise(nonce, idx, note, decided_by=...)`; the *reply-scan* transport is **DELETED** (Modal supplies the note atomically) | **MOVE (recording) + DELETE (reply-scan)** |
| `main(argv)` / `_read_cb_arg` `--cb` CLI (L377-403) | test CLI driving `handle_callback` from JSON | `bin/discord_bot.py --interaction '<json>'` building a parsed tuple → `approval_core.decide` | **REPLACE** |

### 1c. `lib/contracts.py`

| file:symbol | Telegram | Discord | action |
|---|---|---|---|
| `new_draft(...)` approval seed `telegram_message_id` (L244) | seed key | `message_id` (+ new `transport: None`) | **RENAME** (additive — see §2) |
| `new_draft(...)` approval seed `chat_id` (L245) | seed key | `channel_id` | **RENAME** |
| `validate_draft` (L183) | only asserts `approval` is a dict (L205) — does **NOT** assert message/chat keys | unchanged | **KEEP** (verified safe — see §2) |
| `validate_decision` (L210) | asserts `decided_by` is non-empty string; agnostic to prefix value | unchanged (`discord:` prefix still a valid non-empty string) | **KEEP** |
| `ACTIONS` / `STATUSES` / `slugify` / `now_iso` / URL helpers | transport-neutral | unchanged | **KEEP** |

### 1d. `lib/store.py` — **fully transport-agnostic, KEEP ENTIRELY**

| file:symbol | status |
|---|---|
| `atomic_write_json`, `read_json`, `append_jsonl`, `read_jsonl` | **KEEP** |
| `enqueue` / `dequeue` / `find_pending` / `read_queue` / `_write_queue` | **KEEP** (queue stores whatever keys the poster writes; no Telegram coupling in store) |
| `append_decision` / `decisions()` | **KEEP** |
| `save_draft` / `load_draft` / `update_draft` / `iter_drafts` | **KEEP** |
| `write_approved` / `approved_exists` / `approved_path` | **KEEP** |
| `ensure_tree` + memory-tree helpers | **KEEP** |

> Note: store has **zero** `telegram`/`chat_id`/`message_id` string literals — the queue is a
> dumb bag of dicts. The renamed keys flow through it untouched. This is the core reason the
> migration is "transport-only."

### 1e. `cmo.py`, `research.py` — **UNTOUCHED** (no transport coupling).

---

## 2. Contract field changes (greenfield hard-rename — NO shim)

The `approval` block in `lib/contracts.new_draft` (L243-252) and every writer/reader of those
fields move to a generic, transport-neutral vocabulary:

| Old (Telegram) | New (generic) | Where set / read |
|---|---|---|
| `approval.telegram_message_id` | `approval.message_id` | `new_draft` seed; `dispatch._stamp` (L173) → bot stamp |
| `approval.chat_id` | `approval.channel_id` | `new_draft` seed; `dispatch._stamp` (L172) → bot stamp |
| *(none)* | `approval.transport` (**NEW**, `"discord"`) | set by the poster; disambiguates if ever parallel-run |
| queue item `telegram_message_id` | queue item `discord_message_id` | `dispatch.enqueue` (L165) → bot enqueue |
| queue item `chat_id` | queue item `channel_id` | `dispatch.enqueue` (L164) → bot enqueue |
| `decided_by` prefix `telegram:<id>` | `decided_by` prefix `discord:<id>` | core call site (passed in by transport) |
| button `callback_data` (string `apr:<nonce>:<idx>:<action>`) | button `custom_id` (**same string** `apr:<nonce>:<idx>:<action>`) | `_build_keyboard` → `approval_view` |

**Greenfield ⇒ hard rename, no back-compat:** because no queue/drafts exist on disk yet, there
is **no migration of existing data** and **no dual-key read fallback**. We delete the old field
names outright and write only the new ones. (A back-compat shim — read both names, prefer new —
is the move *only if* something were already deployed; it is **not** warranted here and would be
dead code from birth, violating YAGNI.)

**`validate_draft` needs NO change — verified:** `validate_draft` (L183-207) checks
`draft.id`, `created_at`, `source_research_ids`, `persona`, `platform`, `status`, `variants`,
and that `approval` **is a dict** (L205). It never asserts `telegram_message_id`, `chat_id`,
`message_id`, or `channel_id`. So renaming the seed keys in `new_draft` is a pure additive
change that cannot fail validation. The only file edit in `lib/` is the seed dict in
`new_draft` (L244-245, + add `transport`). `validate_decision` (L210-227) is likewise agnostic
to the `decided_by` prefix value.

---

## 3. The `lib/approval_core.py` extraction — exact CORE vs TRANSPORT boundary

This extraction is the concrete "transport-only" guarantee: the security-critical decision
pipeline becomes a pure module that **both** a (retired) Telegram handler and the Discord bot
could call with identical inputs. It takes **already-parsed, already-authorized** inputs and
returns a result dict; it imports **no** discord.py / urllib / network and touches **no**
interaction object.

### CORE (pure — moves into `lib/approval_core.py`)

The pipeline, in order, lifted from `approval_handler.handle_callback`:

1. **nonce resolve** — `_resolve_by_nonce(nonce)` over `store.read_queue()`
   (from `approval_handler.py` L148-154) → queue item → `content_id`.
2. **idempotency / already-resolved** — no queue item for nonce ⇒ `{"ok": True, "reason":
   "already_resolved"}` (L201-205); re-check `store.find_pending(content_id)` is still pending
   (L211-216).
3. **nonce-verify guard** — `item.nonce == nonce` else `{"ok": False, "reason": "bad_nonce"}`
   (L208-209, L215-216).
4. **WRITE-AHEAD** — `store.append_decision({...decided_by, action, variant_idx, note...})`
   **before any other write** (L222-229). `decided_by` and `note` arrive as parameters.
5. **approve branch** — `_draft_variant_text` / `_draft_variant_score` (L157-168), build
   frontmatter, `if not store.approved_exists: store.write_approved(...)` (L243-244),
   `store.update_draft(status="approved")` (L246-255), `store.dequeue` (L256). **Only this
   branch writes `approved/`.**
6. **reject branch** — `store.update_draft(status="rejected")` + `store.dequeue` (L260-271).
7. **revise recording** — the body of `handle_revise_reply` (L348-368): write-ahead revise
   decision carrying `note`, `update_draft` bumping `revise_count` + setting `revise_note`,
   `dequeue`. Exposed as `record_revise`.

### TRANSPORT (stays out of core — lives in `bin/discord_bot.py`)

- **post message** — `channel.send(view=...)` / dry-run print (was `_send_message` / `_build_keyboard`).
- **parse interaction** — `interaction.data["custom_id"]` → `(nonce, idx, action)` (was `_parse_callback_data` on `cb.data`).
- **auth user + channel** — `is_authorized(user_id, channel_id)`, fail-closed, **before** core is ever called (was `is_authorized(user_id, chat)`).
- **ack** — `interaction.response.defer()` ≤3s, `message.edit(view=disabled)`, optional ephemeral followup (was `_ack_callback`).
- **revise UI** — `send_modal(ReviseModal)` then on submit call `record_revise` (replaces the `awaiting_revise_note` reply-scan entirely).

### Function signatures (the public surface — aligned with gateway-spec §6.1)

```text
# lib/approval_core.py  (BLUEPRINT — signatures only)

def decide(nonce: str, variant_idx: int, action: str, *, decided_by: str) -> dict:
    """Pure decision. action ∈ {"approve","reject"}. No network, no transport objects.
    Returns the same result-dict shapes handle_callback returns today:
      {"ok": True,  "action": "approve"|"reject", "content_id": ...}
      {"ok": True,  "reason": "already_resolved"}
      {"ok": False, "reason": "bad_nonce"}
    Order: resolve-nonce → idempotency → nonce-verify → append_decision (write-ahead)
           → approve(approved_exists→write_approved→update_draft→dequeue) | reject(update_draft→dequeue).
    """

def record_revise(nonce: str, variant_idx: int, note: str, *, decided_by: str) -> dict:
    """Write-ahead revise decision carrying `note`, bump approval.revise_count,
    set approval.revise_note, dequeue. Returns {"ok": True, "action": "revise",
    "content_id": ..., "note": note}. No transport state (no awaiting_revise_note flag)."""
```

**Lines that physically move out of `approval_handler.py` into `approval_core.py`:**
L31 (`ACTION_MAP`, shared), L148-168 (`_resolve_by_nonce`, `_draft_variant_text`,
`_draft_variant_score`), L200-284 (the resolve→verify→write-ahead→approve/reject body of
`handle_callback`, minus the auth at L191 and parse at L195 which stay in transport),
L287-310 (`_draft_status`, `_mutate_queue_item` — only the parts `record_revise` needs),
and L332-368 (the recording half of `handle_revise_reply`).

**Lines that do NOT move (transport, rewritten for Discord):** L29 `TELEGRAM_API`, L38-122
(env/dry-run/`_tg_call`/`_ack_callback`), L128-145 (`_parse_callback_data` — re-pointed at
`custom_id`), L59-78 (`is_authorized` — re-pointed at `channel_id`), L184-198 (the callback-dict
field reads), L218/L345 (`decided_by` prefix string), L377-403 (`--cb` CLI).

**Security invariants preserved (identical to Phase-2 — `approval_handler.py` L8-15):**
fail-closed auth (enforced in transport before core), write-ahead decision, approve-only
`write_approved`, idempotent double-tap, token never logged.

---

## 4. File plan

| File | Status | Role |
|---|---|---|
| `lib/approval_core.py` | **NEW** | Pure, transport-free decision core: `decide`, `record_revise` (+ moved helpers). |
| `bin/discord_bot.py` | **NEW** | Standalone Discord bot: `post_pending` scan loop + `on_interaction` + optional Revise modal + dry-run + `--interaction` test CLI. |
| `lib/contracts.py` | **EDIT (one seed dict)** | `new_draft` approval seed: `telegram_message_id`→`message_id`, `chat_id`→`channel_id`, add `transport`. Validators untouched. |
| `lib/store.py` | **UNCHANGED** | Transport-agnostic; renamed keys pass through. |
| `cmo.py`, `research.py` | **UNCHANGED** | No transport coupling. |
| `bin/dispatch.py` | **DELETE (recommended)** | Telegram REST poster; responsibility folds into `discord_bot.post_pending`. |
| `bin/approval_handler.py` | **DELETE (recommended)** | Telegram handler; decision logic extracted to `approval_core`, transport replaced by `discord_bot`. |

### Delete vs keep-disabled

**Recommendation: DELETE both `bin/dispatch.py` and `bin/approval_handler.py`** as part of the
cutover. Rationale (greenfield):

- Nothing is deployed, so there is no "fall back to Telegram in prod" scenario to protect.
- Keeping them means maintaining two transports against one store and two test suites — pure
  carrying cost for code that will never run in production (YAGNI / DRY).
- The *valuable* part of `approval_handler.py` (the decision pipeline) is preserved by moving it
  into `approval_core.py`, not by keeping the file. Git history retains the original if ever needed.

**Optional brief parallel run (only if explicitly desired):** if you want a short overlap to
sanity-check Discord against the same store before deleting Telegram, you *may* temporarily keep
both files, refactored to delegate their decision logic to `approval_core` (so they cannot
diverge). The queue + `dispatched_at` guard + `approved_exists` guard prevent double-post /
double-approve across the two transports, and `approval.transport` disambiguates which posted
each draft. Delete them once Discord is validated. This is an opt-in, not the default.

---

## 5. Test migration

The Phase-2 approval tests assume a Telegram callback dict and drive
`bin/approval_handler.handle_callback`. They split cleanly along the new CORE/TRANSPORT seam.

### What stays vs what is rewritten

| Test artifact | Today | Phase-3 action |
|---|---|---|
| `tests/test_approval.py` — `ApprovalApproveTest`, `ApprovalRejectTest`, `IdempotencyTest` (decision behavior) | call `handle_callback` with TG dict; assert `approved/<id>.md`, decisions.jsonl, draft status, idempotency | **MOVE to a new `tests/test_approval_core.py`** that calls `approval_core.decide(nonce, idx, action, decided_by="discord:42")` directly — **transport-free, the strongest tests, they survive forever.** Assertions on artifact/ledger/status/idempotency are unchanged. |
| `tests/test_approval.py` — `UnauthorizedTest` (`test_unauthorized_user_denied`, `test_group_chat_denied_even_for_allowed_user`) | TG auth: wrong user, group-chat-vs-DM | **REWRITE as `tests/test_discord_auth.py`**: wrong user denied; **wrong-channel denied** replaces the "group chat" case (Discord is channel-locked, not DM-type-locked). Still asserts: no `approved/` file, no decision line, draft not approved. |
| `tests/test_approval.py` — `_ApprovalBase._dispatch()` (drives `bin/dispatch.py` dry-run, reads nonce from queue) | runs `dispatch` stage | **REWRITE** to drive `bin/discord_bot.py` in dry-run posting mode (or a thin post-once entrypoint) and read the nonce from the queue exactly as today. The "stamp `dispatched_at` + nonce, enqueue" contract assertions are unchanged. |
| transport-shell test for `on_interaction` (NEW) | n/a (Telegram tested via `handle_callback`) | **NEW `tests/test_discord_bot.py`**: feed a fake **interaction dict** (`custom_id`, `user.id`, `channel_id`) through the bot's parse+auth path (or the `--interaction` CLI), assert it reaches `decide` and acks; assert dry-run skips network. |

### `tests/helpers.py` changes

| helper | Today | Phase-3 action |
|---|---|---|
| `callback(nonce, variant, action, ...)` (L333) builds a **Telegram** dict (`data`, `from.id`, `message.chat.{type,id}`, `message_id`) | TG-shaped | **REWRITE / add `interaction(...)`** builder producing a Discord-shaped dict: `{"data": {"custom_id": "apr:<nonce>:<v>:<a>"}, "user": {"id": ...}, "channel_id": ..., "message": {"id": ...}}`. Keep the same `nonce/variant/action/user_id` parameter surface so test bodies barely change. |
| `_set_env` (L49) sets `TELEGRAM_*` + `HERMES_TG_DRYRUN` | TG env | **REPLACE** with `DISCORD_ALLOWED_USERS=42`, `DISCORD_APPROVAL_CHANNEL_ID=42`, `HERMES_DISCORD_DRYRUN=1` (drop `TELEGRAM_DM_CHAT_ID`; the channel id is the auth anchor). |
| `HermesTestCase._saved_env` key list (L117) | restores `TELEGRAM_*` keys | **REPLACE** the saved-env key set with the `DISCORD_*` names. |
| `make_pending_draft` approval seed `telegram_message_id`/`chat_id` (L302-311) | TG seed | **RENAME** to `message_id`/`channel_id` (+ `transport: None`) to match `new_draft`. |
| `run_bin("dispatch")` usage | TG poster | point at `discord_bot` post entrypoint (or keep `run_bin` generic; just change the module arg). |

**Net test story:** the *core behavioral tests get stronger* (now transport-free, no fake
network dict at all) and the *transport tests get smaller* (auth + parse + ack only). No
assertion about what "approved" means changes — that logic moved intact into `approval_core`.

---

## 6. Cutover sequence (greenfield clean swap)

Ordered, each step independently verifiable. Default path = clean swap (no parallel run).

1. **Extract the core (no behavior change).** Create `lib/approval_core.py` with `decide` +
   `record_revise` by moving the pipeline out of `approval_handler.py` (per §3). Temporarily,
   `handle_callback` could delegate to it to prove equivalence against the *existing* Telegram
   tests (all current `test_approval.py` cases still green). This is the only step where both
   transports momentarily coexist, and only to prove the extraction is faithful.
2. **Rename the contract fields (hard rename).** Edit `lib/contracts.new_draft` seed
   (`message_id`/`channel_id`/`transport`); update `tests/helpers.make_pending_draft` to match.
   Run `python -m lib.contracts` smoke + `validate_draft` — must stay green (verified no
   validator asserts these keys).
3. **Build the Discord poster.** Implement `bin/discord_bot.py` `post_pending` (scan
   `iter_drafts(status="pending")`, skip dispatched/queued, `secrets.token_urlsafe(8)` nonce,
   `_build_message_text`, post or dry-run, `enqueue` with `channel_id`/`discord_message_id`,
   stamp draft). Verify in dry-run: a pending draft → stamped + enqueued with a nonce.
4. **Build the Discord handler.** Implement `on_interaction`: type-check → `is_authorized(user,
   channel)` fail-closed → parse `custom_id` → `defer()` → `approval_core.decide(...,
   decided_by="discord:<id>")` → disable buttons → ephemeral followup. Add `--interaction` CLI.
5. **Migrate tests.** Create `tests/test_approval_core.py` (moved behavioral tests),
   `tests/test_discord_auth.py` + `tests/test_discord_bot.py` (rewritten transport tests),
   update `tests/helpers.py` (`interaction()` builder + `DISCORD_*` env). Run full suite green.
6. **Delete Telegram.** Remove `bin/dispatch.py` and `bin/approval_handler.py`, and delete the
   old `tests/test_approval.py` (superseded by the split). Grep the tree for `telegram`,
   `chat_id`, `callback_data`, `TELEGRAM_`, `HERMES_TG_DRYRUN` — must return **zero** hits
   outside docs/history. This grep is the migration's completion gate.
7. **Wire the service.** Add the systemd/pm2 unit for `python -m bin.discord_bot`
   (`Restart=always`, env from box `.env`); confirm gateway connect + scan loop + a live
   approve round-trip in the private channel.
8. **Update `CONTEXT.md` / `MEMORY.md`** to record the transport swap (mandated by project rules).

> **Revise (optional, post-MVP):** ship Approve/Reject first. `record_revise` lives in
> `approval_core` from step 1 so the only later addition is one `send_modal` branch in
> `on_interaction` plus a `ReviseModal.on_submit` calling `record_revise` — no core or contract
> change needed.

---

## 7. One-screen summary

- **Change-table headline:** `bin/dispatch.py` and `bin/approval_handler.py` are **100%
  transport and are deleted**; their *decision* logic is preserved by extraction into
  `lib/approval_core.py`. `lib/store.py` and `cmo.py`/`research.py` are **untouched**;
  `lib/contracts.py` gets a **single-dict edit** (the `new_draft` approval seed). Verified:
  `validate_draft`/`validate_decision` need **no** change.
- **Core/transport boundary:** CORE (`approval_core.decide`/`record_revise`) = nonce-resolve →
  idempotency → nonce-verify → write-ahead `append_decision` → approve(`write_approved`→status→
  dequeue) | reject(status→dequeue) | revise(record→dequeue) — no network, no interaction object.
  TRANSPORT (`bin/discord_bot.py`) = post message, parse `custom_id`, auth(user+channel), 3-s
  ack/defer, disable buttons, optional modal.
- **Delete vs keep:** **DELETE** both Telegram files (greenfield — nothing deployed). Optional
  brief parallel run only if explicitly wanted, both delegating to `approval_core` so they can't
  diverge; delete after Discord is validated.
- **Contract renames (hard, no shim):** `approval.telegram_message_id`→`message_id`;
  `approval.chat_id`→`channel_id`; **new** `approval.transport`; queue `telegram_message_id`→
  `discord_message_id`, `chat_id`→`channel_id`; `decided_by` prefix `telegram:`→`discord:`;
  button `callback_data`→`custom_id` (**same** `apr:<nonce>:<idx>:<action>` string). Queue
  mechanics and `decisions.jsonl` schema are otherwise unchanged.
- **Tests that change:** behavioral tests (`ApprovalApproveTest`/`ApprovalRejectTest`/
  `IdempotencyTest`) **move** to `tests/test_approval_core.py` (transport-free, call `decide`
  directly); `UnauthorizedTest` is **rewritten** to `tests/test_discord_auth.py` (wrong-user +
  **wrong-channel** instead of group-chat); **new** `tests/test_discord_bot.py` covers
  `on_interaction` parse/auth/ack; `tests/helpers.py` gains an `interaction()` builder and
  `DISCORD_*` env (replacing `callback()` + `TELEGRAM_*`); old `tests/test_approval.py` is
  deleted.
