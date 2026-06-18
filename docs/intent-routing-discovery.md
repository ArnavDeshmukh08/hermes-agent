# Intent Routing — Discovery (Phase 1)

> Goal: when a Discord message is an operational task, bypass the heavy Hermes agent loop
> (~29.6k tokens → Groq 413) and dispatch straight to the validated `worker/` system.
> Method note: the agent loop + Discord adapter live in the `hermes-agent` framework **on the VPS**,
> so framework-side mapping was done by reading the live source over SSH (read-only); worker-side
> mapping uses the local `worker/` repo. Findings below are cited to real file:line.

---

## Agent A — Current Discord message flow

```
Discord message
  → discord adapter on_message()                 plugins/platforms/discord/adapter.py:767
      → auth/channel/mention gates (require_mention, allowed_channels, allowlist)
      → _handle_message(message, role_authorized) adapter.py:863 / def @4731
          → raw_content = message.content.strip()  adapter.py:4756   ← TEXT AVAILABLE HERE
          → (auto-thread, normalize) → builds a MessageEvent → gateway dispatch
  → gateway message handler                        gateway/run.py (the big dispatch fn)
      → if recognized slash command: emit_collect("command:<name>") ── decision hook ──┐
      → else (PLAIN CHAT): falls through to ▼                                            │
      → emit("agent:start", {message,...})         run.py:8480  (fire-and-forget)        │
      → agent_result = self._run_agent(message=…)  run.py:8482  ← THE 29k PATH ──────────┘
  → response delivered back via adapter.send()      adapter.py:1420
```

## Agent B — Worker architecture entry points
- **Canonical entry:** `~/.hermes/hamza_worker/bin/leadgen.py --spec -` (JSON spec on stdin), already
  deployed + production-validated. Async pool: discover→research∥social→outreach→validate→Sheet,
  then emits `task_complete` → Discord webhook summary. (worker/prime.py `HermesPrime.run_parallel`.)
- **Spec contract:** `{target, location, columns[], count, outreach, sources[], sheet_tab}` —
  dynamic columns, no per-task code. (worker/specs.py)
- **Task queue (already exists):** `tasks/{pending,running,completed,failed}/` atomic file states
  (worker/queue.py) — directly reusable for a `status` command.
- **Notify (already exists):** worker/discord.py posts the summary to `DISCORD_WEBHOOK_URL`.

## Agent C — Operational commands currently supported (by the worker, today)
| Intent | Example phrasings | Worker mapping |
|---|---|---|
| **Lead discovery** | "find/scrape physiotherapy clinics in Mumbai", "find leads/dentists", "research companies" | `leadgen.py --spec` with parsed `{target, location, count}` |
| **Outreach generation** | "generate pitch / create outreach / draft message" | same spec with `outreach:true` (pitch column) |
| **Status / ops** | "status", "queue", "running tasks" | read `tasks/{pending,running,completed,failed}/` counts — no worker run |
| **Knowledge** *(exists separately)* | "save note / remember this / brain dump" | existing `bin/brain.py` capture (defer to v1.1; not lead-engine) |

## Agent D — Where the 29k conversational path begins
- `gateway/run.py:8482  self._run_agent(message=message_text, …)` — preceded by the fire-and-forget
  `emit("agent:start")` at 8480. This assembles system prompt + **SOUL.md** + **skills-hub prompt
  (~10.6k tok, 73 skills)** + tool schemas + USER.md → live 413 proof: *"Requested 29,625, Limit
  12,000 TPM"* (agent.log 20:26:45). Confirmed identical on Telegram (platform-independent).
- **There is exactly ONE decision-capable hook** (`emit_collect`, run.py:7054) and it fires **only for
  recognized slash commands**. Plain `@Hamza …` text has **no** native pre-agent short-circuit.

## Agent E — Safest insertion point
Two viable seams; both keep ALL routing logic in external modules (zero logic in framework code):

