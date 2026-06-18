# Build Sequence — Hermes Prime Phase-1 (Research → CMO → Approval → Approved)

> **Owner:** Agent F — Implementation Planner. **Planning only — no code in this mission.**
> The build order for the content pipeline. Conforms to the authoritative sibling specs:
> [memory-layer-spec.md](./memory-layer-spec.md) (Agent D — schemas + `memory/*` layout, **wins on any disagreement**)
> and [workflow-spec.md](./workflow-spec.md) (Agent E — who reads/writes each path, in what order).
> Grounded in [ARCHITECTURE-DECISION.md](./ARCHITECTURE-DECISION.md) (dual-path: `no_agent` cron + lean direct LLM, never the agent loop).
> Last meaningful update: 2026-06-16.

---

## 0. Naming reconciliation (read first)

The mission CONTEXT and the workflow spec use slightly different script names. **The
workflow spec's names are canonical** (they appear in the cron lines and the §4 seam
table). Equivalences, so no one builds the wrong file:

| Canonical (workflow spec) | CONTEXT alias | Role |
|---|---|---|
| `bin/research.py` | `bin/research_agent.py` | Research Agent — writes Findings |
| `bin/cmo.py` | `bin/cmo_agent.py` | CMO Agent — Findings → Content drafts |
| `bin/dispatch.py` | `bin/dispatch_drafts.py` | Dispatch — pending drafts → Telegram |
| gateway `approval_handler` (lives in the gateway, **not a standalone script**) | `bin/approval_handler.py` | Telegram callback handler — decisions + `approved/*.md` |

> **One subtlety:** per workflow-spec §8 and §3, the approval handler is a **gateway
> `callback_query` handler**, registered inside the running `hermes-gateway.service`,
> **not** an independently-cron'd script. It is built as a handler module that the
> gateway imports. We still list its source file under `bin/` (or a gateway handlers
> dir) for locality, but it is invoked by Telegram callbacks, never by cron.

