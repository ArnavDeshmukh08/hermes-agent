# Discord Gateway Spec — Phase-3 Transport Swap (BLUEPRINT ONLY)

> **Specialist A — Discord Architecture.** Design only. No production code ships from this
> document — pseudocode and skeletons only.
>
> **Goal:** Replace the Telegram transport with Discord while reusing the verified
> transport-agnostic core (`lib/store.py`, `lib/contracts.py`, and the decision logic).
> The decision logic moves into a new `lib/approval_core.py` so both Telegram and Discord
> handlers call the same function — this is the cleanest "transport-only" change.
>
> Status: Phase-2 MVP is built + tested (23/23 passing). Telegram-specific code =
> `bin/dispatch.py`, `bin/approval_handler.py`, and 2 contract fields
> (`approval.telegram_message_id`, `approval.chat_id`).

---

## 0. The seam (what we keep, what we replace)

| Layer | File(s) | Phase-3 action |
|---|---|---|
| Atomic store, queue, `decisions.jsonl`, `write_approved` | `lib/store.py` | **REUSE unchanged** |
| Validators, ID/URL helpers, `new_draft` | `lib/contracts.py` | **REUSE** (one additive field set, §5.3) |
| Decision logic (nonce verify → write-ahead → approve-only `write_approved`) | currently inside `bin/approval_handler.py` | **EXTRACT** to `lib/approval_core.py` (§6) |
| Transport: post draft + buttons | `bin/dispatch.py` | **REPLACE** with Discord poster |
| Transport: receive interaction + ack | `bin/approval_handler.py` | **REPLACE** with `bin/discord_bot.py` |
| Research / CMO | `research.py`, `cmo.py` | **UNTOUCHED** |

The queue (`memory/approvals/queue.json`) remains the **single source of truth** for any
pending approval. Drafts in `memory/content/` carry status + an `approval` block. Discord
message IDs are transport metadata only — never authoritative for whether something is
decided.

---

## 1. Process model

### Decision: STANDALONE `discord.py` bot as a long-running service.

A dedicated bot process (`bin/discord_bot.py`) run under systemd/pm2, **separate** from the
existing hermes-agent gateway. We do **not** wire approvals into hermes-agent's built-in
`discord:` platform block / `hermes-discord` toolset.

**Justification:**

1. **Custom interaction surface.** Approvals need persistent message components (Approve /
   Reject buttons, optional Revise modal) with a precise `custom_id` scheme and a 3-second
   ack contract. The framework's `hermes-discord` toolset is built for conversational
   message send/receive, not for owning a button/modal interaction lifecycle. Driving
   `discord.ui` / raw `on_interaction` through a generic toolset is more friction than a
   thin dedicated bot.
2. **Mirrors the proven shape.** Phase-2 already runs transport as standalone scripts
   (`dispatch.py` poster + `approval_handler.py` handler). A standalone Discord bot is the
   one-to-one structural analogue — minimal conceptual change, same operational model
   (systemd unit, auto-restart).
3. **Blast-radius isolation.** The approval bot can crash/restart/redeploy without touching
   the assistant's main chat gateway, and vice versa. The queue guarantees no pending
   approval is lost across a restart (§7).
4. **Single-writer discipline.** `lib/store.py` assumes single-writer-per-file. One
   dedicated bot process keeps the writer story simple and auditable.

**Operational shape:** one systemd/pm2 unit running `python -m bin.discord_bot`, env-loaded
from the box `.env`, `Restart=always`. discord.py owns the gateway WebSocket + auto-reconnect.

---

## 2. Posting drafts (pending draft → Discord message with buttons)

Two viable patterns were on the table:

- **(a) Single bot process** that scans `memory/content/` for pending+undispatched drafts on
  a loop and posts them itself.
- **(b) Keep a separate poster** (`dispatch.py` refactored to post via Discord REST with
  `components`) and the bot only handles interactions.

### Decision: Pattern (a) — the single bot owns both posting and interaction handling.

**Justification:**

