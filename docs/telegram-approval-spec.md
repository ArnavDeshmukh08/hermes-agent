# Telegram Approval System — Design Spec (Agent C)

> **Status:** Design only. No code written. Implementable by one developer.
> **Scope:** Human approval gate for generated content (e.g. LinkedIn drafts). This system
> **records the decision and moves approved content into the Approved Content Repository**.
> It does **NOT** post to LinkedIn or anywhere else. **No autonomous publishing — ever.**
> **Conforms to:** Agent D's memory spec. This document owns `memory/approvals/*`; it only
> *reads* `memory/content/<id>.json` and *writes* `memory/approved/<id>.md`.

---

## 0. Platform reality — what I confirmed (read-only inspection)

I inspected the live framework at `~/.hermes/hermes-agent/`. Findings:

| Question | Answer | Evidence |
|---|---|---|
| Native inline keyboards? | **YES** | `gateway/platforms/telegram.py:24` imports `InlineKeyboardButton, InlineKeyboardMarkup`. |
| Native `callback_query` routing? | **YES** | `telegram.py:1631` registers `CallbackQueryHandler(self._handle_callback_query)`. The handler (`telegram.py:3363`) routes by `callback_data` **prefix** (`mp:`, `gt:`, `ea:`, `sc:`, `cl:` …). |
| Is there a precedent for "verb buttons that run a script"? | **YES — exact analog** | `gt:` gmail-triage callbacks (`telegram.py:3731`) dispatch `gt:<verb>:<arg>` to scripts in `~/.hermes/scripts/gmail-triage/` via a `_GT_VERB_DISPATCH` table, with auth + idempotency + message-edit. **This is the blueprint.** |
| Built-in authorization for button clicks? | **YES** | `_is_callback_user_authorized()` (`telegram.py:513`) — resolves via the runner's `_is_user_authorized` or falls back to `TELEGRAM_ALLOWED_USERS` env, **fail-closed**. |
| Can the **agent-facing** `send_message` tool attach inline buttons? | **NO** | `tools/send_message_tool.py` has zero `reply_markup`/`callback_data`/`buttons` support. Inline keyboards are only built **internally** inside `telegram.py` per-feature. |

### The decisive consequence

Because the generic send path **cannot attach buttons**, posting an Approve/Reject/Revise
keyboard requires either (a) adding a new internal feature method to `telegram.py` (like the
model-picker / gmail-triage methods do), or (b) a small **standalone dispatcher script** that
calls the Telegram Bot API directly — exactly what the gmail-triage scripts do. We choose (b)
for the **dispatch (outbound)** side because it needs **zero framework changes**, and (a) — a
tiny `apr:` branch in the existing `_handle_callback_query` — for the **inbound** side, mirroring
`gt:` one-for-one.

---

## 1. Recommendation (TL;DR)

- **Primary mechanism:** **Inline-button approval** (`apr:<id>:<variant>:<action>`), routed through
  a new `apr:` prefix branch in the existing `_handle_callback_query`, modeled byte-for-byte on the
  `gt:` gmail-triage handler. **Native support is confirmed**, so this is low-risk.
- **Mandatory fallback (also implemented):** **Reply-based flow** (`approve <id>` / `reject <id>` /
  `revise <id>: <note>`) handled by a text-intercept. This is required anyway to **capture the
  Revise note** (Telegram callbacks carry no free text), and it is the resilience path if the
  callback handler is ever unavailable.
