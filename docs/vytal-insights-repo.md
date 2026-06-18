# Vytal Insights Repository — Design Spec (Specialist B)

> **Status:** design only, no code. Phase-5 knowledge-capture.
> **Problem this fixes (Phase-4 verdict):** content is generic because research is
> synthetic — no real stats, `example.com` URLs. Vytal insights are real,
> proprietary, un-Googleable substance (clinic pain points + real outcomes).
> Feeding one verified, provenance-tagged Vytal insight into the existing
> `research.py → cmo.py → approval` pipeline is the single highest-leverage
> input that flips a post from "any SaaS could write this" to credible founder
> insight.
> **Integration stance:** plug into the EXISTING pipeline with minimal change.
> No vector DB, no RAG, no new agents. Insights become **Findings** the current
> `store.load_findings()` already flattens and `cmo.py` already selects.

---

## 0. How this clips onto the existing pipeline (the whole point)

The CMO is already a finding-consumer. It calls `store.load_findings(unconsumed_only=True, days=14)`,
ranks by `_tag_affinity` (priority tags: `retention, no-show, whatsapp, clinic, healthcare, founder`)
then recency, and renders the winner via `_brief_block` (Topic / Summary / Excerpt).

So the cheapest possible integration is: **an insight is stored as one extra
research-run JSON file in `memory/research/`, whose findings already satisfy the
frozen Finding contract.** The CMO needs *zero* code change to consume it — a
real insight just becomes the highest-ranked finding because we tag it with the
priority tags and give it a fresh `run_date`.

We keep a **second, richer record** in `memory/knowledge/insights/` as the
durable source of truth (full provenance, anonymized source detail, audit
trail). A tiny **projection step** (Specialist D / promotion) emits the lean
Finding view into `memory/research/`. The rich record is what the provenance
gate (Specialist E) reads; the Finding is what the CMO reads.

```
memory/knowledge/insights/<id>.json   ← rich, durable, provenance-complete (source of truth)
                │  (projection: insight_to_finding)
                ▼
memory/research/_insights.json        ← lean research-run of Findings the CMO already consumes
                │  store.load_findings() → cmo.select_topics() → _brief_block()
                ▼
content draft (status pending) → approval → approved/
```

