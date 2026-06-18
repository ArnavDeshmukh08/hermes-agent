# Workflow Spec — Research → CMO → Approval → Approved (Phase-1)

> **Owner:** Agent E (Workflow Integrator). **Design only — no code in this mission.**
> Ties together the four components (A=Research, B=CMO, C=Approval/Telegram, D=Memory contracts).
> Grounded in [ARCHITECTURE.md](./ARCHITECTURE.md) and [ARCHITECTURE-DECISION.md](./ARCHITECTURE-DECISION.md):
> this content pipeline runs on the **deterministic path** (`no_agent` cron + standalone scripts)
> and the **heavy-reasoning path** (lean direct LLM calls), **never through the agent loop**.

## 0. One-paragraph summary
Overnight, a `no_agent` cron runs `research.py`, which writes Finding JSONs to
`memory/research/`. A chained step (`cmo.py`) reads new findings and writes Content draft
JSONs to `memory/content/` with `status=pending`. A dispatch step (`dispatch.py`) pushes
each pending draft to Arnav on Telegram with inline approve/reject/revise buttons. The
gateway's callback handler (`approval_handler`) appends one line to
`memory/approvals/decisions.jsonl` and acts on it: **approve** promotes the chosen variant
to `memory/approved/<id>.md`; **reject** closes it; **revise** re-queues it for `cmo.py`
with the note. **The single human gate is the `approve` decision — nothing is written to
`memory/approved/` (and nothing is ever auto-published) without a matching `approve` line
in `decisions.jsonl`.**

---

## 1. End-to-end flow diagram

```
                       ┌─────────────────────── HEAVY / DETERMINISTIC PATHS (no agent loop) ───────────────────────┐
                       │                                                                                            │
  cron 02:00 IST       │   ~/.hermes/bin/research.py            ──writes──►   memory/research/<date>_<slug>.json    │
  (no_agent job)       │   • lean direct LLM call (Ollama/Groq)                {Finding}                            │
  ───────────────────► │   • web fetch + summarize → Findings                  status: n/a (immutable fact)         │
                       │           │ chained (same cron, step 2)                       │                            │
                       │           ▼                                                   │ (consumed by id)           │
                       │   ~/.hermes/bin/cmo.py                  ──writes──►   memory/content/<id>.json             │
                       │   • reads NEW findings (since cursor)                  {Content draft, status:"pending"}    │
                       │   • lean direct LLM call → variants[]                        │                             │
                       │   • persona from SOUL.md (Hamza/Jack)                        │                             │
                       │           │ chained (same cron, step 3)                      │                             │
                       │           ▼                                                  ▼                             │
                       │   ~/.hermes/bin/dispatch.py            ──reads──►   pending drafts not yet dispatched       │
                       │   • for each pending+undispatched:                          │                             │
                       │     send Telegram msg w/ inline kbd                         │ sets approval.dispatched_at  │
                       │     [✅approve N][❌reject][✏️revise]                         │ (back into content/<id>.json)│
                       └─────────────────────────────────│──────────────────────────┘                             │
                                                          ▼
                            ┌──────────────────────── GATEWAY (interactive path, tiny) ────────────────────────┐
   Arnav taps a button ───► │  hermes-gateway.service → callback_query handler: approval_handler                │
   (Telegram inline kbd)    │  • parse callback_data: <id>|<action>|<variant_idx>                               │
                            │  • (revise) prompt for note → capture reply                                       │
                            │  • APPEND one Decision line ──►  memory/approvals/decisions.jsonl  (append-only)   │
                            │  • act on action:                                                                 │
                            │       approve → write memory/approved/<id>.md ; content.status="approved"         │
                            │       reject  → content.status="rejected"                                         │
                            │       revise  → content.status="revise"; stash note; (re-CMO picks it up)         │
                            └──────────────────────────────────│───────────────────────────────────────────────┘
                                                               ▼  (revise only)
                            ~/.hermes/bin/cmo.py --revise <id>  (chained from handler OR next cron sweep)
                              • reads content/<id>.json + revise note → regenerates variants[]
                              • status back to "pending", revise_count++  ──► dispatch.py re-sends
                                          │
                                          ▼
                            ┌── TERMINAL STATES ──┐
                            │ memory/approved/<id>.md   ← the ONLY publish-eligible artifact (human-gated) │
                            │ content/<id>.json status=rejected / dropped  (stays as audit record)         │
                            └──────────────────────────────────────────────────────────────────────────────┘
```

**Trigger legend:** `research.py`, `cmo.py`, `dispatch.py` are **chained steps of one
overnight `no_agent` cron** (default). `cmo.py --revise` and `approve`/`reject` writes are
driven by the **gateway callback handler**. `dispatch.py` also runs as a lightweight
**standalone sweep** (every 30 min) so revised drafts re-send without waiting for the next
night — see §3.

