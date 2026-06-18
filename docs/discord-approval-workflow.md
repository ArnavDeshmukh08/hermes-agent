# Discord Approval Workflow — Phase-3 Transport Swap (BLUEPRINT ONLY)

> **Specialist D — Approval Workflow Redesign.** Design only. No production code ships from
> this document. Diagrams, sequences, and state tables only.
>
> **Thesis:** The pipeline (Research → CMO → **Approval** → `approved/`) and the content
> `status` state machine are **unchanged from Phase-1**. We swap **only the transport**:
> Telegram inline keyboard + `callback_query` → Discord buttons + `on_interaction`. The
> decision core is transport-agnostic and reused verbatim (extracted to
> `lib/approval_core.py` by a sibling spec; `lib/store.py` + `lib/contracts.py` unchanged).
>
> **Companion specs:** [discord-gateway-spec.md](./discord-gateway-spec.md) (Specialist A,
> process/bot architecture), [workflow-spec.md](./workflow-spec.md) (Phase-1 E2E flow),
> [telegram-approval-spec.md](./telegram-approval-spec.md) (the transport being replaced).

---

## 0. The one-sentence change

> **Identical core, swapped transport.** Everything from "resolve nonce → write-ahead
> `append_decision` → approve-only `write_approved` → status update → dequeue" is reused
> byte-for-byte; the only new code is *how a draft is rendered to a human and how that
> human's tap is received* — Discord buttons + `on_interaction` instead of Telegram inline
> keyboard + `callback_query`.

---

## 1. Updated end-to-end workflow diagram

Same four stages, same file artifacts at every seam. The **only** boxes that changed are the
transport ones (marked **△ CHANGED**); every artifact and the decision core are **= SAME**.

