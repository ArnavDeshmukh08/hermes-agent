# Skills Router — Architecture

> Lightweight, on-demand skill loading for Hermes. Goal: load only the skills a turn
> needs, so the per-turn prompt stays < 12k tok. Design only — implementation is
> approval-gated (`skills/skills-pruner.md` + `/deploy`). Inputs: `reports/skills-audit.md`,
> `registry/skills-registry.md`, `reports/context-bloat-report.md`.

## Problem → target
```
NOW:      msg → [inject all 73 skills ~10.6k] → think → execute     # 413 before work
TARGET:   msg → dispatcher → router → load only matched skills → execute
```

## The good news: the lever already exists in-framework
Hermes does **not** need new routing code. The `hermes-agent` framework already provides
`tools.tool_search` (`enabled: auto`, `search_default_limit: 5`, `max_search_limit: 20`,
`threshold_pct: 10`) — retrieval-based, on-demand surfacing of tools/skills. The fix is to
**use** it (stop static full-hub injection) + **prune** the catalog so the on-demand index
is small. No bespoke router is built; we configure the one that exists. (Realizes **Option B**
— route work around the monolithic agent loop; see `../../docs/ARCHITECTURE-DECISION.md`.)

**Two data caveats the router must respect (verified):**
- `conditions` is **not** a usable gate: present on all 73 skills but an empty stub for
  **71/73** (only `maps`, `research-paper-writing` populated). Route on `tool_search` over
  **name + description + trigger keywords**, not on `conditions`.
- `platforms` is a **weak** filter: 67/73 run on the Linux gateway; only **5 are macOS-only**
  (the `apple` pack). Platform pruning removes 5 skills — the rest of the savings come from
  **disabling off-surface packs** (ARCHIVE) + on-demand routing, not from OS scope.

## Pipeline
```
  Telegram msg
     │
     ▼
  ① Dispatcher — classify intent + surface
     │   surface = chat type (DM or Vytal group → Jack on both)
     │   intent  = keyword/embedding match against registry trigger keywords
     ▼
  ② Skills Router — tool_search over the registry
     │   retrieve top-K (K=3–5) skills above threshold (10%) by name/desc/keywords
     │   drop the 5 macOS-only skills; bias by surface context
     ▼
  ③ Load — always-load core (KEEP) + matched on-demand skills only
     │   core = kanban-orchestrator, kanban-worker, hermes-agent (~0.2k tok)
     ▼
  ④ Execute — agent runs with a lean prompt (< 8k tok total)
     │
     └─ fallback: router matches nothing → proceed with core only (never inject all)
```

## Load tiers (from the registry)
| Tier | When loaded | Members | ~Budget |
|------|-------------|---------|--------:|
| **P0 core** | every turn | KEEP set (kanban ×2, hermes-agent) | ~0.2k |
| **P1 on-demand (high)** | router match | productivity / github / software-dev / research / email | 0 until matched |
| **P2 on-demand (low)** | router match | rest of plausible skills | 0 until matched |
| **P3 archived** | never (pack disabled) | 32 off-surface skills | 0 |

## Config levers (NestJS-free; all in `~/.hermes/config.yaml` + pack install state)
1. **Stop static hub injection** — make the skills hub on-demand rather than always-rendered
   (lean the `skills_hub` auxiliary to retrieval; keep only the KEEP core in the base prompt).
2. **`tools.tool_search`** — keep `enabled: auto`, set a low `search_default_limit` (3–5) and
   keep `threshold_pct` so weak matches don't load. Retrieval keys off name/description/keywords.
3. **Disable ARCHIVE packs** — remove the 32 off-surface skills from the installed/index set
   (re-installable; nothing deleted) so retrieval space is small. **This is the main lever** —
   the savings come from surface-relevance pruning, not platform.
4. **Surface scoping** — load only the active surface's `SOUL.md` section (Jack in DMs vs Jack in the Vytal group).
5. **Platform filter (minor)** — exclude the 5 macOS-only `apple` skills on the Linux gateway.

## Intent → skill-group map (dispatcher hints)
| Intent signal | Surface | Candidate skills |
|---|---|---|
| booking / appointment / clinic / lead / Vytal | Jack (Vytal group) | kanban, (outreach via `~/.hermes/bin` scripts) |
| remind / note / idea / schedule | Jack (DMs) | reminders (script), kanban |
| research / paper / market | either | arxiv, blogwatcher, llm-wiki, polymarket |
| code / PR / repo / debug / deploy | dev-dispatch | github (merged), debugging (merged), plan, tdd |
| docs / sheet / pdf / ocr | either | google-workspace, notion, airtable, ocr-and-documents |
| (no match) | active surface | P0 core only |

## Constraints / non-goals
- **No new code/agents/skills** — configure the framework's existing `tool_search`.
- Must not regress: a turn with no skill match still works (core-only), never re-injects all.
- Heavy/agentic work still routes off Groq (per-job override / local Ollama) — orthogonal to
  this, but compounds the budget win.

## Acceptance criteria
- [ ] A 3-char message (`het`) logs `Requested < 12000` and returns a real reply (no 413/reset).
- [ ] A skill-triggering message loads only the matched skill(s) + core, not all 73.
- [ ] Always-loaded skills overhead < 1k tok; typical turn < 8k tok.
- [ ] Archived packs are disabled, re-installable, and absent from the on-demand index.
- [ ] No surface cross-load (DM-only `SOUL.md` content absent in a Vytal-group turn and vice-versa).

## Validation
Per `skills/skills-pruner.md` + `/deploy`: back up → apply on the box → restart →
`grep "Requested" ~/.hermes/logs/agent.log` shows < 12k → send a routed query and confirm
only the expected skill surfaced. Roll back on any regression.
