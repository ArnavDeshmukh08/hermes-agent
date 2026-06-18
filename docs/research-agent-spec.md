# Research Agent Spec — Stage 1 of the Content Pipeline (Phase-1)

> **Owner:** Agent A (Research Agent Architect). **Design only — no code, no infra optimization.**
> Stage 1 of the content pipeline defined in [workflow-spec.md](./workflow-spec.md):
> `research_agent.py ──► research/*.json ──► cmo.py ──► content/*.json ──► dispatch.py ──► Telegram`.
> Runs on the **deterministic path** (`no_agent` cron + standalone script), **never through the agent loop**.
> Grounded in [ARCHITECTURE.md](./ARCHITECTURE.md), [ARCHITECTURE-DECISION.md](./ARCHITECTURE-DECISION.md), [ROADMAP.md](./ROADMAP.md).

> **Naming note:** Agent E's workflow-spec refers to this script as `research.py`. The canonical
> filename for this mission is **`~/.hermes/bin/research_agent.py`**. They are the **same component** —
> `research.py` is an accepted alias/symlink. All contracts below match the Finding contract Agent E consumes.

---

## 1. Purpose & pipeline position

The Research Agent is **stage 1** (the harvester). Once per night it collects raw market
signal for Vytal's content engine across four buckets:

- **idea** — content ideas / angles worth posting about.
- **trend** — LinkedIn / industry trends gaining traction.
- **competitor** — competitor moves, launches, positioning, posts.
- **hook** — proven hooks, headlines, and copy frameworks to reuse.

It **scrapes → lightly summarizes → writes immutable Finding JSONs** to `memory/research/`.
It does **not** generate content, does **not** touch `memory/content/` or `memory/approved/`,
and does **not** set any status (Findings are immutable facts with no lifecycle — per workflow-spec §2).
Its only consumer is the **CMO Agent** (`cmo.py`), which reads new Findings and drafts content.

**Single-writer rule:** `research_agent.py` is the **only** writer to `memory/research/`.

```
cron 02:00 IST  ──►  research_agent.py  ──writes──►  memory/research/<yyyymmdd>_<slug>.json   (array of Findings)
(no_agent job)        scrape + lean summarize          + optional .md human summary
                              │
                              └─ chained ──►  cmo.py  (consumes new Findings by id)
```

---

## 2. Inputs

### 2.1 Source config — `memory/research/_sources.json`

A single, hand-editable JSON config. Lives **next to** the run outputs so the whole research
domain is one folder. Loaded fresh on every run (edit it, next night picks it up — no redeploy).
The leading `_` keeps it sorted away from dated run files and excluded from run globs (`<yyyymmdd>_*`).

```json
{
  "version": 1,
  "updated_at": "2026-06-16",
  "run": {
    "max_findings_per_run": 40,
    "max_findings_per_source": 8,
    "per_source_timeout_sec": 25,
    "summarize": "ollama",          // "ollama" (preferred, no TPM cap) | "groq" | "none" (raw excerpt only)
    "summary_model": "llama3.1:8b",
    "min_excerpt_chars": 120
  },
  "topics": [
    "patient retention",
    "clinic no-shows",
    "healthcare SaaS marketing",
    "dental practice growth"
  ],
  "keywords_required": ["clinic", "patient", "retention", "appointment", "practice"],
  "keywords_blocked": ["casino", "crypto airdrop"],
  "competitors": [
    { "name": "Solutionreach", "url": "https://www.solutionreach.com/blog", "type": "competitor" },
    { "name": "Weave",         "url": "https://www.getweave.com/blog/",      "type": "competitor" },
    { "name": "NexHealth",     "url": "https://www.nexhealth.com/resources", "type": "competitor" }
  ],
  "sources": [
    { "name": "LinkedIn — healthcare-marketing trend tag", "url": "https://www.linkedin.com/...", "type": "trend",  "scraper": "stealth" },
    { "name": "SaaS growth blog",                          "url": "https://example-blog.com/feed", "type": "idea",  "scraper": "fetch" },
    { "name": "Copywriting hooks library",                 "url": "https://example-hooks.com/",     "type": "hook",  "scraper": "fetch" }
  ]
}
```

**Field meaning**
- `topics` / `keywords_required` / `keywords_blocked` — relevance gate. A scraped item is kept
  only if it matches ≥1 required keyword/topic (case-insensitive substring) and **no** blocked
  keyword. This is the no-LLM relevance filter (cheap, deterministic).
- `competitors[]` and `sources[]` — each entry carries an explicit `type` that becomes the
  Finding `type`, and a `scraper` hint: `"fetch"` (plain web fetch backend) or `"stealth"`
  (route through existing `~/.hermes/bin/stealth_scrape.py` for JS/anti-bot sites like LinkedIn).