```
┌──────────────────── HEAVY / DETERMINISTIC PATHS (no agent loop) — UNCHANGED ────────────────────┐
│                                                                                                  │
│  cron 02:00 IST     bin/research.py        ──writes──►   memory/research/<run_id>.json           │
│  (no_agent job)     • lean direct LLM call               {ResearchRun + Findings}  = SAME        │
│  ────────────────►  • web fetch → Findings               (immutable facts, no status)            │
│                          │ chained step 2                        │ (consumed by id)               │
│                          ▼                                       ▼                                │
│                     bin/cmo.py            ──writes──►   memory/content/<id>.json                  │
│                     • reads NEW findings                {Content draft, status:"pending"} = SAME  │
│                     • lean LLM → variants[]             • cmo.py is SOLE creator of "pending"     │
│                     • persona from SOUL.md                       │                                │
└──────────────────────────────────────────────────────────────────│─────────────────────────────┘
                                                                     │ scanned by the bot
                                                                     ▼
┌──────── DISCORD APPROVAL BOT — bin/discord_bot.py (single long-running process) ─────────────────┐
│                                                                                                  │
│  △ CHANGED (transport: POST)                                                                      │
│  @tasks.loop(30s) post_pending():                                                                 │
│    for draft in store.iter_drafts(status="pending"):                                              │
│      skip if approval.dispatched_at set     (already posted)                                      │
│      skip if store.find_pending(id)          (already queued)   ──── guards = SAME as dispatch.py │
│      nonce = secrets.token_urlsafe(8)                                                             │
│      msg = channel.send(text, view=Approve/Reject buttons)  ───►  #hermes-approvals (private)     │
│      custom_id = "apr:<nonce>:<idx>:<action>"   (a=approve, r=reject, v=revise)                   │
│      store.enqueue({content_id, nonce, variant_idx, channel_id, discord_message_id, state:sent}) │
│      store.update_draft(id, stamp dispatched_at + message_id + channel_id + nonce)                │
│                                          │                                                        │
│                                          ▼   WRITE  ──►   memory/approvals/queue.json   = SAME    │
│                                                          {updated_at, pending:[ {content_id,      │
│                                                           nonce, variant_idx, …} ]}               │
│                                                                                                  │
│   Arnav clicks  [ ✅ Approve ] / [ ❌ Reject ]   in #hermes-approvals                              │
│        │                                                                                          │
│        ▼  △ CHANGED (transport: RECEIVE)                                                          │
│   on_interaction(interaction):                                                                    │
│     1. interaction.response.defer()                3s ack  (≈ answerCallbackQuery)                │
│     2. parse custom_id → (nonce, idx, action)      (≈ _parse_callback_data)                       │
│     3. AUTHORIZE  fail-closed:                                                                    │
│          interaction.user.id ∈ DISCORD_ALLOWED_USERS  AND                                         │
│          interaction.channel_id == DISCORD_APPROVAL_CHANNEL_ID                                    │
│        │ (deny → ephemeral "unauthorized", write NOTHING)                                         │
│        ▼                                                                                          │
│  ┌────────────────────── lib/approval_core.py  ── = SAME (transport-free core) ───────────────┐  │
│  │  decide(nonce, idx, action, decided_by="discord:<user_id>"):                                │  │
│  │    a. resolve nonce → queue item → content_id   (store.read_queue scan)                     │  │
│  │    b. idempotency: no pending item ⇒ {already_resolved}  (no-op)                             │  │
│  │    c. nonce verify: item.nonce == nonce else {bad_nonce}                                     │  │
│  │    d. WRITE-AHEAD:  store.append_decision(...) FIRST  ──► memory/approvals/decisions.jsonl   │  │
│  │                                                          {ts,content_id,variant_idx,         │  │
│  │                                                           action,note,decided_by} = SAME     │  │
│  │    e. ┌─ action==approve ─► ★ SINGLE HUMAN GATE ★ ────────────────────────────────────────┐ │  │
│  │       │   if not store.approved_exists(id):                                                │ │  │
│  │       │       store.write_approved(id, frontmatter, body=variant.text)                     │ │  │
│  │       │                                              ──► memory/approved/<id>.md  = SAME    │ │  │
│  │       │   store.update_draft(id, status="approved")                                        │ │  │
│  │       └───────────────────────────────────────────────────────────────────────────────────┘ │  │
│  │       └─ action==reject  ─► store.update_draft(id, status="rejected")   (NO approved/ write) │  │
│  │    f. store.dequeue(content_id)                                                             │  │
│  └────────────────────────────────────────────────────────────────────────────────────────────┘  │
│        │                                                                                          │
│        ▼  △ CHANGED (transport: REFLECT outcome)                                                  │
│   interaction.message.edit(view=disabled_buttons + outcome label)                                 │
│   interaction.followup.send(ack_text, ephemeral=True)                                             │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                ┌─────────────────────────┴─────────────────────────┐
                ▼ approve                  ▼ reject                  ▼ revise (OPTIONAL, §4)
   memory/approved/<id>.md       content/<id>.json            content/<id>.json
   (ONLY publish-eligible        status="rejected"            status="revise" + revise_note
    artifact; human-gated)       (TERMINAL, audit)            → bin/cmo.py --revise re-drafts
   status="approved" (TERMINAL)                                → status="pending" → re-posted
```

**Process ownership of each step (who does what):**

| Step | Process | Artifact written |
|---|---|---|
| Research | `bin/research.py` (cron) | `memory/research/<run_id>.json` |
| Draft (sole `pending` creator) | `bin/cmo.py` (cron) | `memory/content/<id>.json` (status `pending`) |
| Post pending draft + buttons | `bin/discord_bot.py` scan loop | `memory/approvals/queue.json`; stamps `approval.*` on the draft |
| Receive tap, ack, auth | `bin/discord_bot.py` `on_interaction` | (no store write yet — gate is auth) |
| Decision (write-ahead, approve/reject, dequeue) | `lib/approval_core.py` (called by bot) | `memory/approvals/decisions.jsonl`; `memory/approved/<id>.md` (approve only); status on the draft |
| Re-draft on revise | `bin/cmo.py --revise <id>` | `memory/content/<id>.json` (status back to `pending`) |