---

## 2. Content lifecycle state machine

A *Finding* is an immutable fact and has **no status**; it is an input, not a tracked
entity. The tracked entity is the **Content draft** (`memory/content/<id>.json`). Its
`status` field is the single source of truth for lifecycle.

```
         (Finding ingested by cmo.py)
                    │
                    ▼
        ┌──────────────────────┐
        │       draft          │  cmo.py just created variants[]; not yet sent
        │  status="pending"    │  lives: memory/content/<id>.json
        └──────────┬───────────┘
                   │ dispatch.py sends to Telegram, stamps approval.dispatched_at
                   ▼
        ┌──────────────────────┐
        │      pending         │  awaiting human; buttons live in Telegram
        │  status="pending"    │  lives: memory/content/<id>.json (dispatched=true)
        └───┬──────────┬───────┴─────────┐
   approve  │   reject  │          revise │
            ▼           ▼                 ▼
   ┌─────────────┐ ┌──────────┐  ┌───────────────────┐
   │  approved   │ │ rejected │  │      revise        │
   │ +approved/  │ │ (audit   │  │ status="revise"    │
   │   <id>.md   │ │  record) │  │ note stashed       │
   └─────────────┘ └──────────┘  └─────────┬──────────┘
     TERMINAL        TERMINAL              │ cmo.py --revise regenerates
                                           │ revise_count++
                              ┌────────────┴─────────────┐
                              │ revise_count <= MAX (3)?  │
                              └───────┬───────────┬───────┘
                                 yes  │       no  │
                                      ▼           ▼
                               back to        status="dropped"
                              "pending"        (TERMINAL, audit record)
                              (re-dispatch)
```

**Who sets each status (single-writer rule per transition):**

| Transition | Set by | Status written | Physical location of item |
|---|---|---|---|
| create | `cmo.py` | `pending` | `memory/content/<id>.json` |
| dispatched | `dispatch.py` (stamps `approval.dispatched_at`, status unchanged) | `pending` | same file |
| approve | gateway `approval_handler` | `approved` | file + new `memory/approved/<id>.md` |
| reject | gateway `approval_handler` | `rejected` | `memory/content/<id>.json` (audit) |
| revise | gateway `approval_handler` | `revise` | `memory/content/<id>.json` (+ note) |
| re-draft | `cmo.py --revise` | `pending` (revise_count++) | `memory/content/<id>.json` |
| drop | `cmo.py --revise` (cap exceeded) | `dropped` | `memory/content/<id>.json` (audit) |

**Invariant:** only the gateway handler may move an item to `approved`, and only after it
has appended the `approve` Decision line. `approved/<id>.md` is written **after** the
decision line is durably appended (§6, §7).

---

## 3. Triggers & scheduling

| Stage | Script | Trigger | Cadence | LLM? |
|---|---|---|---|---|
| Research | `~/.hermes/bin/research.py` | `no_agent` cron job | overnight, 02:00 IST | lean direct call (Ollama primary, Groq fallback) |
| CMO draft | `~/.hermes/bin/cmo.py` | chained step 2 of same cron | immediately after research | lean direct call |
| Dispatch | `~/.hermes/bin/dispatch.py` | chained step 3 of cron **+** standalone sweep | after CMO, then every 30 min | none (deterministic) |
| Approval | gateway `approval_handler` | Telegram `callback_query` (gateway) | on tap, 24/7 | none |
| Re-CMO (revise) | `cmo.py --revise <id>` | invoked by handler *or* next dispatch sweep picks `status=revise` | on demand | lean direct call |

**Why this shape:**
- **Chained overnight cron** keeps the happy path one job: research → draft → first
  dispatch in a single `no_agent` run, so a normal night needs zero interactive turns.
- **Dispatch also runs as a standalone 30-min sweep** so two things don't stall:
  (a) revised drafts (`status=revise` → re-CMO → `pending`) get re-sent the same day, and
  (b) any draft that failed to send overnight (Telegram down) is retried. The sweep is
  idempotent (§6): it only sends drafts where `status=pending` **and**
  `approval.dispatched_at` is null.
- **Respecting the 12k cap:** research and CMO use **lean direct LLM calls** (a single
  prompt with only the findings/persona slice — no skills-hub, no agent loop), routed to
  **Ollama** (65k ctx, no TPM cap) as primary for these heavy generation jobs, with Groq
  70B as fallback for short calls. Nothing here loads the 17k always-on context, so no
  stage can 413. The gateway handler does **no** LLM work — it's pure dispatch + file IO.

**Cron registration (illustrative, design-level):**
```
# Nightly content pipeline (no_agent — zero agent-loop tokens)
0 2 * * *   ~/.hermes/bin/research.py && ~/.hermes/bin/cmo.py && ~/.hermes/bin/dispatch.py
# Re-dispatch / retry sweep (deterministic)
*/30 * * * * ~/.hermes/bin/dispatch.py --sweep
```