- `run.summarize` — controls the only LLM step. **`ollama` is the default** (local, no 12k TPM
  cap, per ROADMAP §7). `groq` is fallback for short batches. `none` skips the LLM entirely and
  stores the trimmed raw excerpt as the summary (fully deterministic, zero tokens).

**Boundary validation (per coding-style "validate at system boundaries"):** on load, the script
validates `_sources.json` against the schema above. If the file is missing or malformed, it does
**not** crash — it writes a `failures` note (see §6) and exits cleanly with an empty run.

### 2.2 Implicit inputs
- Existing run files in `memory/research/` — read **only** to compute deterministic Finding ids
  for idempotent overwrite (a re-run of the same night overwrites, never duplicates — workflow-spec §226).
- Existing assets: `~/.hermes/bin/stealth_scrape.py` (anti-bot scraping), the web-fetch tool
  backend, and Ollama `llama3.1:8b` at the configured `base_url` (Mac tunnel).

---

## 3. Outputs

### 3.1 Primary — research run JSON (the contract)
`memory/research/<yyyymmdd>_<topic-slug>.json` — a **JSON array of Finding objects**.

- One file **per run** (one night = one or more files). Grouping: by **run date + dominant
  topic slug**. Default behavior is **one file per night** named for the run date and the
  highest-yield topic, e.g. `20260616_patient-retention.json`. (If a run spans clearly distinct
  topics, the script MAY emit one file per topic slug for the same date — all share `run_date`.)
- The array is the unit the CMO consumes; CMO tracks a cursor over Finding `id`s it has consumed.

### 3.2 Optional — human summary `.md`
`memory/research/<yyyymmdd>_<topic-slug>.md` — a short, skimmable digest (counts per type +
top 5 findings as bullet links). For Arnav's eyes; **not** consumed by any script. Cheap to
generate from the same data, no extra LLM call.

---

## 4. Storage format

### 4.1 Finding contract (exact shape — matches workflow-spec A→B row)

```json
{
  "id": "a1b2c3d4",
  "run_date": "2026-06-16",
  "type": "competitor",
  "topic": "patient retention",
  "summary": "Weave shipped automated recall reminders; positions around 'fewer no-shows, less front-desk work'.",
  "source_url": "https://www.getweave.com/blog/automated-recall-reminders",
  "raw_excerpt": "Our new recall automation sends...",
  "tags": ["recall", "no-shows", "automation", "competitor:weave"]
}
```

| Field | Type | Rule |
|---|---|---|
| `id` | string | **Deterministic** = first 8 hex of `sha1(source_url + "|" + topic)`. Guarantees idempotent overwrite + stable cross-stage reference. |
| `run_date` | string | `YYYY-MM-DD` of the run. |
| `type` | enum | `"idea"` \| `"trend"` \| `"competitor"` \| `"hook"` — taken from the source's `type`. |
| `topic` | string | The matched topic from `_sources.json.topics`. |
| `summary` | string | ≤ ~280 chars. Ollama one-liner, OR trimmed `raw_excerpt` when `summarize:"none"`. |
| `source_url` | string | **MANDATORY, real, fetched URL.** A Finding with no real `source_url` is **discarded** (guardrail: real data only — no fabricated competitor data). |
| `raw_excerpt` | string | Verbatim scraped text slice (≥ `min_excerpt_chars`), the evidence behind `summary`. |
| `tags` | string[] | Lowercase keywords + `competitor:<name>` / `source:<name>` provenance tag. |

**Guardrail enforcement:** `source_url` is the spine of the real-data rule. The script asserts a
non-empty, scheme-`http(s)` URL that was actually fetched this run. Any candidate missing it is
dropped before write. The LLM summarizer is **never** asked to invent facts — it only condenses
the already-scraped `raw_excerpt`; the prompt forbids adding claims not present in the excerpt.

### 4.2 Run grouping & naming
- **File** = `memory/research/<yyyymmdd>_<topic-slug>.json` (slug = lowercase, hyphenated topic).
- **Run** = all Finding files sharing one `run_date`. CMO de-dups by consumed `id`s, so re-running
  a night overwrites the same filenames and yields no duplicate downstream drafts.

---

## 5. Scheduling mechanism

### 5.1 The cron job (`~/.hermes/cron/jobs.json`)
A framework **`no_agent`** job (zero agent-loop tokens) runs the standalone script overnight.
This is stage 1 of the chained nightly pipeline already defined in workflow-spec §3 — research is
the **first command** in that chain:

```jsonc
// ~/.hermes/cron/jobs.json  (research is step 1 of the nightly chain)
{
  "jobs": [
    {
      "id": "nightly-research",
      "no_agent": true,
      "schedule": "0 2 * * *",                      // 02:00 IST daily, overnight
      "command": "~/.hermes/bin/research_agent.py",
      "provider": "ollama",                         // per-job routing; summarizer is local
      "model": "llama3.1:8b",
      "base_url": "http://<mac-tunnel>:11434",      // Ollama tunnel (ROADMAP §7)
      "timeout_sec": 600,
      "on_output": "telegram"                       // no_agent delivery of the script's stdout note
    }
    // chained steps cmo.py, dispatch.py follow per workflow-spec §3
    // (Agent E owns the full chain registration; this block defines step 1 only)
  ]
}
```