**Seam artifacts (the contract files — all `= SAME` as Phase-1):**

| Seam | Artifact | Producer | Consumer |
|---|---|---|---|
| Research → CMO | `memory/research/<run_id>.json` | `research.py` | `cmo.py` |
| CMO → Approval | `memory/content/<id>.json` | `cmo.py` (create); `discord_bot.py` (stamp `approval.*`) | `discord_bot.py`, `approval_core` |
| Approval queue (pending) | `memory/approvals/queue.json` | `discord_bot.py` (enqueue/dequeue) | `approval_core`, bot scan loop |
| Decision ledger | `memory/approvals/decisions.jsonl` | `approval_core.append_decision` | audit; `cmo.py --revise`; reconciler |
| Approved repo | `memory/approved/<id>.md` | `approval_core` **approve branch only** | downstream publish (out of scope) |

---

## 2. Discord interaction sequence (numbered)

This is the precise per-tap order. Steps **△** are Discord-transport-specific; steps **=** are
the reused core. The single human gate is marked **★**.

```
 0. (precondition) discord_bot.py scan loop already posted the draft to #hermes-approvals
    with two buttons whose custom_id = apr:<nonce>:<idx>:a  and  apr:<nonce>:<idx>:r,
    and recorded the pending item in queue.json. Buttons rendered via a persistent
    View(timeout=None) — render only; handling is global (survives restart, §5).

 1. △  Arnav clicks [✅ Approve] (or [❌ Reject]). Discord fires on_interaction.
       Guard: ignore if interaction.type is not a component interaction.

 2. △  interaction.response.defer()   ← 3-SECOND ACK CONTRACT.
       Acknowledges the tap immediately, buys 15 min for follow-up.
       (Discord equivalent of Telegram answerCallbackQuery.)
       NOTE: for action == "revise" we do NOT defer — a Modal *is* the response (§4).

 3. △  Parse custom_id "apr:<nonce>:<idx>:<action>"  →  (nonce, idx, action).
       Malformed → ephemeral "bad data", no state change, STOP.

 4. ★/△  AUTHORIZE — fail-closed, BEFORE the core is ever called:
            allowed = parse_csv(DISCORD_ALLOWED_USERS)
            ok = bool(allowed)
                 and str(interaction.user.id) in allowed
                 and str(interaction.channel_id) == DISCORD_APPROVAL_CHANNEL_ID
         Deny (empty allowlist, wrong user, or wrong channel) →
            ephemeral "⛔ not authorized", write NOTHING, STOP.
       (This is the auth gate; the *publish* gate is step 8a. Both must pass for approved/.)

 ── steps 5–9 are lib/approval_core.decide(nonce, idx, action,
                                           decided_by=f"discord:{interaction.user.id}") = SAME ──

 5. =  RESOLVE nonce → content_id by scanning store.read_queue()["pending"]
       for an item whose nonce matches.

 6. =  IDEMPOTENCY: if no pending item resolves (already decided / dequeued / stale button) →
       return {already_resolved}. NO write. (Handles double-click + stale-after-restart.)

 7. =  NONCE VERIFY: resolved item.nonce must equal the custom_id nonce, else {bad_nonce}. STOP.

 8. =  WRITE-AHEAD: store.append_decision({ts, content_id, variant_idx, action, note:"",
                                            decided_by:"discord:<user_id>"})
       appended to decisions.jsonl FIRST — before any side effect. This is the durable
       commit point; if we crash after this, the reconciler rolls forward (§5).

 9. =  ACT on action:
       8a. ★ SINGLE HUMAN GATE — action == "approve" (THE ONLY BRANCH THAT WRITES approved/):
              if not store.approved_exists(content_id):
                  store.write_approved(content_id, frontmatter, body=variant.text)
                                                       ──► memory/approved/<id>.md
              store.update_draft(content_id, status="approved")
       8b.   action == "reject":
              store.update_draft(content_id, status="rejected")   ← NO approved/ write
       (revise → record_revise, §4)
       then: store.dequeue(content_id)   ← removes the pending item (makes step 6 a no-op next time)

10. △  REFLECT outcome on Discord:
       interaction.message.edit(view=disabled_view + outcome label)  ← buttons disabled so the
       message can't be re-tapped (UI-layer guard; the core in step 6 is the real guard).
       interaction.followup.send(ack_text(result), ephemeral=True).
       Edit failures are swallowed/logged — the decision is ALREADY durable from step 8.
```