---

## 4. Data-contract table (the seams)

Every handoff artifact, its producer, and its consumer — so no stage guesses another's
format. Schemas are **owned by Agent D**; this table fixes who reads/writes each.

| Seam | Artifact (path) | Schema | Producer | Consumer(s) |
|---|---|---|---|---|
| A→B | `memory/research/<date>_<slug>.json` | **Finding** `{id, run_date, type, topic, summary, source_url, raw_excerpt, tags[]}` | `research.py` | `cmo.py` |
| B→C | `memory/content/<id>.json` | **Content draft** `{id, created_at, source_research_ids[], persona, platform, variants[...], status, approval{}}` | `cmo.py` (create); `dispatch.py` (stamps `approval.dispatched_at`) | `dispatch.py`, gateway `approval_handler` |
| C→ledger | `memory/approvals/decisions.jsonl` | **Decision** `{ts, content_id, variant_idx, action, note, decided_by}` (append-only, one JSON/line) | gateway `approval_handler` | audit; `cmo.py --revise` (reads latest note); `dispatch.py` (skips decided ids) |
| C→D | `memory/approved/<id>.md` | **Approved item** (markdown body = chosen variant) | gateway `approval_handler` (approve only) | downstream publish step (out of Phase-1 scope) |

**`approval{}` sub-object on the Content draft** (the per-item routing state dispatch and
the handler share — within Agent D's `approval{}` slot):
```
approval: {
  dispatched_at: <iso|null>,   // set by dispatch.py when the Telegram msg is sent
  message_id:    <int|null>,   // Telegram message id, for editing the keyboard on decision
  decided_at:    <iso|null>,   // set by handler when a terminal/loop decision lands
  decision:      <"approve"|"reject"|"revise"|null>,
  revise_count:  <int>,        // 0 on first draft; ++ each re-CMO
  revise_note:   <string|null> // last note from a revise decision, consumed by cmo.py --revise
}
```
`callback_data` carried by each inline button: `"<content_id>|<action>|<variant_idx>"`
(kept < Telegram's 64-byte limit by using short ids).

---

## 5. The Revise loop

1. Arnav taps **✏️ revise** on variant *N*. Gateway prompts "what should change?"; his
   reply is captured as the **note**.
2. Handler appends a Decision: `{action:"revise", variant_idx:N, note:"...", ...}` to
   `decisions.jsonl`, sets `content.status="revise"`, `approval.revise_note=note`,
   `approval.decided_at=now`, and **edits the original Telegram message** to remove its
   buttons (so the stale draft can't be acted on twice).
3. The draft is re-picked by `cmo.py --revise <id>` — invoked directly by the handler, or
   (fallback) by the next dispatch sweep that sees `status=revise`. `cmo.py` reads the
   current draft + `revise_note` + the original `source_research_ids[]`, **regenerates
   `variants[]` for the same `<id>`** (id is stable across cycles — see §6), increments
   `approval.revise_count`, clears `revise_note`, sets `status="pending"`,
   `approval.dispatched_at=null`.
4. `dispatch.py` re-sends the regenerated draft (new Telegram message, new `message_id`).

**Cycle cap:** `MAX_REVISE_CYCLES = 3`. Before regenerating, `cmo.py --revise` checks
`revise_count`. If incrementing would exceed the cap, it does **not** regenerate; it sets
`status="dropped"` and sends Arnav a one-line "dropped after 3 revisions — start over with
a fresh idea?" note. Dropped items are terminal and remain as audit records. This bounds
infinite back-and-forth and unbounded LLM spend.

---

## 6. Failure & idempotency across stages

The whole pipeline is **file-driven and re-runnable**. Two rules make it idempotent:
**stable ids** and **guard-on-state** (every writer checks current status before acting).

| Failure / re-run | What prevents damage |
|---|---|
| **`research.py` runs twice** | Finding id is deterministic: `sha1(source_url + topic)` (or `<date>_<slug>`). Re-run writes the same filename → overwrite, no dup. CMO de-dups by `source_research_ids[]` already consumed (tracks a cursor / a `consumed` marker), so the same finding never spawns two drafts. |
| **`cmo.py` runs twice on the same finding** | Content `<id>` is deterministic from `source_research_ids[] + persona + platform`. Second run targets the **same** `content/<id>.json`. CMO **only creates if absent or if status∈{revise}**; an existing `pending/approved/rejected` draft is left untouched. No duplicate draft. |
| **`dispatch.py` sends a draft twice** | Dispatch sends **only** when `status=="pending" AND approval.dispatched_at==null`. It stamps `dispatched_at` + `message_id` **before** considering the send complete; the 30-min sweep then skips it. A draft already decided (status≠pending) is skipped. → no double-post. |
| **Telegram send fails mid-dispatch** | `dispatched_at` is written **only after** the Telegram API returns success. If the send throws, `dispatched_at` stays null → next sweep retries. (At-least-once send, but the decision ledger de-dups outcomes.) |
| **Gateway restarts with a pending queue** | State lives in files, not memory. On restart the handler reconstructs nothing — pending drafts are simply those with `status=pending`. Buttons in already-sent Telegram messages still post `callback_data`; the handler is **idempotent on decision**: if a Decision for `(content_id, action)` already exists in `decisions.jsonl` *or* `status` is already terminal, it ignores the duplicate tap and re-confirms via message edit. → no lost and no double approvals. |
| **Two taps / double-fire callback** | Handler does a **read-modify guard**: it appends the Decision and flips status **only if** `status==pending`. The second tap finds `status≠pending` and is a no-op. `decisions.jsonl` may rarely get two lines for the same id under a race; the **terminal-status check** (not the ledger) is authoritative for side effects, so `approved/<id>.md` is still written exactly once. |
| **Research returns nothing** | `research.py` writes zero Finding files; `cmo.py` finds no new findings → creates no drafts; `dispatch.py` has nothing pending → sends nothing. The pipeline is a clean no-op night. (Optional: a heartbeat "no findings tonight" note, low priority.) |
| **`approved/<id>.md` write fails after the decision line** | The Decision line is the commit point. On the next sweep, a reconciler step (`dispatch.py --sweep`) detects `status` not yet `approved` but a matching `approve` line in the ledger, and **re-creates** `approved/<id>.md`. Idempotent because the file path is `<id>`-keyed. |

**Ordering guarantee for approve (write-ahead):** append Decision line → fsync →
write `approved/<id>.md` → set `status=approved`. If we crash between steps, the ledger is
the durable record and the sweep reconciles forward. We never set `status=approved` before
the ledger line exists, so we can never "lose" the fact that approval happened, nor publish
without it.

---

## 7. Guardrail enforcement — the single human gate

**Hard rule (from CLAUDE.md): no autonomous publishing. Human approval mandatory.**
Enforced structurally, not by convention:

1. **Only one writer can create `memory/approved/<id>.md`:** the gateway
   `approval_handler`, and **only** inside the `action=="approve"` branch. No script
   (`research.py`, `cmo.py`, `dispatch.py`) ever writes to `memory/approved/`. (This is a
   reviewable, greppable invariant: `approved/` has exactly one producer.)
2. **That branch is reachable only from a real human tap.** The approve path begins at a
   Telegram `callback_query` on Arnav's chat id; the handler verifies `decided_by` is the
   allowed Arnav id before acting. There is no code path that synthesizes an approve.
3. **Write-ahead to the ledger:** the handler appends the `approve` Decision to
   `decisions.jsonl` **before** writing the `.md`. So every file in `memory/approved/` is
   backed by a matching `approve` line. The reconciler enforces the converse direction
   (ledger `approve` ⇒ file exists); together: **`approved/` ⟺ a recorded human `approve`.**
4. **Publishing is out of Phase-1 scope and also gated:** even once a downstream publisher
   exists, it consumes `memory/approved/` — which by (1)–(3) only ever contains
   human-approved items. Sends remain a separate approval per the global guardrails.

**Auditability:** `decisions.jsonl` is append-only and timestamped; it is the complete,
replayable record of every approve/reject/revise, by whom, with what note. Any item in
`approved/` can be traced to its exact `approve` line and back through `content/<id>.json`
→ `source_research_ids[]` → the originating Findings.

---

## 8. Boundary resolutions (A / B / C / D)
- **D owns the schemas and the `memory/*` directory layout**; E owns *who reads/writes
  each path and in what order* (§4 table, single-writer rules in §2).
- **A (research.py) writes `research/` only.** It never touches `content/` or status.
- **B (cmo.py) is the only creator of `content/<id>.json`** and the only re-drafter; it
  never sends Telegram messages and never writes `approved/`.
- **C (dispatch.py + gateway handler) owns all Telegram IO**, the `approval{}` sub-object,
  the `decisions.jsonl` ledger, and the sole write to `approved/`. dispatch.py never calls
  an LLM; the handler never calls an LLM.
- **Stable, deterministic ids** (Finding id from source+topic; Content id from
  source_research_ids+persona+platform) are the contract that makes every stage
  idempotent and re-runnable — this is the one cross-cutting decision E imposes on A–D.
```
research.py ──► research/*.json ──► cmo.py ──► content/*.json ──► dispatch.py ──► Telegram
                                       ▲                              │
                                       └──── revise note ◄── decisions.jsonl ◄── approval_handler ──► approved/*.md
```
```
```