1. **The bot must run regardless.** Receiving Discord interactions *requires* a live gateway
   connection. So a process is always up. Splitting posting into a second process buys
   nothing and adds a moving part.
2. **`discord.py` already gives us a clean loop primitive** (`discord.ext.tasks.loop`) and a
   fully-formed channel object to call `channel.send(view=...)` on. Posting through the same
   library that handles interactions keeps one auth path, one rate-limiter, one set of
   intents.
3. **Idempotency is already in the store.** The poster's "have I sent this?" check is exactly
   what `dispatch.py` does today: skip if `approval.dispatched_at` is set OR
   `store.find_pending(content_id)` returns an item. We reuse that guard verbatim.

So `bin/dispatch.py` (Telegram REST poster) is **retired** for the Discord path; its
posting responsibility folds into the bot's background scan loop. (We may keep a thin
`bin/dispatch.py` alive only if Telegram is run in parallel during migration — see §6.4.)

### Posting flow (per scan tick)

```text
for draft in store.iter_drafts(status="pending"):
    if approval.dispatched_at set:            continue   # already posted
    if store.find_pending(draft.id) is not None: continue # already queued
    variant   = draft.variants[0]                         # top variant, idx 0
    nonce     = secrets.token_urlsafe(8)
    text      = build_message_text(variant)               # reuse dispatch.py formatting
    view      = ApprovalView(nonce, idx=0)  # or raw component dict (see §3)
    msg       = await channel.send(content=text, view=view)
    store.enqueue({
        content_id: draft.id, variant_idx: 0, nonce,
        state: "sent",
        channel_id: DISCORD_APPROVAL_CHANNEL_ID,
        discord_message_id: msg.id,
        sent_at: now_iso(),
    })
    store.update_draft(draft.id, stamp_approval(nonce, channel_id, msg.id, sent_at))
```

Notes:
- The message-text builder (`_build_message_text`) and top-variant pick (`_top_variant`) are
  transport-neutral and lifted directly from today's `dispatch.py`.
- `store.enqueue` / `store.update_draft` / `store.find_pending` are unchanged.
- Dry-run mode (§5.4) skips `channel.send`, fabricates a deterministic `discord_message_id`,
  but still enqueues + stamps — exactly the Phase-2 dry-run contract.

---

## 3. Button / interaction handling

Two implementation styles:

- **`discord.ui.View` + `Button`** (persistent, `timeout=None`): declarative components,
  per-button callbacks.
- **Raw `on_interaction`** parsing `interaction.data["custom_id"]`: one handler, manual parse.

### Recommendation: raw `on_interaction` parse (best mirror of Telegram `handle_callback`).

**Justification:** Phase-2's `handle_callback` already centralizes everything on a single
parsed `custom_id` string (`_parse_callback_data` → `apr:<nonce>:<idx>:<action>`). Raw
`on_interaction` is the one-to-one Discord analogue: one entry point, parse `custom_id`, hand
to the shared decision core. It also avoids View re-registration on restart (§7) — persistence
is automatic because we never rely on in-memory View state.

We still *render* buttons. The cleanest middle path:
- **Render** with a lightweight persistent `discord.ui.View(timeout=None)` (or a raw
  components payload) just to draw the buttons.
- **Handle** in a global `on_interaction` that parses `custom_id` — not in per-button View
  callbacks. This way the handler survives restarts with zero View registration and matches
  the Telegram parse path exactly.

### `custom_id` scheme

Reuse the Telegram scheme verbatim:

```text
apr:<nonce>:<variant_idx>:<action>      action ∈ { a (approve), r (reject) }   (v = revise, optional)
```

**Length check against Discord's 100-char `custom_id` limit:**

```text
"apr:" (4) + nonce + ":" (1) + idx + ":" (1) + action (1)
nonce = secrets.token_urlsafe(8)  → 11 chars (8 bytes base64url, no padding)
idx   ≤ 2 chars in practice (we only ever post variant 0; allow up to 99)
total ≈ 4 + 11 + 1 + 2 + 1 + 1 = 20 chars   ✅  well under 100
```