**Where the single human gate is (exactly):** step **8a** inside `approval_core.decide` —
`action == "approve"` is the *only* branch that calls `store.write_approved`. It is reachable
only after step 4 (an allow-listed human, in the configured private channel) and step 8
(the write-ahead decision line). `reject` (8b) never touches `approved/`. No scheduled job,
no scan loop, and no LLM path can synthesize an approve — `approved/<id>.md` exists **iff**
a human clicked Approve and a matching `approve` line is in `decisions.jsonl`.

---

## 3. State machine (content `status` — IDENTICAL to Phase-1)

Same states as Phase-1: `pending → approved | rejected | revise → (re-cmo) pending … → dropped`.
Only the **actor for the approve/reject/revise transitions** changes (Discord bot via
`approval_core` instead of the Telegram gateway handler). Every other transition is unchanged.

```
        (Finding ingested by cmo.py)
                   │
                   ▼
        ┌────────────────────┐
        │      pending       │  cmo.py created variants[]; status="pending"
        │ memory/content/    │  (cmo.py is the SOLE creator of pending)
        │   <id>.json        │
        └─────────┬──────────┘
                  │ discord_bot.py scan loop posts buttons, stamps approval.dispatched_at
                  ▼  (status stays "pending" — dispatch is not a status change)
        ┌────────────────────┐
        │   pending (posted) │  awaiting human; buttons live in #hermes-approvals
        └──┬────────┬────────┴────────┐
   approve │ reject │          revise │   ← set by approval_core (called by discord_bot)
           ▼        ▼                 ▼
    ┌──────────┐ ┌──────────┐ ┌────────────────┐
    │ approved │ │ rejected │ │     revise      │
    │ +approved│ │ (audit)  │ │ status="revise" │
    │ /<id>.md │ │          │ │ revise_note set │
    └──────────┘ └──────────┘ └────────┬───────┘
      TERMINAL     TERMINAL            │ cmo.py --revise regenerates, revise_count++
                                       ▼
                          ┌─────────────────────────┐
                          │ revise_count <= MAX (3)? │
                          └────────┬────────┬────────┘
                              yes  │    no  │
                                   ▼        ▼
                              pending   status="dropped"
                            (re-posted) (TERMINAL, audit)
```

**Who sets each status (single-writer rule per transition):**

| Transition | Set by (Phase-3) | Status written | vs Phase-1 |
|---|---|---|---|
| create | `bin/cmo.py` | `pending` | **SAME actor** |
| dispatched (stamp only) | `bin/discord_bot.py` scan loop | `pending` (unchanged; stamps `dispatched_at`) | actor △ (was `dispatch.py`) |
| **approve** | `approval_core.decide` (called by `discord_bot.py`) — **SOLE writer of `approved/`** | `approved` + `approved/<id>.md` | actor △ (was Telegram handler); **gate logic = SAME** |
| reject | `approval_core.decide` (called by `discord_bot.py`) | `rejected` | actor △; logic = SAME |
| revise | `approval_core.record_revise` (called by `discord_bot.py`) | `revise` (+ note) | actor △; logic = SAME |
| re-draft | `bin/cmo.py --revise` | `pending` (revise_count++) | **SAME actor** |
| drop (cap exceeded) | `bin/cmo.py --revise` | `dropped` | **SAME actor** |

**Invariant (unchanged):** only the approve branch of `approval_core` creates
`memory/approved/<id>.md`, and only after appending the `approve` decision line.
`approved/<id>.md` ⟺ a recorded human `approve`.