- **Outbound dispatch:** a small `post_approval.py` script posts the draft **with** the inline
  keyboard by calling the Bot API directly (token from the gateway's existing Telegram config),
  then enqueues the item. Zero framework changes for outbound.
- **Storage:** filesystem queue only — `queue.json` (pending) + `decisions.jsonl` (append-only log).
  **No DB.**

One developer can ship this: one script (`post_approval.py`), one inbound branch (`apr:` in
`telegram.py` + a tiny text-intercept), and the JSON queue files.

---

## 2. Canonical paths & data contracts (conform to Agent D)

```
$HERMES_HOME/memory/                    # HERMES_HOME = ~/.hermes
├── content/<id>.json                   # READ. status: draft|pending  (CMO/CMO-writer owns this)
├── approvals/
│   ├── queue.json                      # WRITE. pending items only (this system owns)
│   └── decisions.jsonl                 # WRITE. append-only decision log (this system owns)
└── approved/<id>.md                    # WRITE on approve. The Approved Content Repository.
```

### 2.1 `content/<id>.json` (input — shape we depend on)

We read only these fields; everything else is opaque and passed through untouched.

```jsonc
{
  "id": "cnt_2026-06-16_a1b2",          // content_id, matches filename
  "status": "draft",                     // draft → we move to pending → approved/rejected
  "variants": [                          // 1..N drafts; we present the top-scored one(s)
    { "idx": 0, "text": "…draft body…", "score": 0.82 },
    { "idx": 1, "text": "…alt draft…",  "score": 0.74 }
  ],
  "meta": { "topic": "patient retention", "channel": "linkedin" },
  "approval": {}                         // we populate: telegram_message_id, queued_at, etc.
}
```

If `score` / `variants` are absent we degrade gracefully (treat whole `text` as variant 0,
score shown as `n/a`).

### 2.2 `decisions.jsonl` line (the decision contract — exact)

```jsonc
{ "ts": "2026-06-16T14:03:22Z", "content_id": "cnt_2026-06-16_a1b2",
  "variant_idx": 0, "action": "approve", "note": "", "decided_by": "telegram:8412…" }
```

`action ∈ {"approve","reject","revise"}`. `note` is non-empty only for `revise`.
`decided_by` = `"telegram:<user_id>"`.

---

## 3. Queue structure — `queue.json`

A single JSON object keyed by `content_id`. Holds **pending items only**; resolved items are
removed on decision (the durable record is `decisions.jsonl` + the content file's own status).

```jsonc
{
  "version": 1,
  "pending": {
    "cnt_2026-06-16_a1b2": {
      "content_id": "cnt_2026-06-16_a1b2",
      "variant_idx": 0,                    // which variant was presented as the primary one
      "shown_variants": [0, 1],            // variants included in the message (for audit)
      "score": 0.82,
      "chat_id": 8412345678,               // Arnav's chat — where the buttons were posted
      "telegram_message_id": 5567,         // the message carrying the keyboard (for edit/idempotency)
      "sent_at": "2026-06-16T14:01:10Z",
      "state": "sent",                     // sent | awaiting_revise_note
      "nonce": "a1b2"                      // short collision-resistant token; see §4 callback scheme
    }
  }
}
```

### Lifecycle of a queue entry

- **Enter:** CMO writes `content/<id>.json` (`status:"draft"`). A **dispatch step** (`post_approval.py`,
  run by CMO after writing, or by a cron sweep over `content/*.json` with `status:"draft"`) posts the
  message with buttons, flips the content file to `status:"pending"`, stamps
  `approval.telegram_message_id` + `approval.queued_at`, and inserts the `pending[<id>]` entry.
- **Leave:** a decision (button or reply) → write a `decisions.jsonl` line → update content file status
  → on approve write `approved/<id>.md` → `del queue.pending[<id>]`.

### Atomicity rule (filesystem only, no DB)

All writes to `queue.json` and to a `content/<id>.json` use **write-temp-then-`os.replace`** under a
per-file lock (`fcntl.flock` on a `queue.json.lock` sentinel). `decisions.jsonl` is append-only with
`O_APPEND` (atomic for small lines). This is the entire concurrency story — one writer process at a
time, no locks held across network calls.

---

## 4. Callback architecture

### 4.1 `callback_data` scheme

Telegram caps `callback_data` at **64 bytes** (the codebase calls this out at `telegram.py:2863`),
so we **cannot** put the full content_id in it safely. Scheme:

```
apr:<nonce>:<variant>:<action>
```

- `apr` — prefix; the new branch in `_handle_callback_query` matches `data.startswith("apr:")`.
- `<nonce>` — short token (e.g. last 4–6 chars of the id, stored as `queue.pending[id].nonce`).
  The handler looks up the **content_id** from `queue.json` by scanning for the matching nonce
  (queue is tiny; O(n) is fine). The nonce keeps us under 64 bytes and avoids leaking the full id.
- `<variant>` — integer variant index the button refers to.
- `<action>` — `a` (approve) | `r` (reject) | `v` (revise).

Example buttons on one message:
```
apr:a1b2:0:a   ✅ Approve v0
apr:a1b2:1:a   ✅ Approve v1      (only if >1 variant shown)
apr:a1b2:0:r   ❌ Reject
apr:a1b2:0:v   ✏️ Revise
```

### 4.2 Mapping callback → content_id + action

In `_handle_callback_query`, add (mirrors the `gt:` block at `telegram.py:3387`):

```python
if data.startswith("apr:"):
    await self._handle_approval_callback(query, data, query_chat_id=..., query_chat_type=...,
                                         query_thread_id=..., query_user_name=...)
    return
```

`_handle_approval_callback` (new method, sibling of `_handle_gmail_triage_callback`):

1. Parse `apr:<nonce>:<variant>:<action>`; on malformed → `query.answer("Invalid approval data.")`.
2. **Authorize:** call the existing `self._is_callback_user_authorized(caller_id, …)`. On fail →
   `query.answer("⛔ You are not authorized to approve content.")`. (Only Arnav's id passes.)
3. **Resolve nonce → content_id** from `queue.json`. If not found → expired/already-resolved
   (see §5).
4. **Approve / Reject:** call the shared decision routine (§4.4) and edit the message to a final
   state with `reply_markup=None` (kills the keyboard → idempotent).
5. **Revise:** do **not** finalize. Set `queue.pending[id].state = "awaiting_revise_note"`, edit the
   message to "✏️ Reply to this message with your revision note for `<id>`", and let the
   text-intercept (§4.3) capture the next reply. (Callbacks can't carry free text — this is *why*
   the reply path must exist.)

### 4.3 Where the handler lives — the integration seam (explicit)

Two seams, both mirroring existing, shipping precedents:

- **Inbound buttons (primary):** add the `apr:` branch + `_handle_approval_callback` **inside
  `gateway/platforms/telegram.py`**, directly modeled on the `gt:` gmail-triage handler
  (`telegram.py:3387` dispatch + `:3731` impl). This reuses the registered `CallbackQueryHandler`,
  the auth helper, and the message-edit idempotency pattern verbatim. ~80 lines.

- **Inbound reply fallback (and Revise note capture):** a **text-intercept**. The framework already
  has this pattern — the clarify tool's "Other → type answer" flips a session into text-capture and
  "the next message in the session is captured by the gateway's text-intercept"
  (`telegram.py:2828–2836`). We reuse it: when any message text matches
  `^(approve|reject|revise)\s+(\S+)(?::\s*(.*))?$` **OR** the chat has a queue entry in state
  `awaiting_revise_note`, route it to the same shared decision routine (§4.4) **before** normal agent
  handling, and swallow it (don't forward to the LLM).

- **Outbound (dispatch):** a standalone **`~/.hermes/scripts/approvals/post_approval.py`** that calls
  the Telegram Bot API `sendMessage` with `reply_markup` directly (the agent-facing `send_message`
  tool can't attach buttons — confirmed). It reads the bot token + Arnav's chat id from the gateway's
  existing Telegram config (same source the gateway uses; `TELEGRAM_BOT_TOKEN` /
  `TELEGRAM_ALLOWED_USERS`), so **no framework change** for outbound.

> Net framework change: one new branch + one new method in `telegram.py` (copy of `gt:`), plus one
> regex check in the text path. Everything else is standalone scripts + JSON files.

### 4.4 Shared decision routine (single source of truth)

Both the button handler and the reply handler call **one** function, e.g.
`scripts/approvals/record_decision.py: record_decision(content_id, variant_idx, action, note, decided_by)`:

1. **Lock** `queue.json`. Re-read it. If `content_id ∉ pending` → return `ALREADY_RESOLVED`
   (idempotency: a double-tap or a button+reply race finds nothing the second time).
2. Append one line to `decisions.jsonl` (the contract from §2.2).
3. Update `content/<id>.json`: `status = "approved" | "rejected" | "revise"`;
   set `approval.decided_at`, `approval.decided_by`, `approval.action`,
   and for revise `approval.revise_note`.
4. **On approve:** write `approved/<id>.md` — the chosen variant's text plus a small front-matter
   header (id, score, approved_at, decided_by). This is the **only** thing "publishing" means here;
   nothing leaves the box.
5. **On revise:** the content stays out of `approved/`. CMO picks up `status:"revise"` +
   `revise_note`, regenerates, and re-dispatches a fresh draft (new message, new queue entry).
6. `del queue.pending[content_id]`; write queue via temp+replace; release lock.
7. Return the result so the caller can edit the Telegram message to the final label.

Because step 1 is the gate, the routine is **idempotent**: whichever input wins removes the pending
entry; the loser is a no-op.

---

## 5. Error handling

| Case | Behavior |
|---|---|
| **Unknown / expired id** (nonce not in queue) | `query.answer("This draft was already resolved or expired.")`; strip keyboard if the message still has one. Reply path: bot replies "No pending draft `<id>`." |
| **Double-tap** (two clicks before first resolves) | First click removes `pending[id]` under lock and edits message `reply_markup=None`. Second click hits `ALREADY_RESOLVED` → `query.answer("Already resolved.")`. No double write. |
| **Button + reply race** (clicks Approve and also types `reject <id>`) | Same lock + `ALREADY_RESOLVED` guard. Whichever acquires the lock first wins; the other is a no-op. |
| **Message edit failure** (Telegram API error on `edit_message_text`) | Wrap in try/except and log (the `gt:`/`ea:` handlers already do `except Exception: pass`). The decision is **already persisted** in step 2–6 before the edit, so a failed edit never loses the decision — it only leaves stale buttons. A clarifying `query.answer()` still confirms to the user. |
| **Revise note capture** | On `:v`, set `state="awaiting_revise_note"` + remember `chat_id`/`content_id`. Next text reply from the authorized user in that chat is consumed as the note, `record_decision(action="revise", note=...)` runs, message edited to "✏️ Revision requested." If the user instead taps another button or types `cancel <id>`, clear the awaiting state. **Timeout:** if no note arrives within N hours (config, default 24h), a cron sweep reverts the entry to `state="sent"` and re-shows the keyboard so it isn't stuck. |
| **Unauthorized user** (not Arnav's id) | `_is_callback_user_authorized` returns False → `query.answer("⛔ You are not authorized…")`, no state change. Reply path: ignored (and logged). Fail-closed: empty allowlist denies all (matches `telegram.py:556`). |
| **Gateway restarts mid-queue** | Queue is on disk (`queue.json`), so pending items survive. On restart the gateway does **nothing automatically** — the buttons on old messages still work (handler re-resolves nonce→id from the persisted queue). A startup sweep (`reconcile.py`, optional) logs any `pending` whose content file is missing/already-decided and drops them. **No auto-publish on restart** — restart never makes a decision. |
| **Content file missing / malformed** at dispatch | `post_approval.py` skips it, logs, leaves no queue entry. |
| **`approved/<id>.md` write fails** | Decision routine treats `approved/` write as the final step; if it throws, abort the `pending` deletion (keep item pending) and surface an error so it's retried rather than silently lost. The `decisions.jsonl` line is written only after `approved/` succeeds for approve actions (reorder: approved-write before log-append for approve), so the log never claims an approval that didn't materialize. |

---

## 6. State transitions

Content-file `status`, driven by this system:

```
            (CMO writes draft)
                  │
                  ▼
              draft ──────dispatch (post_approval.py)──────► pending
                                                               │
                          ┌────────────────────┬───────────────┼───────────────┐
                          │ apr:…:a / "approve" │ apr:…:r       │ apr:…:v        │
                          ▼                     ▼ "reject"      ▼ "revise <note>" │
                      approved              rejected          revise ────────────┘
                  (write approved/<id>.md)  (terminal)        (CMO regenerates →
                  (terminal)                                   new draft → pending)
```

Queue-entry `state` (only while `pending`):

```
sent ──(user taps Revise)──► awaiting_revise_note ──(note received)──► [dequeued: revise]
  │                                   │
  │                                   └─(timeout/cancel)──► sent
  └─(approve/reject)──► [dequeued: approved | rejected]
```

Guarantees:
- Every transition out of `pending` writes exactly one `decisions.jsonl` line.
- `approved/<id>.md` exists **iff** the last decision for `<id>` was `approve`.
- Nothing transitions without an authorized human decision. **No path auto-publishes.**

---

## 7. Telegram message format (what Arnav sees)

Primary (inline buttons), single message:

```
🟢 Draft ready for approval — cnt_2026-06-16_a1b2
score 0.82 · linkedin · topic: patient retention

— v0 —
<the draft text, HTML-escaped>

[ ✅ Approve ]  [ ❌ Reject ]  [ ✏️ Revise ]
```

- Default: present **only the top-scored variant** (lowest cognitive load). If ≥2 variants and the
  score gap is small (config threshold), show two and render per-variant approve buttons
  (`apr:<nonce>:0:a`, `apr:<nonce>:1:a`) so approving also *selects* the variant.
- Content ID and score are always in the header (audit + reply-fallback addressing).

Reply fallback (works with zero callback support):

```
🟢 Draft cnt_2026-06-16_a1b2 (score 0.82). Reply:
  approve cnt_2026-06-16_a1b2
  reject  cnt_2026-06-16_a1b2
  revise  cnt_2026-06-16_a1b2: tighten the hook, drop the emoji
```

The same `<id>` is accepted in full or by its `<nonce>` suffix.

---

## 8. Build checklist (one developer)

1. `scripts/approvals/post_approval.py` — read `content/<id>.json`, format message, `sendMessage`
   with `reply_markup`, write `queue.json` entry, flip content `status:"pending"`.
2. `scripts/approvals/record_decision.py` — the shared, locked, idempotent decision routine (§4.4).
3. `telegram.py`: add `apr:` branch (1 line) + `_handle_approval_callback` (copy `_handle_gmail_triage_callback`, swap script-dispatch for `record_decision`).
4. `telegram.py` text path: regex intercept for `approve|reject|revise <id>` + `awaiting_revise_note`
   capture → `record_decision`.
5. (Optional) `scripts/approvals/reconcile.py` — startup/cron sweep for stale pendings + revise timeouts.

No database. No new long-running process. ~150–200 LOC + the two framework hooks.
```