Confirmed: the scheme fits with ~80 chars of headroom. `ACTION_MAP = {a: approve, r: reject,
v: revise}` is reused unchanged from `approval_handler.py`.

### 3-second interaction ack + disable buttons

Discord requires an interaction response within **3 seconds** or it errors out. Decision-core
writes (queue read, JSONL append, atomic file write) are fast but we must not gamble on it.

```text
on_interaction(interaction):
    if interaction.type != component:              return
    if not is_authorized(interaction.user.id,
                         interaction.channel_id):  # fail-closed (§5.2)
        await interaction.response.send_message("Not authorized", ephemeral=True); return

    await interaction.response.defer()             # <-- ACK within 3s, buys 15 min

    parsed = parse_custom_id(interaction.data["custom_id"])
    result = approval_core.decide(parsed.nonce, parsed.idx, parsed.action,
                                  decided_by=f"discord:{interaction.user.id}")

    # edit the original message to disable buttons so it can't be re-tapped
    await interaction.message.edit(view=disabled_view(result))   # buttons → disabled
    # optional: ephemeral confirmation toast
    await interaction.followup.send(ack_text(result), ephemeral=True)
```

- `interaction.response.defer()` is the Discord equivalent of Telegram's
  `answerCallbackQuery` — it acknowledges the tap immediately.
- `interaction.message.edit(view=...)` with all buttons `disabled=True` mirrors the Telegram
  `editMessageReplyMarkup` "clear keyboard" step, preventing double-decisions at the UI layer.
  (The decision core is *also* idempotent — §6 — so a race can never double-write.)

---

## 4. Revise (OPTIONAL for MVP)

Required buttons are **Approve / Reject only**. Revise is designed but marked **optional** and
deferred past MVP.

### Design: Revise as a native Discord Modal.

Telegram captured revise notes via a fragile "reply-intercept" (`handle_revise_reply` scans
for an `awaiting_revise_note` queue item and grabs the next message text). Discord offers a
strictly better native primitive: a **Modal** (`discord.ui.Modal` + `TextInput`) — a real
free-text dialog tied directly to the interaction.

```text
on_interaction(... action == revise ...):
    # do NOT defer; a modal IS the response
    await interaction.response.send_modal(ReviseModal(nonce, idx))

ReviseModal.on_submit(modal_interaction):
    note = modal_interaction.text_input.value
    approval_core.record_revise(nonce, idx, note,
                                decided_by=f"discord:{user.id}")
    await modal_interaction.response.edit_message(view=disabled_view())
```

**Why this is cleaner than Telegram's path:**
- No stateful `awaiting_revise_note` queue flag, no reply-message scanning, no "most recent
  wins" ambiguity.
- The note is captured atomically in one interaction, bound to the exact nonce/draft.
- `approval_core.record_revise` reuses the same write-ahead `append_decision` +
  `update_draft` (bump `revise_count`, set `revise_note`) logic as today.

**MVP scope:** ship Approve/Reject. Revise modal is a fast-follow; `record_revise` lives in
`approval_core` from day one so the bot only needs to add one `send_modal` branch later.

---

## 5. Env / config

### 5.1 Environment variables (box `.env`)

| Var | Purpose |
|---|---|
| `DISCORD_BOT_TOKEN` | Bot token. Never logged. Absence ⇒ dry-run (mirrors `TELEGRAM_BOT_TOKEN`). |
| `DISCORD_ALLOWED_USERS` | Comma-separated Discord user IDs. **Empty ⇒ deny-all (fail-closed).** |
| `DISCORD_APPROVAL_CHANNEL_ID` | The private approval channel ID drafts are posted to. |
| `DISCORD_GUILD_ID` | Guild (server) the approval channel lives in (scoping / validation). |
| `HERMES_DISCORD_DRYRUN` | Truthy ⇒ force dry-run regardless of token (mirrors `HERMES_TG_DRYRUN`). |

### 5.2 Auth (fail-closed, mirrors `is_authorized`)