---

## 4. Revise (OPTIONAL — deferred past MVP)

**Required buttons are Approve / Reject only.** Revise is fully designed but **optional** and
not in the MVP cut. The state `revise` and the re-cmo loop are part of the Phase-1 machine
(§3) and are preserved; only the *capture mechanism* changes.

```
 1. △  Arnav clicks [✏️ Revise] (custom_id apr:<nonce>:<idx>:v).
 2. △  on_interaction: action == "revise" ⇒ do NOT defer; the Modal IS the response:
          interaction.response.send_modal(ReviseModal(nonce, idx))
       (ReviseModal = discord.ui.Modal with one TextInput for the note.)
 3. △  ReviseModal.on_submit(modal_interaction):
          note = modal_interaction.text_input.value
 4. =  approval_core.record_revise(nonce, idx, note, decided_by="discord:<user_id>"):
          • write-ahead append_decision({action:"revise", note, …}) to decisions.jsonl
          • update_draft: status="revise", approval.revise_note=note,
            approval.revise_count unchanged here (cmo.py --revise bumps it on re-draft)
          • dequeue(content_id)
 5. △  modal_interaction.response.edit_message(view=disabled_view + "✏️ revision requested").
 6. =  bin/cmo.py --revise <id> (re-cmo, deferred for MVP) reads revise_note, regenerates
       variants[] for the SAME id, revise_count++, status="pending" → bot re-posts.
```

**Why the Discord path is strictly cleaner than Telegram's:** Telegram had no free-text in a
callback, so it needed a fragile `awaiting_revise_note` queue flag + a reply-message intercept
("next message wins"). Discord's **native Modal** captures the note atomically, bound to the
exact nonce/draft, with no extra queue state and no message-scanning. `record_revise` lives in
`approval_core` from day one, so enabling Revise later is a one-branch (`send_modal`) addition
to the bot — no core change.

---

## 5. Idempotency & restart (queue is the source of truth)

The decision core is idempotent and the queue file is authoritative; Discord message IDs are
transport metadata only and are **never** authoritative for whether something is decided.

| Scenario | What prevents damage |
|---|---|
| **Double-click** (two taps before the first resolves) | First tap runs `decide` → `append_decision` + `dequeue` under the single-writer store. Second tap re-runs `decide`, finds **no pending item for the nonce** (step 6) → `{already_resolved}` no-op. Plus the UI guard: step 10 disables the buttons after the first tap. No double `write_approved` (also guarded by `approved_exists`). |
| **Stale button after a bot restart** | Buttons are handled in **raw `on_interaction`**, not per-View callbacks — there is **no in-memory View state to lose**, so a tap on a message posted *before* the restart still arrives with its `custom_id` intact and is handled normally. The bot resolves nonce→content_id from the on-disk `queue.json`. If that draft was already decided (not pending), step 6 returns `already_resolved`. |
| **Two pending drafts posted** | Each gets its **own `nonce`** and its own `queue.json` pending entry and its own Discord message. `decide` resolves strictly by nonce → exactly one content_id, so taps never cross-contaminate. The scan loop posts each draft once (guarded by `dispatched_at` + `find_pending`). |
| **Crash mid-decision** | Write-ahead: `append_decision` lands **before** `write_approved`/`update_draft`/`dequeue`. If the process dies between them, the item stays queued and a reconciliation pass (ledger `approve` line present but `approved/<id>.md` missing) rolls it forward. `write_approved` is idempotent via the `approved_exists` guard. |
| **Gateway reconnect** | `discord.py` auto-reconnects/resumes the WebSocket; systemd/pm2 `Restart=always` covers full crashes. Pending approvals survive on disk in `queue.json`; nothing is decided automatically on reconnect. |
| **Scan loop re-runs** | Posting is idempotent: skip if `approval.dispatched_at` is set **or** `store.find_pending(id)` returns an item — the exact guard `dispatch.py` uses today. No double-post. |