| Option | Seam | UX | Framework edit | Robustness |
|---|---|---|---|---|
| **E1 — adapter shim (RECOMMENDED)** | `_handle_message` right after `raw_content` (adapter.py:~4757): call external `hamza_router.try_route(raw_content, message, self)`; if it handles → `return` (agent skipped) | **`@Hamza find …` free-text** (matches success criterion) | ~6 lines, one file, all logic external | one bundled-plugin touch; backed-up + documented; trivially reversible |
| **E2 — native slash-command hook** | register `/find`,`/status`; `command:*` `emit_collect` returns `{"decision":"handled","message":…}` | `/find …` (not free-text) | **zero core edits** | most robust; framework-native; but wrong UX |

**Recommendation:** **E1 (adapter shim) as primary** — it is the only way to satisfy the explicit
success criterion (`@Hamza find 100 physiotherapy clinics in India`), and the touch is a single
6-line call into an external module (`hamza_router`). Ship **E2 as a zero-edit fallback** for the
robust slash path. The shim runs *before* any agent token is spent → no 29k, no 413.

---

## Proposed thinnest layer (for Phase 2)
```
~/.hermes/hamza_worker/
  router.py     # classify(text) -> Route|None   (regex/keyword, NO LLM)
  dispatch.py   # spawn worker subprocess async, build "task started", post summary on complete
  bridge.py     # try_route(text, message, adapter) called by the adapter shim
```
- **router.py:** deterministic regex/keyword → `Route(intent, params)`. Unknown → `None` → falls
  through to the agent (e.g. "what do you think about AI agents?").
- **dispatch.py:** for `lead`/`outreach` → build spec → `Popen` `leadgen.py --spec -` detached →
  return `task_id` immediately; worker posts the Sheet summary to Discord on `task_complete`.
  For `status` → read the task-queue dirs, return counts synchronously.
- **Adapter shim:** 6 lines; `return` when handled. No new framework, no agent council, no
  lead-engine change.

## Risks / open questions (for the Review Board)
1. Editing a bundled framework file (adapter.py) — fragility on framework update. Mitigation:
   externalize all logic; back up; document re-patch.
2. Param parsing without an LLM: "find 100 physiotherapy clinics in India" → `{count:100,
   target:"physiotherapy clinics", location:"India"}` via regex. Edge cases (no count, weird phrasing)
   → conservative defaults or fall through to agent.
3. Async + Discord reply: the worker runs as a detached subprocess; the shim must reply "task started"
   synchronously, then the worker (not the shim) posts completion. Confirm webhook posts to the same
   channel/thread.
4. Auth/safety: route only for allow-listed users (already enforced upstream by on_message gates).
   Outreach stays draft-only (no sends).

---

## Review Board verdict → FINAL DECISION (supersedes the E1 proposal above)

**Technical:** early-return from `_handle_message` does block the agent (✓ viable), but the seam at 4756
precedes auto-threading and a fixed webhook misroutes the summary; spawn must survive gateway restart.
**Integration:** editing the bundled `adapter.py` is wiped on any framework reinstall (CRITICAL);
the only zero-edit native short-circuit is slash-only; the webhook→thread mismatch is real.
**Devil's Advocate:** cut to ONE intent, collapse modules, reuse the worker's existing
queue + poster, fall through aggressively; the `@Hamza` sigil isn't worth editing a 6,633-line vendored file.