```text
is_authorized(user_id, channel_id):
    allowed = parse_csv(DISCORD_ALLOWED_USERS)
    if not allowed:                      return False   # fail-closed
    if str(user_id) not in allowed:      return False
    if str(channel_id) != DISCORD_APPROVAL_CHANNEL_ID: return False  # channel-locked
    return True
```

This is the Discord analogue of Telegram's allow-list + DM-only check: allow-listed user
**and** the interaction must come from the configured private approval channel.

### 5.3 Intents

```text
intents = discord.Intents.default()
intents.guilds = True            # see the approval channel
# interactions (button taps / modal submits) arrive over the gateway by default
# message_content = False        # NOT needed — buttons/modals don't read message text
```

**`message_content` is explicitly NOT required** (and not requested) — approvals run entirely
on component interactions and modals, never on reading user message bodies. This keeps the bot
out of Discord's privileged-intent review.

### 5.4 Dry-run / mock mode (tests, no live Discord)

Same contract as Phase-2: dry-run when `HERMES_DISCORD_DRYRUN` is truthy **OR**
`DISCORD_BOT_TOKEN` is empty.

- **Poster dry-run:** skip `channel.send`; print the would-send payload (content + components +
  a deterministic fake `discord_message_id`); still `enqueue` + stamp the draft.
- **Handler tests:** `approval_core.decide(...)` and `record_revise(...)` are pure (no Discord
  objects, no network) and are unit-tested directly — exactly how `handle_callback` is tested
  today. Tests build a fake parsed-interaction tuple `(nonce, idx, action, user_id)` and call
  the core. No discord.py import needed in the core test path.
- A thin `bin/discord_bot.py --interaction '<json>'` CLI (mirroring `approval_handler.py
  --cb`) lets us drive a decision end-to-end without a gateway connection.

### 5.5 Contract fields (`lib/contracts.new_draft`)

Today the `approval` block hardcodes `telegram_message_id` + `chat_id`. To stay transport-
agnostic, **rename/generalize** (additive, backward-compatible):

```text
approval:
    message_id   : None     # was telegram_message_id  (generic transport message id)
    channel_id   : None     # was chat_id              (generic transport channel/chat id)
    transport    : None     # "telegram" | "discord"   (NEW — which transport posted it)
    nonce, dispatched_at, decided_at, decision, revise_count, revise_note   (unchanged)
```

Validators (`validate_draft`) don't assert these specific keys, so this is a safe additive
change. If parallel-running both transports during migration, `transport` disambiguates.
(Minimal alternative: keep `telegram_*` names and add `discord_message_id`/`discord_channel_id`
— uglier; the generic rename is preferred.)

---

## 6. New files + the shared decision core

### 6.1 `lib/approval_core.py` (NEW — the transport-agnostic decision core)

Extract the decision logic currently embedded in `approval_handler.handle_callback` into pure,
transport-free functions. **Both** the Telegram handler and the Discord bot call these — this
is the entire "transport-only" change.

Public surface:

```text
# lib/approval_core.py

def decide(nonce, variant_idx, action, *, decided_by) -> dict:
    """Transport-agnostic decision. No network, no Discord/Telegram objects.

    1. resolve nonce -> queue item -> content_id (store.read_queue scan).
    2. idempotency: no pending item for nonce/content_id -> {"already_resolved"}.
    3. nonce verify: item.nonce must equal nonce, else {"bad_nonce"}.
    4. WRITE-AHEAD: store.append_decision(...) FIRST.
    5. approve  -> if not approved_exists: store.write_approved(id, frontmatter, body=variant)
                   -> update_draft status="approved" -> dequeue.
       reject   -> update_draft status="rejected" -> dequeue.
       (revise handled by record_revise, below.)
    Returns {ok, action, content_id, reason?} — same shape handlers return today.
    """

def record_revise(nonce, variant_idx, note, *, decided_by) -> dict:
    """Write-ahead revise decision carrying `note`, bump revise_count,
    set revise_note, dequeue. (Used by the optional Revise modal.)"""
```

