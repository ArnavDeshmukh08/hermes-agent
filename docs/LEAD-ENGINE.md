# Hermes Lead Engine — Finalized Architecture + Implementation Roadmap

> **Status:** Design locked (2026-06-17). Ready to code next session.
> **Scope:** Generalize the EXISTING lead pipeline (`~/.hermes/bin/hamza_orchestrator.py`, ~500 LOC)
> from "hardcoded doctors/clinics + fixed 6-field schema" into a **config-driven** one:
> *user asks for leads with arbitrary fields → gets a populated spreadsheet, no code change per task.*
> **Hard boundary (from the mission):** no new memory systems, no new governance, no future phases.
> This is a **refactor of one file**, not a framework.

---

## 0. Verdicts (mission success criteria 1–2)

| Item | Verdict | Evidence |
|---|---|---|
| **CodeGraph** | **REJECTED for this work** | Indexes large repos fine (Vytal App: 408 files/6284 nodes), but the lead-engine code lives **on the VPS** (local MCP can't reach remote files) and is only ~500 LOC (grep/Read suffices). Wrong scale. Keep CodeGraph available for the local Vytal App repo; do not use it here. |
| **ECC** | **MINIMAL profile only** | Installed `rules/ecc/{common,python}` + review agents (planner/code-reviewer/python-reviewer/security-reviewer). These cost **zero context tokens** unless invoked. The full 105-file/18-language stack is bloat — rejected. ECC used here only to drive the review board below. |
| **Recon** | All 6 capabilities **already exist** | `hamza_orchestrator.py` already does Search → Scrape → Extract → Analyze → Write-Sheet → Outreach. The ONLY gap is hardcoding. This is a ~70–85 line generalization, not a build. |

---

## 1. The one architectural decision: config-driven via a JSON task spec

Everything vertical-specific in the current pipeline lives in **three places**: the source URL
templates, the fixed 6-field schema, and the email persona. Lift those into a **task spec** supplied
as JSON. Nothing else changes.

### Entry contract (Integration MUST-FIX M2)
The canonical entry is a **JSON spec**, not an in-pipeline Groq NL-parse:

```bash
hamza_orchestrator.py --spec spec.json     # or: --spec - (stdin)
```

Hermes (the interactive agent, already on Groq) emits the spec as part of its normal turn — so we do
**not** pay for a second in-pipeline Groq call, and we remove a malformed-JSON failure mode. A thin
NL→spec convenience wrapper is **optional** and clearly separate.

### The spec
```jsonc
{
  "target":   "pediatric dentists",   // REQUIRED. if empty -> hard error, refuse
  "location": "Pune",                  // optional, default null
  "sources":  ["practo","generic_web"],// default ["generic_web"]
  "fields":   ["name","clinic","phone","email","website"],  // PLAIN STRINGS (not {name,desc})
  "count":    12,                      // default 12
  "outreach": false,                   // default false; true => draft only, never send
  "sheet_tab":"practo_pediatric_dentists"  // optional, default = slug(target[+source])
}
```

Defaults are applied inline with `dict.get(k, default)` at use sites (no `_normalize_spec` layer —
Devil's Advocate). Persona stays the **3 existing hardcoded constants** ("Arnav at Vytal", "free
15-min discovery call", "max 3 sentences") until a second persona actually exists (DA: cut
`outreach_persona`).

---

## 2. Function-by-function changes (only `hamza_orchestrator.py`)

| Stage | Today (hardcoded) | After (spec-driven) | Δ lines |
|---|---|---|---|
| **Entry** | `parse_command(NL)` → `{count,specialty,city}` | `load_spec(--spec json)`; inline defaults; `if not target: refuse` | +8 / −15 |
| **Search** | `build_directory_urls` + `search_leads_ddg` | `resolve_sources(spec)` over a **2-entry** `SOURCE_BUILDERS` dict | +14 / −30 |
| **Scrape** | `scrape_url(url, 6000)` | **UNCHANGED**; `stealth_scrape` stays the escalation | 0 |
| **Extract** | `extract_leads(text, specialty, city, count)` fixed 6 fields | `extract_leads(text, fields, count)`; prompt built from `fields[]`; **enforce exact key names** | +12 / −15 |
| **Write** | hardcoded 8-col array | one `lead_to_row(lead, fields, draft=None)` + dynamic header + provenance cols | +20 / −20 |
| **Outreach** | `draft_email(lead, ctx)` w/ constants | `draft_email(lead)` (constants kept); only if `outreach=true`; **draft-only** | +4 / −4 |
| **main()** | 6 stages, literals | same 6 stages, reads `spec`; early-exit loop; dedupe | +20 / −15 |

**Net ≈ +78 / −113 ≈ ~80 changed/new lines, one file.** `sheets_agent.py`, `scrape_url`,
`stealth_scrape.py`, `validator_agent.py`, `outbound_dispatcher_agent.py` are **untouched**.

### 2a. Source builders — 2 entries, no dead code
```python
# value = builder(target, location, count) -> [url]
SOURCE_BUILDERS = {
    "practo":      lambda t, l, n: [f"https://www.practo.com/search?q={q(t)}&city={q(l)}"] if l else [],
    "generic_web": lambda t, l, n: ddg_urls(f"{t} {l or ''}".strip(), n),  # wraps existing DDG + BLOCKED_DOMAINS
}
def resolve_sources(spec):
    urls = []
    for key in spec.get("sources", ["generic_web"]):
        b = SOURCE_BUILDERS.get(key, SOURCE_BUILDERS["generic_web"])   # unknown -> fail-closed to DDG
        urls += b(spec["target"], spec.get("location"), spec.get("count", 12))
    return list(dict.fromkeys(urls))     # de-dup URLs across sources (Tech LOW)
```
**`google_maps` and `justdial` do NOT ship day 1** — the source research found both need camoufox
(not Jina-readable); shipping builders that silently return nothing is worse than not shipping them
(DA #9). Add them later *with* the camoufox path, behind a tested flag.

### 2b. Extraction — dynamic fields + exact-name enforcement (fixes Tech HIGH bug 5)
```python
def _fields_prompt(fields):
    keys = ", ".join(f'"{f}"' for f in fields)
    return (f"Return objects using EXACTLY these key names (verbatim, do not rename or paraphrase): {{{keys}}}.\n"
            "NEVER hallucinate names, contacts, or details. If a field is absent on the page, use null.")
```
The verbatim **no-hallucinate clause is preserved** (provenance hard rule). Forcing exact key names in
the prompt is the **zero-cost** fix for the "near-miss key → silent empty cell" data-corruption risk;
a fuzzy `difflib` reconciler is deferred until/unless it actually bites.

### 2c. Row building — ONE function (resolves the C/D duplicate)
```python
PROV_COLS = ["source_url", "fetched_at"]              # ALWAYS injected (Integration M3)

def lead_to_row(lead, fields, draft=None):
    row = [str(lead.get(f) or "") for f in fields]     # missing->"", extra dropped, order preserved
    row += [lead.get("source_url",""), lead.get("fetched_at","")]
    if draft is not None:
        row += [draft, "PENDING REVIEW"]
    return row

def header_for(fields, outreach):
    h = list(fields) + PROV_COLS
    return h + (["draft","status"] if outreach else [])
```
`str(lead.get(f) or "")` does coercion + alignment in one line — kills the separate `_coerce`. No
`_cell` list/dict handling (leads are flat scalars — DA). **No `_guard`**: instead write with
`value_input_option="RAW"` (one gspread setting) — this neutralizes formula-injection from scraped
pages **and** stops Sheets mangling `+91-...` phones, replacing the whole guard (resolves Tech HIGH
bug 3 + DA #6 in one line). `source_url`/`fetched_at` are captured at the **scrape boundary**, never
from the LLM.

### 2d. main() control flow (fixes Tech CRITICAL bug 1 + dedupe)
```python
MAX_URLS, MAX_DRY = 36, 3   # named constants, not magic
def main(spec_path):
    spec = load_spec(spec_path)
    if not spec.get("target"): return refuse("need a target")
    urls = resolve_sources(spec)[:MAX_URLS]
    leads, dry = [], 0
    for url in urls:
        if len(leads) >= spec.get("count",12): break
        text = scrape_url(url) or stealth_scrape(url)
        if not text: continue
        got = extract_leads(text, spec["fields"], spec.get("count",12)-len(leads))
        for L in got: L["source_url"], L["fetched_at"] = url, now_iso()   # provenance at boundary
        if not got: dry += 1
        else: dry = 0
        if dry >= MAX_DRY: break          # <-- stops the TPM-burning runaway loop on zero-yield pages
        leads += got
    leads = dedupe(leads)[:spec.get("count",12)]        # key = first non-empty of phone>email>name
    leads = validator.check(leads)                       # MANDATORY provenance/sanity gate
    tab = spec.get("sheet_tab") or slug(spec)
    add_tab(SHEET_ID, tab)
    if first_write(tab): append_row(SHEET_ID, tab, header_for(spec["fields"], spec["outreach"]))
    rows = [lead_to_row(L, spec["fields"], draft_email(L) if spec.get("outreach") else None) for L in leads]
    append_bulk(SHEET_ID, tab, rows)                      # value_input_option="RAW"
    return summary(tab, len(rows))                        # sends remain manual, downstream
```

---

## 3. Provider routing (flagged disagreement — decided)

Integration M1 invoked the locked rule "heavy generation → local Ollama, Groq 413s" (CONTEXT.md §10).
**Nuance that resolves it:** that 413 was the **26k-token agent loop**. This orchestrator is a
`no_agent` script — a single 6000-char extraction is ~2–3k tokens, which *fits* Groq's 12k-TPM
**per-request** ceiling, and the existing script already runs extraction on Groq and works. The real
risk is **TPM-rate** (per-minute) across `count=12` sequential calls.

**Decision (shortest path, honest):**
- **v1 keeps Groq for extraction/draft** (proven, fits as a no_agent script) **+ add exponential
  backoff** (3 tries) around each call to survive 429s.
- Make the extraction provider a **one-line switch** (`base_url`) so flipping to **local Ollama**
  (no TPM cap, the locked heavy-path fallback) is trivial if 429s appear in practice.
- The lightweight NL→spec parse (optional wrapper only) is the sole remaining Groq touch and is tiny.

This respects the locked architecture (Ollama is the drop-in fallback, one line away) without adding
the Mac-tunnel dependency to the day-1 critical path.

---

## 4. Source recommendation (mission criterion E)

Ranked for free, Jina-first, Indian clinic leads (full table in the review transcript):

1. **Practo** — densest Indian doctor/clinic data; **profile** pages are SSR/Jina-readable. Volume.
2. **Clinic's own website via DuckDuckGo** — the **email** source (directories hide email; clinic
   sites expose it in footer/contact). Pivot: `clinic+city` → DDG → first non-aggregator domain →
   Jina-read `/contact`,`/about` → regex email/`mailto:`.
3. IndiaMART (best *native* email yield, weaker for solo doctors) — later.
4. Justdial / Google Maps — **camoufox-only**, defer.
5. State medical council registries — enrichment/verification only (no contact fields).

**v1 ships:** `practo` + `generic_web` (which covers the clinic-site-via-DDG email path).
**Email strategy:** treat email as a **derived** field from the clinic's own site, never expected
from a directory.

---

## 5. Guardrails preserved (non-negotiable)

- **No auto-send.** Orchestrator writes drafts with status `PENDING REVIEW` and **does not import the
  send function at all** (draft-only module boundary — Integration S3). Sends stay behind
  `outbound_dispatcher_agent.py`'s existing manual gate.
- **No fake data.** Verbatim no-hallucinate clause kept; `source_url`+`fetched_at` injected at the
  scrape boundary on **every** row regardless of `fields[]`; `validator_agent.py` is a **mandatory**
  gate, not optional (Integration M3).
- **DPDP / anti-spam:** provenance per lead; outreach approval-gated; prefer business emails.

---

## 6. Implementation roadmap (next session codes immediately)

All edits in **`~/.hermes/bin/hamza_orchestrator.py`** unless noted. Order:

1. **`load_spec()`** — read `--spec json|-`, inline defaults, refuse on empty `target`. *(~8 lines)*
2. **`SOURCE_BUILDERS` + `resolve_sources()`** — 2 entries, URL de-dup, fail-closed. Relocate the
   existing Practo/DDG bodies into them; delete `build_directory_urls`. *(~14 lines)*
3. **`extract_leads(text, fields, count)`** — `_fields_prompt` from `fields[]`, exact-key
   enforcement, keep no-hallucinate clause. *(~12 lines)*
4. **`lead_to_row` + `header_for` + `PROV_COLS`** — one row builder; delete the hardcoded 8-col
   array and any `_coerce`/`_cell`/`_guard`. *(~12 lines)*
5. **`main()`** — spec-driven flow, capture provenance at scrape, `MAX_DRY` early-exit, `dedupe`
   (phone>email>namekey), mandatory `validator.check`, durable per-source tab, header-once. *(~22 lines)*
6. **`draft_email(lead)`** — drop ctx params, keep constants, only when `outreach`. *(~4 lines)*
7. **Sheet write:** pass `value_input_option="RAW"` to the gspread append (in `sheets_agent.py`
   call-through — confirm the helper forwards it; if not, ~2 lines there — the **only** possible
   non-orchestrator edit). *(~1–2 lines)*
8. **Backoff** wrapper around Groq extraction/draft calls; provider `base_url` switch documented for
   the Ollama fallback. *(~8 lines)*

**Total ≈ 80 lines, one file** (plus a possible 2-line `value_input_option` pass-through).

### First live test (acceptance)
```bash
# spec asks for a NON-default field set to prove "arbitrary fields, no code change"
echo '{"target":"pediatric dentists","location":"Pune","sources":["practo","generic_web"],
       "fields":["name","clinic","phone","email","instagram"],"count":5,"outreach":false}' \
  | hamza_orchestrator.py --spec -
```
**Pass =** a new sheet tab with header `name|clinic|phone|email|instagram|source_url|fetched_at`,
≤5 real rows each tracing to a real `source_url`, empty (not invented) cells where a page lacked the
field, and **zero** emails sent.

---

## 7. What was CUT (Devil's Advocate, accepted)

`outreach_persona` object · `fields[]` as `{name,desc}` (→ plain strings) · `SOURCE_REGISTRY` of 4 ·
`google_maps`/`justdial` day-1 builders · `_normalize_spec` layer · separate `dynamic_columns.py`
file · `_cell` list/dict handling · `_guard` (→ `value_input_option="RAW"`) · `ensure_header`
raise-on-mismatch (→ write-if-empty) · `count*3` over-fetch (→ early-exit loop). Proposed ~165 →
**~80 lines.**

**KEPT against minimalism** (hard rules, not gold-plating): provenance columns + mandatory validator,
the zero-yield early-exit (real CRITICAL bug), and dedupe with a defined key.