The proven `remind.py` pattern applies: **the script produces output; the `no_agent` cron
delivers that output to Telegram.** Here the script's stdout is a one-line run summary
(see §6) that the cron relays to Arnav.

### 5.2 LLM leanness (HARD CONSTRAINT compliance)
- **Scrape + relevance filter = zero LLM** (deterministic keyword match).
- **Summarize = lean, batched, local.** One short prompt **per finding** (or small batch),
  input = a single `raw_excerpt` slice + a fixed instruction. Input stays **well under 6k**;
  default route is **Ollama** (no TPM cap), so batch summarizing 40 findings is safe.
- If `summarize:"groq"` is chosen, the script **chunks** so each call is < 6k input and respects
  the 12k TPM cap (sleeps between calls); on the first 429 it falls back to Ollama, then to
  `"none"` (raw excerpt) — never blocks the run.
- The agent gateway loop (~17k tok/turn) is **never** invoked. This is a standalone script.

---

## 6. Failure handling

Principle (per project CLAUDE.md): **be honest about failures — surface broken steps, don't fake
success, don't crash.** The script is resilient per-source and always finishes the run.

| Failure | Behavior | What gets written |
|---|---|---|
| **Source unreachable / timeout** | Skip that source after `per_source_timeout_sec`; **1 retry** with backoff, then give up on it. Other sources continue. | Source logged to the run's `failures` list; its Findings simply absent. |
| **Rate-limited (Groq 429 on summarize)** | Fall back: Groq → Ollama → `"none"` (raw excerpt). Run never stalls on the LLM. | Findings still written, summaries may be raw excerpts; note records the downgrade. |
| **Empty results from a source** | Not an error — source yielded nothing relevant after the keyword gate. | Counted in note as `0 findings`; no file penalty. |
| **Whole run empty** | Clean no-op night (matches workflow-spec §232). | **No** Finding files written; a heartbeat note "no findings tonight" goes to Telegram. |
| **Partial run** (some sources ok, some failed) | Write the Findings that succeeded; report failures. | Finding file(s) written for good sources + `failures` note. |
| **`source_url` missing on a candidate** | **Discard** the candidate (real-data guardrail). | Not written; counted as `dropped_no_url` in the note. |
| **`_sources.json` missing/malformed** | No crash. Exit clean, empty run. | Note: "research config missing/invalid — skipped." |
| **Re-run same night** | Deterministic ids → same filenames overwritten; no duplicates. | Idempotent overwrite. |

**How failures surface (no crash, via the `no_agent` delivery):** the script's **last stdout line**
is a compact run summary that the cron relays to Telegram, e.g.:

```
🔎 Research 2026-06-16: 31 findings (idea 9 / trend 7 / competitor 11 / hook 4)
   sources 7/9 ok · 2 unreachable (LinkedIn-trend, SaaS-blog) · summarizer=ollama · 3 dropped(no url)
   → memory/research/20260616_patient-retention.json
```

A machine-readable `failures[]` (source name, reason, http status/exception) is also appended into
the run output's metadata trailer (or a sibling `<yyyymmdd>_run.log`) so downstream and Arnav can
audit. Failures **reduce** the night's findings; they never abort the pipeline — `cmo.py` proceeds
on whatever Findings exist.

---

## 7. Boundaries (single-responsibility, per workflow-spec §273)

- **A (`research_agent.py`) writes `memory/research/` only.** Never `content/`, never `approved/`,
  never any status, never sends approval-gated messages.
- Its **only LLM use** is lean local summarization of already-scraped text; it invents nothing.
- Its **only output to a human** is the no-agent Telegram run note (informational, not gated).
- Everything is **filesystem memory** (markdown + JSON). **No vector DB / embeddings / RAG.**
  Retrieval downstream = glob `memory/research/<date>_*.json` + JSON filter.

---

## 8. One example finding (copy-ready)

```json
[
  {
    "id": "7f3a9c21",
    "run_date": "2026-06-16",
    "type": "hook",
    "topic": "clinic no-shows",
    "summary": "Hook framing that converts: lead with the cost of the problem ('Every no-show costs a clinic ~$200') before the fix.",
    "source_url": "https://example-hooks.com/healthcare/cost-of-inaction",
    "raw_excerpt": "The strongest opening lines quantify the pain first. 'Every no-show costs a clinic roughly $200 in lost chair time' outperformed feature-led openers by 3x in our tests...",
    "tags": ["hook", "cost-of-inaction", "no-shows", "source:copywriting-hooks-library"]
  }
]
```