**What moves in (verbatim logic, from `approval_handler.py`):**
`_parse_callback_data`'s nonce/idx/action validity, `_resolve_by_nonce`, the idempotency
re-check via `store.find_pending`, the bad-nonce guard, the write-ahead `append_decision`, the
approve branch (`approved_exists` guard → `write_approved` → `update_draft` → `dequeue`), the
reject branch, and the revise recording from `handle_revise_reply`.

**What stays in the transport handler (does NOT move):** auth (`is_authorized`), `custom_id`
parsing into `(nonce, idx, action)`, and all Discord/Telegram side effects (defer, ack, edit
message, send modal). The core takes already-parsed, already-authorized inputs and returns a
result dict; the handler renders that result back to the user.

**Security invariants preserved** (identical to Phase-2):
- Write-ahead: decision appended before any side effect.
- Only the approve branch ever calls `store.write_approved`.
- Idempotent: a double-tap on an already-resolved item is a no-op (`already_resolved`).
- Auth is enforced in the handler *before* the core is ever called (fail-closed).

### 6.2 `bin/discord_bot.py` (NEW — the Discord transport)

Owns: gateway connection, the background post-scan loop (§2), `on_interaction` (§3), the
optional Revise modal (§4), auth (§5.2), dry-run (§5.4), and a `--interaction` test CLI.

Event-handler skeleton (pseudocode — NOT production code):

```python
# bin/discord_bot.py  (SKELETON ONLY)
import discord
from discord.ext import tasks
from lib import store, contracts, approval_core

SCAN_SECONDS = 30
ACTION_MAP = {"a": "approve", "r": "reject", "v": "revise"}

class HermesApprovalBot(discord.Client):
    async def on_ready(self):
        self.post_pending.start()          # begin scan loop once connected

    @tasks.loop(seconds=SCAN_SECONDS)
    async def post_pending(self):
        channel = self.get_channel(int(APPROVAL_CHANNEL_ID))
        for draft in store.iter_drafts(status="pending"):
            if draft["approval"].get("dispatched_at"):        continue
            if store.find_pending(draft["id"]) is not None:   continue
            variant = draft["variants"][0]
            nonce   = secrets.token_urlsafe(8)
            text    = build_message_text(variant)             # lifted from dispatch.py
            if is_dry_run():
                msg_id = deterministic_message_id(draft["id"])
                print_dryrun(text, nonce); 
            else:
                msg = await channel.send(content=text, view=approval_view(nonce, 0))
                msg_id = msg.id
            store.enqueue({...nonce, content_id, channel_id, discord_message_id=msg_id,
                           state="sent", sent_at=contracts.now_iso()})
            store.update_draft(draft["id"], stamp_approval(nonce, channel_id, msg_id))

    async def on_interaction(self, interaction):
        if interaction.type is not component_or_modal:        return
        # 1) AUTH — fail-closed, write nothing on failure
        if not is_authorized(interaction.user.id, interaction.channel_id):
            return await interaction.response.send_message("unauthorized", ephemeral=True)
        # 2) parse custom_id  apr:<nonce>:<idx>:<action>
        parsed = parse_custom_id(interaction.data["custom_id"])
        if parsed is None:
            return await interaction.response.send_message("bad data", ephemeral=True)
        nonce, idx, action = parsed

        if action == "revise":                                 # OPTIONAL, post-MVP
            return await interaction.response.send_modal(ReviseModal(nonce, idx))

        # 3) ACK within 3s
        await interaction.response.defer()
        # 4) shared decision core does nonce-verify + write-ahead + approve/reject
        result = approval_core.decide(nonce, idx, action,
                                      decided_by=f"discord:{interaction.user.id}")
        # 5) disable buttons so it can't be tapped twice (core is also idempotent)
        await interaction.message.edit(view=disabled_view())
        await interaction.followup.send(ack_text(result), ephemeral=True)

# --interaction '<json>' CLI for tests (no gateway): build parsed tuple -> approval_core.decide
```

### 6.3 `bin/approval_handler.py` (REFACTOR, not delete)