**Source-of-truth rule:** `memory/approvals/queue.json` (pending set) + `decisions.jsonl`
(durable ledger) are authoritative. A pending item exists ⇒ undecided; it's gone ⇒ decided
(see the ledger for the outcome). The bot holds no authoritative state in memory.

---

## 6. Identical-vs-changed (proves "redesign the transport only")

| Concern | IDENTICAL to Telegram workflow (reused core) | CHANGED (transport only) |
|---|---|---|
| **Pipeline** | Research → CMO → Approval → `approved/` (same stages, same order) | — |
| **Content `status` machine** | `pending → approved \| rejected \| revise → (re-cmo) pending … → dropped` | — |
| **Data contracts** | `lib/contracts.py` validators (`validate_draft`, `validate_decision`), ID helpers, `new_draft` | `approval` block field names generalized (`message_id`/`channel_id`/`transport`) — additive, validators don't assert these keys |
| **Persistence** | `lib/store.py` — atomic JSON, append-only `decisions.jsonl`, `enqueue`/`dequeue`/`find_pending`, `write_approved` (all unchanged) | — |
| **Queue** | `memory/approvals/queue.json` is the single source of truth; nonce-keyed pending items | — |
| **Decision ledger** | `memory/approvals/decisions.jsonl` — same line shape `{ts, content_id, variant_idx, action, note, decided_by}` | `decided_by` **prefix** = `discord:<user_id>` (was `telegram:<user_id>`) |
| **Approved repo** | `memory/approved/<id>.md`, written by the approve branch only | — |
| **Decision core** | `approval_core.decide` / `record_revise`: nonce verify → idempotency → write-ahead `append_decision` → approve-only `write_approved` → status update → dequeue | — |
| **`custom_id` scheme** | `apr:<nonce>:<idx>:<action>` (a/r/v), nonce-keyed resolution | parsed from `interaction.data["custom_id"]` (was Telegram `callback_data`); fits Discord's 100-char limit (~20 chars used) |
| **Write-ahead ordering** | append decision FIRST, then side effects; crash-safe; reconciler rolls forward | — |
| **Single human gate** | `action=="approve"` is the SOLE caller of `write_approved`; reachable only from an authorized human | — |
| **Button render** | logical "Approve / Reject / Revise" affordances | △ Discord `View(timeout=None)` buttons (was Telegram `InlineKeyboardMarkup`) |
| **Interaction receive** | one parsed entry point → core | △ `on_interaction` (was `callback_query` handler) |
| **Ack** | acknowledge tap immediately | △ `interaction.response.defer()` 3s ack (was `answerCallbackQuery`) |
| **Authorization** | fail-closed allow-list + private-surface check, enforced *before* the core | △ `DISCORD_ALLOWED_USERS` + `channel_id == DISCORD_APPROVAL_CHANNEL_ID` (was `TELEGRAM_ALLOWED_USERS` + DM-only) |
| **Outcome reflection** | disable the affordance so it can't be re-acted | △ `interaction.message.edit(view=disabled)` (was `editMessageReplyMarkup`) |
| **Revise capture** | `record_revise` write-ahead logic | △ native Discord Modal (was `awaiting_revise_note` reply-intercept) — strictly cleaner |
| **Poster process** | scan `content/` for pending+undispatched; same `dispatched_at`/`find_pending` guards | △ folded into `bin/discord_bot.py` scan loop (was `bin/dispatch.py` Telegram REST poster) |
| **Restart safety** | queue on disk is authoritative; idempotent core | △ raw `on_interaction` ⇒ no View re-registration needed (Telegram had no view-state concern) |

**Conclusion:** every contract, the store, the ledger, the `approved/` write, the nonce
scheme, write-ahead ordering, and the single human gate are **unchanged**. The delta is
confined to four transport mechanics — button render, interaction receive/parse,
user+channel auth, message edit — plus the `decided_by` prefix. That is the proof that
Phase-3 is a transport swap, not a redesign of the approval logic.