All paths below are under `HERMES_HOME = ~/.hermes` and the workflow store is
`~/.hermes/memory/` (singular — **never** the framework's `~/.hermes/memories/`).

---

## 1. Complete file / artifact list

Grouped by component. **(seed)** = hand-authored content file created once at setup.
**(code)** = a script/module to implement. **(generated)** = created at runtime by code,
listed so the tree is complete but **not** built by hand.

### 1.1 Shared library (built first, used by everything)
```
~/.hermes/bin/lib/
├── memio.py        (code)  atomic write (*.tmp → fsync → os.replace); jsonl append;
│                            glob+filter helpers; base-dir resolver
│                            (${HERMES_MEMORY_DIR:-$HERMES_HOME/memory})
├── contracts.py    (code)  validators for Contract A (Finding), B (Content draft),
│                            C (Decision); slug() + content-id builder; status enum guard
└── llm.py          (code)  lean direct LLM call (Ollama primary 65k ctx, Groq 70B
                             fallback); NO skills-hub, NO agent loop, NO 17k context
```
*Why a lib first:* memory-layer-spec §5.5 (atomic single-writer writes) and the three
pinned contracts are used by **every** stage. Building them once removes the #1 source of
cross-stage drift (each stage inventing its own write path / its own id format).

### 1.2 Memory layer — folder tree + seed files
```
~/.hermes/memory/
├── research/                         (generated dir; seed below)
│   ├── _sources.json                 (seed)  research source list / config (1 source for MVP)
│   └── _archive/                     (generated; created on first 90-day archive)
├── content/                          (generated dir, empty at start)
├── approvals/
│   ├── queue.json                    (seed)  initial empty queue: {"updated_at":null,"pending":[]}
│   └── decisions.jsonl               (seed)  created empty (touch); append-only forever
├── voice/
│   ├── style.md                      (seed)  the voice guide — how the persona writes
│   └── samples.md                    (seed)  3–5 example posts exemplifying the voice
└── approved/                         (generated dir, empty at start — guarded: 1 writer only)
```
*Seed-file notes:*
- `voice/style.md` + `voice/samples.md` are **prompt material** for `cmo.py`. They are
  the single biggest quality lever and MUST exist before the CMO stage can produce
  on-voice drafts. Author them from `SOUL.md` (persona) before step 3.
- `research/_sources.json` defines what `research.py` reads. MVP = exactly one source.
- `approvals/queue.json` and `approvals/decisions.jsonl` are seeded (empty) so the first
  dispatch/handler run has files to append to / rewrite (no "file not found" branch).

### 1.3 Stage scripts
```
~/.hermes/bin/
├── research.py     (code)  glob/seed → web fetch + lean LLM summarize → Finding JSON.
│                            Writes memory/research/<yyyymmdd>_<slug>.json (Contract A).
│                            Deterministic Finding id; re-run overwrites, never dups.
├── cmo.py          (code)  reads NEW (consumed==false) findings → lean LLM → variants[]
│                            → memory/content/<id>.json status="pending" (Contract B).
│                            Sets consumed=true on used findings. Supports `--revise <id>`.
├── dispatch.py     (code)  sends pending+undispatched drafts to Telegram w/ inline kbd;
│                            stamps approval.dispatched_at + message_id. `--sweep` mode for
│                            the 30-min retry/re-send + the approve reconciler.
└── handlers/approval_handler.py   (code; imported by the gateway, NOT cron'd)
                                    parses callback_data <id>|<action>|<variant_idx>;
                                    appends Decision (Contract C); write-ahead → approve
                                    writes memory/approved/<id>.md + status=approved;
                                    reject/revise update status; edits the TG message.
```

### 1.4 Cron entries (registered last, after each stage is proven standalone)
```
# Nightly content pipeline — no_agent, zero agent-loop tokens (workflow-spec §3)
0 2 * * *    ~/.hermes/bin/research.py && ~/.hermes/bin/cmo.py && ~/.hermes/bin/dispatch.py
# Re-dispatch / retry / reconcile sweep — deterministic, idempotent
*/30 * * * * ~/.hermes/bin/dispatch.py --sweep
```
The gateway `approval_handler` needs **no** cron line — it fires on Telegram callbacks
inside the always-on `hermes-gateway.service`.

---

## 2. Build order (strict, dependency-ordered)

Foundation-first: the memory layer + contracts + voice seed are the bedrock; then the
one stage testable in pure isolation (research), then each downstream stage with the
previous one's output (or a fixture) as input. **Do not start step N+1 until step N's
standalone verification passes.**

### Step 1 — Shared lib + memory tree + seeds  *(the foundation)*
- **Produces:** `lib/memio.py`, `lib/contracts.py`, `lib/llm.py`; the full
  `~/.hermes/memory/` tree; seeds `research/_sources.json`, `voice/style.md`,
  `voice/samples.md`, `approvals/queue.json`, `approvals/decisions.jsonl`.
- **Depends on:** nothing (greenfield). Reads `SOUL.md` for the voice seed.
- **Verify standalone (before step 2):**
  - `python -c "from lib.contracts import slug; print(slug('Clinic no-shows & DPDP, 2026!'))"` → `clinic-no-shows-dpdp-2026`.
  - `python -c "from lib.contracts import content_id; print(content_id('20260616','1432','no show hook'))"` → `20260616-1432-no-show-hook`.
  - Round-trip a fixture Finding / Content / Decision through the validators: valid passes, a tampered one (missing required field, bad `type` enum, score >1) raises.
  - `memio` atomic write test: write a dict, kill mid-write of a `.tmp` → target file is either absent or fully-valid, never half-written.
  - Tree assert: all five seed files exist; `queue.json` parses to `{"pending":[]}`; `decisions.jsonl` exists and is empty.

### Step 2 — Research Agent (`research.py`)  *(first stage testable alone)*
- **Produces:** `memory/research/<yyyymmdd>_<slug>.json` conforming to Contract A, ≥1 Finding with a real `source_url`.
- **Depends on:** Step 1 (memio, contracts.validate_finding, llm, `_sources.json`).
- **Verify standalone (before step 3):**
  - `python ~/.hermes/bin/research.py` → assert a file matching `memory/research/$(date -u +%Y%m%d)_*.json` exists.
  - Assert it validates against Contract A and has `findings[]` length ≥ 1, each finding `consumed:false`, ≥1 finding carrying a non-null real `source_url`.
  - **Idempotency:** run twice → same filename overwritten, no duplicate file, no duplicate finding ids.

### Step 3 — CMO Agent (`cmo.py`)  *(testable alone with a fixture research file)*
- **Produces:** `memory/content/<content-id>.json` (Contract B), `status="pending"`, ≥1 variant with `score_breakdown` (hook/clarity/voice_match/cta); flips `consumed:true` on the findings it used.
- **Depends on:** Step 1 (contracts, llm, **voice seeds**), and a research file — either the real one from Step 2 **or a hand-made fixture** so CMO is provable in isolation.
- **Verify standalone (before step 4):**
  - Place a known-good fixture in `memory/research/` (or use Step 2's output). Run `python ~/.hermes/bin/cmo.py`.
  - Assert `memory/content/<id>.json` exists, validates Contract B, `status=="pending"`, `variants` length ≥ 2 (MVP target), `source_research_ids[]` non-empty, voice is visibly the persona's (eyeball against `voice/samples.md`).
  - Assert the consumed findings now show `consumed:true` in their `research/*.json`.
  - **Idempotency:** re-run on the same finding → same `content/<id>.json` (deterministic id), no second draft; an already-pending draft is left untouched.

### Step 4 — Dispatch + Telegram approval  *(testable with one hand-made draft)*
- **Produces:** a Telegram message with inline `[✅ approve N] [❌ reject] [✏️ revise]`; on tap, a line in `decisions.jsonl`, and (approve only) `memory/approved/<id>.md` + `status="approved"`.
- **Depends on:** Step 1 (memio, queue/decisions), a Content draft (real from Step 3 **or one hand-made `content/<id>.json`**), Telegram bot creds in `.env`, the running gateway to host `approval_handler`.
- **Verify standalone (before step 5):**
  - `python ~/.hermes/bin/dispatch.py` with one `pending` draft → message arrives; `content/<id>.json` now has `approval.dispatched_at` + `message_id`; a pointer is in `queue.json`.
  - Tap **Approve** → assert `memory/approved/<id>.md` exists with frontmatter `content_id` matching and body = the chosen variant; a matching `approve` line is in `decisions.jsonl`; `status=="approved"`; the draft is removed from `queue.pending[]`; the TG message's buttons are edited away.
  - Tap **Reject** on a second draft → `status=="rejected"`, a `reject` line logged, **no** file in `approved/`.
  - **Double-tap guard:** tap Approve twice → exactly one `approved/<id>.md`, no error (terminal-status check is authoritative).

### Step 5 — Revise loop + cron scheduling
- **Produces:** working `revise` round-trip (handler → `status=revise` + note → `cmo.py --revise` regenerates same `<id>`, `revise_count++` → back to `pending` → re-dispatched), the `MAX_REVISE_CYCLES=3` cap (→ `status=dropped`), and the two registered cron lines.
- **Depends on:** Steps 2–4 all green.
- **Verify standalone (before step 6):**
  - Tap **Revise**, reply with a note → assert `revise` line in `decisions.jsonl`, `approval.revise_note` set, `status=="revise"`, original TG buttons removed.
  - Run `python ~/.hermes/bin/cmo.py --revise <id>` → same `<id>` regenerated, `revise_count==1`, `revise_note` cleared, `status` back to `pending`, `dispatched_at` null; `dispatch.py --sweep` re-sends.
  - Force `revise_count` to 3 then revise again → `status=="dropped"`, no regeneration, a "dropped after 3 revisions" note sent.
  - `crontab -l` (or the framework's `no_agent` registration) shows both lines.

### Step 6 — End-to-end dry run
- **Produces:** one full pass research → CMO → dispatch → human approve → `approved/<id>.md`, driven only by the cron entry (or a manual `&&` chain), no hand-holding.
- **Depends on:** Steps 1–5.
- **Verify:** see §5 "End-to-end" test below.

---

## 3. MVP cut — smallest slice that delivers value end-to-end

**Ship this first; it proves the whole spine with the least surface:**

| Dimension | MVP | Deferred polish (after MVP runs nightly) |
|---|---|---|
| Research sources | **1 source** (`_sources.json` has one entry) | multiple sources; per-source weighting; dedup across sources |
| Topics per run | **1 topic** | topic queue / rotation; trend detection |
| Variants per draft | **2 variants** | 3–5 variants; A/B angle diversity tuning |
| Scoring | a **single overall `score`** is enough to render; `score_breakdown` filled but not tuned | calibrated `hook/clarity/voice_match/cta` weights; threshold gating before dispatch |
| Approval UI | **reply-based** decision is acceptable (`approve`/`reject` typed reply parsed) if inline buttons are slow to wire | **inline buttons** `[✅][❌][✏️]` with `callback_data` (the spec's target — promote ASAP, it's the real UX) |
| Revise loop | optional in MVP (reject + re-run is a fine v0) | full revise round-trip + `MAX_REVISE_CYCLES` cap |
| Scheduling | run the chain **manually** to prove it | the `0 2 * * *` + `*/30` cron lines |
| Reconciler | skip | `dispatch.py --sweep` ledger→file reconciler |

**MVP definition of done:** one source → one Finding file → one 2-variant draft → one
Telegram message → one human Approve → one `approved/<id>.md`, with the `approve` line in
`decisions.jsonl`. That is the entire value loop in its thinnest form.

> Inline buttons vs reply-based is the one place to consciously trade down for MVP speed,
> then upgrade — everything else (atomic writes, contracts, single human gate) is
> non-negotiable from day one because retrofitting them is expensive and risky.

---

## 4. What can run immediately after implementation

The **first real end-to-end run** (Step 6) is:
```
~/.hermes/bin/research.py && ~/.hermes/bin/cmo.py && ~/.hermes/bin/dispatch.py
```
**It produces, in order:**
1. `memory/research/<today>_<slug>.json` — ≥1 Finding with a real source.
2. `memory/content/<id>.json` — a `pending` draft with 2 variants; used findings flipped `consumed:true`.
3. A **Telegram message to Arnav** with approve/reject(/revise) controls; the draft stamped `dispatched_at` + `message_id`, a pointer in `queue.json`.
4. On Arnav's **Approve** tap: `memory/approved/<id>.md` (the first entry in the Approved Content Repository) + an `approve` line in `decisions.jsonl` + `status="approved"`.

Nothing publishes. The pipeline stops at `approved/` — the human gate (workflow-spec §7).
Once the cron lines from Step 5 are registered, this same loop runs unattended each night
and a draft is simply waiting for Arnav's tap in the morning.

---

## 5. Manual tests that prove success

Copy-pasteable, per stage. (Replace `<id>` / `<slug>` with the actual generated value;
`TODAY=$(date -u +%Y%m%d)`.)

**Research (Step 2):**
```bash
python ~/.hermes/bin/research.py
ls ~/.hermes/memory/research/${TODAY}_*.json           # ≥1 file exists
python - <<'PY'                                        # ≥1 finding w/ real source_url
import glob,json,datetime
f=sorted(glob.glob(f"{__import__('os').path.expanduser('~')}/.hermes/memory/research/*.json"))[-1]
d=json.load(open(f)); fs=d["findings"]
assert len(fs)>=1, "no findings"
assert any(x.get("source_url") for x in fs), "no finding carries a real source_url"
assert all(x["consumed"] is False for x in fs), "findings must start consumed=false"
print("OK research:", f, len(fs), "findings")
PY
```

**CMO (Step 3):**
```bash
python ~/.hermes/bin/cmo.py
python - <<'PY'                                        # draft valid, ≥2 variants, sources non-empty
import glob,json,os
f=sorted(glob.glob(os.path.expanduser('~/.hermes/memory/content/*.json')))[-1]
d=json.load(open(f))
assert d["status"]=="pending"
assert len(d["variants"])>=2
assert d["source_research_ids"], "draft has no source findings"
assert d["id"]==os.path.basename(f)[:-5], "content-id must equal filename stem"
print("OK cmo:", d["id"], len(d["variants"]), "variants")
PY
# and assert the used findings are now consumed:true in research/*.json
```

**Approval (Step 4) — the success path AND the guardrail:**
```bash
python ~/.hermes/bin/dispatch.py                       # message arrives in Telegram
# --- tap Approve on the message, then: ---
ID=<id>
test -f ~/.hermes/memory/approved/${ID}.md && echo "approved file exists"
grep -q "\"content_id\":\"${ID}\".*\"action\":\"approve\"" ~/.hermes/memory/approvals/decisions.jsonl \
  && echo "approve line logged"
# frontmatter content_id matches:
head -20 ~/.hermes/memory/approved/${ID}.md | grep -q "content_id: ${ID}" && echo "frontmatter OK"
```

### The three most important success tests (must all pass)

1. **Research produces real, sourced findings.**
   `python ~/.hermes/bin/research.py` then assert `memory/research/<today>_*.json` exists
   and contains ≥1 finding with a **non-null real `source_url`**. (If research can't cite,
   the whole pipeline is unsourced — fail fast here.)

2. **Approve creates exactly the approved artifact + the ledger line.**
   Post a draft, tap **Approve** → assert `memory/approved/<id>.md` exists (frontmatter
   `content_id` matches, body = chosen variant) **and** a matching `approve` line is in
   `decisions.jsonl` **and** `content/<id>.json` `status=="approved"`. This is the value
   loop's terminal success.

3. **GUARDRAIL — no approval ⇒ nothing in `approved/` (the hard rule).**
   Run the full chain and dispatch a draft, then **do NOT tap Approve** (or tap **Reject**).
   Assert `memory/approved/` contains **no** file for that `<id>`, and `decisions.jsonl`
   contains **no** `approve` line for it. Repeat after a gateway restart to prove state is
   file-driven, not in-memory. This structurally enforces "human approval mandatory; no
   autonomous publishing" (workflow-spec §7) — `approved/` has exactly one writer
   (`approval_handler`), reachable only from a real human tap.
   ```bash
   # after dispatch but with NO approve tap:
   ID=<id>
   test ! -f ~/.hermes/memory/approved/${ID}.md && echo "GUARDRAIL OK: no approved file without approval"
   ! grep -q "\"content_id\":\"${ID}\".*\"action\":\"approve\"" ~/.hermes/memory/approvals/decisions.jsonl \
     && echo "GUARDRAIL OK: no approve line"
   ```

**End-to-end (Step 6):** run the cron chain manually, tap Approve once, and assert all of
test 1 + test 2 pass in a single pass with no manual file edits in between.

---

## 6. Build spine, one line

**(1) lib + memory tree + voice seed → (2) research.py → (3) cmo.py → (4) dispatch.py + gateway approval_handler → (5) revise loop + cron → (6) e2e dry run.**
Each step ships only after its standalone verification passes; the foundation (atomic
writes, the three contracts, the voice seed) is built once and reused by every stage.