Keep the file for the Telegram path but **gut the decision logic** — `handle_callback` becomes:
auth → parse `custom_id` → `approval_core.decide(...)` → ack/edit. `handle_revise_reply`
becomes: auth → `approval_core.record_revise(...)`. Net: Telegram and Discord now share one
core, proving the transport seam.

### 6.4 Migration note

During cutover, both transports can run in parallel (two units) against the same store; the
queue + `dispatched_at` guard + `approved_exists` guard keep them from double-posting or
double-approving. Once Discord is validated, retire the Telegram poster/handler.

### New files summary

| File | Status | Role |
|---|---|---|
| `lib/approval_core.py` | **NEW** | Shared, transport-free decision core (`decide`, `record_revise`). |
| `bin/discord_bot.py` | **NEW** | Standalone Discord bot: post loop + `on_interaction` + modal + dry-run + test CLI. |
| `bin/approval_handler.py` | **REFACTOR** | Telegram handler delegates to `approval_core`. |
| `bin/dispatch.py` | **RETIRE** (or keep for parallel-run) | Telegram REST poster; superseded by the bot's post loop. |
| `lib/contracts.py` | **EDIT** (additive) | Generalize `approval` fields → `message_id`/`channel_id`/`transport`. |

---

## 7. Reconnect / resilience

1. **Gateway auto-reconnect.** `discord.py`'s `Client.run` handles WebSocket drops and resumes
   automatically. systemd/pm2 `Restart=always` covers full-process crashes.
2. **Persistent custom_ids survive restarts.** Because we **handle in raw `on_interaction`**
   (not per-View callbacks), there is **no View registration to lose** on restart. A button
   tapped on a message posted before a restart still arrives with its `custom_id` intact and is
   handled normally. (If we used `discord.ui.View` callbacks instead, we'd have to re-add the
   persistent view via `client.add_view()` on every boot — the raw approach sidesteps this.)
3. **A restart never loses a pending approval.** The **queue is the source of truth.** Pending
   items live in `memory/approvals/queue.json` on disk; `dispatched_at` on the draft prevents
   re-posting. On boot:
   - The scan loop re-examines `status="pending"` drafts: anything already `dispatched_at` +
     present in the queue is skipped (no duplicate post); anything pending-but-unposted gets
     posted.
   - In-flight taps that were never written are simply re-tappable (the message buttons are
     still live), and the decision core is idempotent so a re-tap can't double-write.
4. **Crash mid-decision is safe.** Write-ahead means `append_decision` lands before
   `write_approved`/`dequeue`. If the process dies between them, the item stays queued; the next
   tap (or a reconciliation pass over `decisions.jsonl` vs the queue) completes it. The
   `approved_exists` guard makes `write_approved` itself idempotent.

---

## 8. Summary checklist

- [x] **Process model:** standalone `bin/discord_bot.py` service, separate from hermes-agent gateway.
- [x] **Post + handle:** Pattern (a) — single bot scans `memory/content/` and posts; same bot handles interactions.
- [x] **Interaction style:** raw `on_interaction` parsing `custom_id` (mirrors Telegram `handle_callback`); render via persistent `View(timeout=None)`.
- [x] **`custom_id`:** `apr:<nonce>:<idx>:<action>` ≈ 20 chars, fits Discord's 100-char limit.
- [x] **Ack:** `interaction.response.defer()` within 3s, then `message.edit` to disable buttons.
- [x] **Revise:** optional native Modal (`record_revise` in core from day one; bot adds a `send_modal` branch later).
- [x] **Env:** `DISCORD_BOT_TOKEN`, `DISCORD_ALLOWED_USERS` (empty ⇒ deny-all), `DISCORD_APPROVAL_CHANNEL_ID`, `DISCORD_GUILD_ID`; intents = guilds (message_content NOT needed); dry-run via empty token or `HERMES_DISCORD_DRYRUN`.
- [x] **Shared core:** extract decision logic into `lib/approval_core.py` (`decide`, `record_revise`) — both transports call it.
- [x] **Resilience:** discord.py auto-reconnect; raw custom_ids survive restarts with no view registration; queue is source of truth; write-ahead + idempotent core ⇒ no lost or double approvals.