**Decision (thinnest design that still meets the literal success criterion `@Hamza find …`):**
1. **Insertion = a user-space hook, NOT a framework-file edit.** Ship `~/.hermes/hooks/hamza_router/`
   (`HOOK.yaml` + `handler.py`). On `gateway:startup` the handler **wraps the live Discord adapter's
   `_handle_message`** (class monkeypatch). Free-text `@Hamza …` is intercepted; **zero edits to any
   framework file** (survives reinstall — kills Integration-F1 + DA's core objection). If the hook
   ever fails to load, behavior simply degrades to today's agent path (safe).
2. **Dispatch = in-process `asyncio.create_task`** running the existing `worker.prime.HermesPrime`
   (reuse, don't rebuild — DA). Reply "on it · task `<id>`" instantly via the **live bot**
   (`message.channel.send`); post the completion summary via the **same live bot to the same channel**
   (fixes the webhook→thread blocker F2/F3; no subprocess/env hazards F4/F5). Worker already uses
   `asyncio.to_thread` for blocking I/O, so the gateway loop is never stalled.
3. **Intents v1 = `lead` (find/scrape) + `status`** (the two Phase-4 cases that map cleanly). Regex:
   grab `count` + treat the niche/geo remainder as target/location; **ambiguous → return None → agent**.
   `task_id = sha1(message.id)` (idempotent against redelivery — F6).
4. **Cut for v1:** knowledge intent, the separate webhook poster, multi-module split, thread-fidelity
   (post to channel). `outreach`/"for row N" deferred (not the core criterion).
5. The worker's lead-gen Groq calls are small per-request (≪12k, with the built-in semaphore+backoff),
   so the bypass path **never** assembles the 29k agent context → no 413.

**Net new code:** one hook dir (`HOOK.yaml` + `handler.py`) + a tiny `router.py`, all under
`~/.hermes/hooks/`, reusing `~/.hermes/hamza_worker/`. No framework edits, no new framework, no
agent council, no lead-engine change.

---

## Implementation + Phase 4/5 Results

**Shipped** (`hooks/hamza_router/`, deployed to `~/.hermes/hooks/hamza_router/`):
- `hamza_intent_router.py` — pure regex classifier, NO LLM. Intents: `lead` (verb+noun), `status`,
  `outreach` (row-level). Ambiguous → None → agent. 13 unit tests, Ruff clean.
- `handler.py` — `gateway:startup`/`session:start` hook that monkeypatches the live
  `DiscordAdapter._handle_message` (signature-agnostic `*args,**kwargs`). Operational → reply via the
  **live bot** + background task (semaphore-bounded, blocking gspread via `asyncio.to_thread`) →
  reuse `worker.prime.HermesPrime`; completion posted to the **same channel**. Verified install:
  `[hamza_router] installed router on DiscordAdapter._handle_message`.

**ECC review applied** (Technical/Security/Integration/Devil's-Advocate): fixed blocking-I/O-on-loop
(→ `to_thread`), unrooted tasks (→ tracked set + done-callback), signature coupling (→ `*args`),
no concurrency cap (→ `Semaphore(2)`), `update_cell` formula-injection (→ `value_input_option=RAW`),
outreach regex false-positives (tightened; "write a message" no longer routes), missing-config
cryptic error (→ precondition). Devil's-Advocate flagged outreach as out-of-v1-scope; kept because
Phase 4 explicitly tests it, but rebuilt as the safe (threaded, RAW, draft-only) version.

**Phase 4 — validation (on the live patched gateway):**
| Case | Result |
|---|---|
| `find 5 physiotherapy clinics in Mumbai` | routed → worker → **5 real leads** (PhysioWorld, Physio & Beyond — real Practo source_urls), 5/5 provenance, 12.9s, **no 413, no agent loop** |
| `find 4 dentists in Delhi` | routed → worker → 4 leads, 4/4 provenance, 22.7s, no 413 |
| `status` | queue counts returned synchronously |
| `outreach for row N` | classifier routes (not agent); reads row → draft_pitch → writes `Personalized Pitch` (RAW, draft-only) |
| conversational `Hey` | **fell through to the agent** (413 — expected; router correctly does NOT intercept chat) |

**Phase 5 — old path vs new path** (same intent, measured on the box):
| Metric | OLD: Discord → agent loop | NEW: Discord → router → worker |
|---|---|---|
| Outcome | **413 failure** + session auto-reset | task completes, real leads written |
| Tokens (agent context) | **29,625 requested** vs 12k TPM limit | **0** (agent never invoked) |
| LLM calls | 1 Groq call → 413 | small bounded worker extraction calls (≪12k each, semaphore+backoff) |
| Wall-clock | ~2.4s **to fail** | ~13–23s **to succeed** (real scraping) |
| Success rate | 0% on substantive turns | 100% on routed lead-gen |

The new path trades a few seconds of real work for a result, where the old path returned an error in
~2.4s. Success criterion met: `@Hamza find … clinics …` routes to the worker, executes, writes the
sheet, and reports — without the 29k-token path.

> Pending only: the literal in-Discord post by the operator for end-to-end sign-off. The transport is
> already proven (Discord delivery HTTP 204 in the prior mission; the live `Hey` fall-through; the
> in-gateway worker runs above). Telegram operational messages still hit the agent (Discord-only
> patch) — a known, accepted scope boundary.