Why two files instead of writing straight to `memory/research/`:
- The Finding contract has **no provenance fields**. A laundered stat ("18 patients
  recovered") would land in a Finding with no way to prove it's real. The rich
  record is where `verifiable`, `source_detail`, `evidence`, and the audit live.
- Privacy: the rich record holds the anonymization mapping discipline; only the
  already-anonymized projection reaches the prompt surface.
- Retirement / correction: insights get retired or corrected over time; research
  runs are append-mostly. Separating them keeps the audit clean.

---

## 1. Repository structure

```
memory/
  knowledge/
    insights/
      <id>.json                 # one insight per file (rich record — §2)
      _index.json               # lightweight roll-up: id → {kind, verifiable, status, captured_at, tags}
      _retired/                 # corrected/retired insights moved here, never deleted (audit)
        <id>.json
    README.md                   # capture rules + anonymization rule, human-facing
  research/
    _insights.json              # PROJECTED research-run of Findings (one per active, unconsumed insight)
```

### Naming
`<id>` = `insight_<yyyymmdd>_<kind>_<slug>` using the same IST day + `contracts.slugify`
conventions already in `lib/contracts.py` (so ids sort chronologically and are
filesystem-safe). Examples:
- `insight_20260617_pain_point_front-desk-forgets-followups`
- `insight_20260617_metric_recovered-patients-clinic-a`
- `insight_20260612_pricing_per-seat-rejected`

### Scaling
- One file per insight keeps writes atomic (reuse `store.atomic_write_json`) and
  diffs reviewable. At expected volume (a handful/week) this is fine for years.
- `_index.json` lets the projection + review tooling scan without opening every
  file. Rebuildable from the files, so it's a cache, not a source of truth.
- If `insights/` ever exceeds ~1k files, shard by year (`insights/2026/...`).
  Not needed now (YAGNI) — note it and move on.
- `_retired/` preserves corrected/superseded records for the audit trail. We
  never hard-delete an insight that was ever published.

---

## 2. File contract — the insight record (extends the pinned base item)

Conforms to the pinned base knowledge-item contract and specializes
`provenance.kind ∈ {vytal_usage, customer_discovery, implementation}`.

```jsonc
{
  "id": "insight_20260617_metric_recovered-patients-clinic-a",
  "captured_at": "2026-06-17T13:40:00+05:30",   // IST, contracts.now_iso()
  "type": "vytal_insight",                       // fixed discriminator for this repo

  "insight_kind": "metric",                      // enum — see below
  "title": "Clinic A recovered 18 lapsed patients in 30 days via WhatsApp recalls",
  "observation": "After enabling automated 30/60/90-day recall messages, the clinic re-booked 18 patients who had not visited in >6 months.",
  "body": "Full context: ... what triggered it, what the clinic did before, why it matters.",

  // --- metric (OPTIONAL — present only when there is a real number) ---
  "metric": {
    "value": 18,
    "unit": "patients",
    "direction": "up",                 // up | down | flat — for phrasing ("recovered", "fell")
    "baseline": null,                  // optional prior value, e.g. 22 for a 22%→14% drop
    "window": "30d",                   // the measurement window
    "as_of": "2026-06-16"              // the date the number was read
  },

  // --- provenance (REQUIRED; the stat gate reads this) ---
  "provenance": {
    "kind": "vytal_usage",             // vytal_usage | customer_discovery | implementation
    "source": "Vytal dashboard — Clinic A — recalls report",  // WHICH dashboard/clinic/report
    "source_detail": {
      "channel": "dashboard",          // dashboard | clinic_call | onboarding | support_thread | observation
      "clinic_ref": "clinic-a",        // ANONYMIZED handle (never a real clinic name) — see §2.3
      "captured_on": "2026-06-17",     // date Arnav logged it
      "measured_on": "2026-06-16"      // date the underlying number/event is true as of
    },
    "verifiable": true,                // TRUE only if a real source + real evidence exist
    "evidence": "Dashboard recalls report shows 18 re-bookings tagged 'recall' between 2026-05-17 and 2026-06-16."
  },

  "tags": ["retention", "whatsapp", "clinic", "metric", "case-study"],
  "status": "active",                  // active | draft | retired
  "usage": { "consumed": false, "used_in": [] }   // mirrors Finding.consumed; back-filled on publish
}
```

### 2.1 `insight_kind` enum
Captures the five substance categories from the brief:

| `insight_kind`      | What it captures                                         | Typically has metric? | provenance.kind        |
|---------------------|----------------------------------------------------------|-----------------------|------------------------|
| `pain_point`        | A clinic problem/frustration (qualitative)               | No                    | customer_discovery     |
| `workflow_failure`  | A concrete process that breaks (front desk forgets, etc.)| Sometimes             | customer_discovery / implementation |
| `customer_discovery`| A learning from talking to a clinic (need, objection)    | Rarely                | customer_discovery     |
| `pricing_learning`  | What pricing/packaging worked or got rejected            | Sometimes ($)         | customer_discovery     |
| `implementation`    | Build/ops observation from running Vytal                 | Sometimes             | implementation         |
| `metric`            | A real measured outcome (recovered patients, rate drop)  | **Yes (required)**    | vytal_usage            |

**Rule:** if `insight_kind == "metric"`, the `metric` object **and**
`provenance.verifiable == true` with a real `source` + `evidence` are **mandatory**.
For all other kinds, `metric` is omitted and the record is qualitative.

### 2.2 Field summary (beyond the base contract)
- `insight_kind` — enum above.
- `observation` — what happened, one or two sentences (this is the substance).
- `metric` — `{value, unit, direction, baseline?, window, as_of}` — **optional**;
  required iff a real number is being claimed.
- `provenance.source` — human string naming the dashboard/clinic/report.
- `provenance.source_detail` — structured `{channel, clinic_ref, captured_on, measured_on}`.
- `provenance.verifiable: bool` — the publishable-stat flag.
- `provenance.evidence` — the concrete artifact/number that backs the claim.
- `tags[]` — **must include at least one CMO priority tag** so the projection
  ranks well; include `metric`/`case-study` where apt.

### 2.3 Anonymization (hard project rule — DPDP / privacy)
- **No real clinic name, doctor name, patient name, phone number, or address
  ever enters a record.** Clinics are referenced only by a stable anonymized
  handle (`clinic-a`, `clinic-b`).
- Anonymize **at capture**, not later — the template (§5) only offers the handle,
  never a free-text name field that could leak a real identity.
- The handle→real-clinic mapping is **NOT stored in this repo**. It lives in
  `secrets/clinic-map.json` (gitignored, on the box only), so the knowledge repo
  and anything it projects is privacy-safe by construction.
- Numbers about a clinic are aggregate outcomes (counts, rates), never
  individual patient data.

### 2.4 Three example records

**(a) Pain point — qualitative, no metric, no stat-gate needed**
```jsonc
{
  "id": "insight_20260617_pain_point_front-desk-forgets-followups",
  "captured_at": "2026-06-17T13:42:00+05:30",
  "type": "vytal_insight",
  "insight_kind": "pain_point",
  "title": "Front desks rely on memory for follow-ups, so lapsed patients silently churn",
  "observation": "Clinic staff said no-shows and lapsed patients aren't tracked anywhere — they remember to call 'when it's slow', which never happens.",
  "body": "Came up unprompted on a discovery call. The receptionist owns recalls but has no list; the doctor assumes it's handled. The gap is invisible until revenue dips.",
  "provenance": {
    "kind": "customer_discovery",
    "source": "Discovery call notes — Clinic B",
    "source_detail": { "channel": "clinic_call", "clinic_ref": "clinic-b", "captured_on": "2026-06-17", "measured_on": "2026-06-17" },
    "verifiable": false,
    "evidence": "Paraphrased from a live call; qualitative, no number claimed."
  },
  "tags": ["retention", "no-show", "clinic", "pain-point"],
  "status": "active",
  "usage": { "consumed": false, "used_in": [] }
}
```
No number ⇒ `verifiable:false` is fine ⇒ it is publishable as an *observation*,
never as a statistic. The CMO can write "clinics tell me their front desk tracks
follow-ups from memory" — true, sourced, no fabricated figure.

**(b) Real metric — recovered-patient count with full provenance (publishable stat)**
```jsonc
{
  "id": "insight_20260617_metric_recovered-patients-clinic-a",
  "captured_at": "2026-06-17T13:40:00+05:30",
  "type": "vytal_insight",
  "insight_kind": "metric",
  "title": "Clinic A recovered 18 lapsed patients in 30 days via WhatsApp recalls",
  "observation": "Automated 30/60/90-day recalls re-booked 18 patients who hadn't visited in >6 months.",
  "body": "First clinic with recalls live for a full month. 18 of the re-bookings carry the 'recall' source tag in the dashboard.",
  "metric": { "value": 18, "unit": "patients", "direction": "up", "baseline": null, "window": "30d", "as_of": "2026-06-16" },
  "provenance": {
    "kind": "vytal_usage",
    "source": "Vytal dashboard — Clinic A — recalls report",
    "source_detail": { "channel": "dashboard", "clinic_ref": "clinic-a", "captured_on": "2026-06-17", "measured_on": "2026-06-16" },
    "verifiable": true,
    "evidence": "Recalls report: 18 re-bookings tagged 'recall' between 2026-05-17 and 2026-06-16."
  },
  "tags": ["retention", "whatsapp", "clinic", "metric", "case-study"],
  "status": "active",
  "usage": { "consumed": false, "used_in": [] }
}
```

**(c) Pricing learning — has a money number, qualitative outcome**
```jsonc
{
  "id": "insight_20260612_pricing_per-seat-rejected",
  "captured_at": "2026-06-12T19:05:00+05:30",
  "type": "vytal_insight",
  "insight_kind": "pricing_learning",
  "title": "Per-seat pricing got rejected; clinics want flat per-clinic pricing",
  "observation": "Two clinics balked at per-receptionist pricing — they think in 'per clinic', and seat-counting felt like a tax on adding staff.",
  "body": "Switched the pitch to a flat monthly per-clinic price; both moved forward. Signal: package by clinic, not by seat.",
  "metric": { "value": 2, "unit": "clinics", "direction": "flat", "baseline": null, "window": "n/a", "as_of": "2026-06-12" },
  "provenance": {
    "kind": "customer_discovery",
    "source": "Sales call notes — Clinic A, Clinic C",
    "source_detail": { "channel": "clinic_call", "clinic_ref": "clinic-a,clinic-c", "captured_on": "2026-06-12", "measured_on": "2026-06-12" },
    "verifiable": true,
    "evidence": "Two recorded objections to per-seat; both accepted flat per-clinic in the same calls."
  },
  "tags": ["pricing", "founder", "clinic", "customer-discovery"],
  "status": "active",
  "usage": { "consumed": false, "used_in": [] }
}
```
The "2 clinics" here is a verifiable count of a real event, so `verifiable:true`.
A post may say "two clinics rejected per-seat pricing" — defensible. It must NOT
extrapolate ("most clinics hate per-seat") — that would be a fabricated stat.

---

## 3. The provenance discipline (the heart of the fix)

The risk we are killing is the **laundered stat**: a number that sounds real,
gets into a post, and Arnav can't defend it. The discipline:

**A number is publishable as a statistic only if ALL of these hold in the record:**
1. **The number** is in `metric` (`value` + `unit`), not buried in prose.
2. **The source** is named in `provenance.source` (which Vytal dashboard / clinic
   report / call) — concrete enough to re-open and re-check.
3. **The date** is pinned: `metric.as_of` (when the number is true) and
   `source_detail.measured_on`. A stat without a date is not publishable.
4. **`provenance.verifiable == true`** and `provenance.evidence` describes the
   concrete artifact that backs it (the report row, the count, the tag filter).

If any of (1)–(4) is missing, `verifiable` MUST be `false`, and the projection
(§4) **strips the number from the Finding** — the insight can still ship as a
qualitative observation, just never as a stat. This is exactly what the
provenance gate (Specialist E) enforces before a draft reaches approval: a draft
that asserts a numeric claim whose source insight is not `verifiable:true` is
blocked.

**Contrast — qualitative pain point:** record (a) makes no numeric claim, so
there is nothing to launder. `verifiable:false` is correct and harmless; no
stat-gate is triggered. The CMO presents it as a lived observation ("clinics
tell me…"), which is credible *because* it's sourced to a real call, not because
it carries a number.

**One-line rule of thumb:** *No number without a named source and a date. No
stat in a post without `verifiable:true`.*

---

## 4. How the CMO consumes it (≤6k token budget preserved)

No CMO code change. A projection step (run by promotion / Specialist D, or a
2-line addition to `research.py`) reads active, unconsumed insights and writes
`memory/research/_insights.json` as a normal research-run whose findings obey the
frozen Finding contract. Mapping `insight → Finding`:

| Finding field | Source from insight                                                            |
|---------------|--------------------------------------------------------------------------------|
| `id`          | the insight `id` (already unique, sortable)                                     |
| `type`        | `idea` for pain/discovery/pricing/workflow; `trend` reserved for external — insights are first-person, so default `idea` |
| `topic`       | a short topic derived from `title`/tags                                         |
| `summary`     | `observation`, **with the verified stat inlined** *only if* `verifiable:true` (e.g. "recovered 18 patients in 30 days") |
| `source_url`  | a stable internal ref, e.g. `vytal://insight/<id>` (NOT `example.com`) so the provenance is traceable, not faked |
| `raw_excerpt` | `body` (trimmed to the CMO's `EXCERPT_MAX_CHARS`)                               |
| `tags`        | insight `tags` (carries the priority tags → high `_tag_affinity`)              |
| `consumed`    | `false`                                                                         |

**Why this lands as the chosen post:** `cmo.select_topics` ranks by
`_tag_affinity` (priority tags) then recency. We tag insights with priority tags
(`retention`, `whatsapp`, `clinic`, …) and give the projection a fresh
`run_date`, so a real insight outranks synthetic findings and gets selected as
the **primary**. The existing `_brief_block` then renders Topic/Summary/Excerpt
straight into the lean prompt — no new prompt scaffolding, budget untouched.

**Credible AND defensible:** because the stat is only inlined into `summary` when
`verifiable:true`, every number the CMO ever sees is already backed by a named
source + date in the rich record. The provenance gate (E) cross-checks the draft
against the source insight before approval; on the approval card, the insight
`source` string can be surfaced so Arnav approves with the receipt in hand.

**Consumption hygiene:** when the CMO marks the Finding `consumed`, a post-step
mirrors that back to the insight (`usage.consumed=true`, append the
`content_id` to `usage.used_in`). An insight is projected as a fresh Finding
again only if Arnav explicitly reactivates it (new angle), preventing the same
stat from being recycled into many posts.

---

## 5. Capture method (phone-friendly, <90s, privacy-safe)

Goal: right after a clinic call or a dashboard glance, Arnav logs an insight in
under 90 seconds from his phone, already anonymized.

**Primary path — Telegram/Discord one-liner template.** Send a tagged message;
a tiny parser (reuses `lib/contracts` + `lib/store` patterns) builds the record
and drops it in `memory/knowledge/insights/`:

```
/insight metric clinic-a recalls
Recovered 18 patients in 30 days
src: dashboard recalls report  as_of: 2026-06-16
tags: retention, whatsapp
```
- Line 1: `/insight <kind> <clinic_ref> <topic>` — `clinic_ref` is the anonymized
  handle only; the bot rejects anything that looks like a real name/phone.
- Line 2: the `observation` (and, for a metric, the number + unit).
- Line 3: `src:` → `provenance.source`; `as_of:` → the date.
- For `metric` kind, the bot **requires** `src:` + `as_of:` and sets
  `verifiable:true`; if either is missing it saves the record as a `draft` with
  `verifiable:false` and pings Arnav that "this can't be used as a stat yet."

**Voice path.** A voice note → existing Whisper transcript → same parser. The bot
echoes the parsed record back for a one-tap confirm before it's `active` (so a
mis-transcribed number never becomes a publishable stat).

**Weekly review (the metric harvester).** A Sunday nudge: "Open the Vytal
dashboard — any outcome worth recording?" Arnav skims recalls/retention numbers
and fires 1–3 `/insight metric …` lines. This is the engine that keeps a steady
supply of *real, dated, sourced* numbers flowing into the pipeline.

**Privacy-safe by construction:** the template never has a real-name field; the
handle→clinic mapping stays in `secrets/clinic-map.json` (gitignored). Worst case
a record leaks `clinic-a recovered 18 patients` — no identity, DPDP-safe.

---

## Summary of the contract surface (for the other specialists)
- **Storage:** rich record in `memory/knowledge/insights/<id>.json` (source of
  truth) + lean projection to `memory/research/_insights.json` (what the CMO
  already consumes). No DB, no RAG, no new agent.
- **Schema:** base item + `insight_kind` enum + `observation` + optional `metric`
  + `provenance{source, source_detail, verifiable, evidence}` + `tags[]`.
- **Provenance discipline:** a number is a publishable stat only with
  number+unit, named source, date, and `verifiable:true`; else the projection
  strips the number and it ships as a qualitative observation. This is the gate
  Specialist E enforces.
- **CMO consumption:** projection maps insight→Finding (priority tags + fresh
  run_date → selected as primary), stat inlined into `summary` only when
  `verifiable:true`; ≤6k budget and existing prompt untouched.
- **Capture:** phone `/insight` template (text or voice) <90s, anonymized at
  capture, plus a weekly dashboard-review nudge to harvest real metrics.
```
