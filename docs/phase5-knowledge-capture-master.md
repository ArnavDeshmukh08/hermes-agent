# Phase 5 — Master: Founder Knowledge Capture (design)

> Hermes Prime integration of the capture swarm (A opinions · B insights · C objections ·
> D voice · E provenance) + Review Board (Technical APPROVE-WITH-FIXES · Content
> APPROVE-WITH-FIXES · Devil's Advocate: build lean first). **Design only — no code, no deploy,
> no new agents.** Fixes the Phase-4 root cause (input poverty + the open provenance gap) by
> feeding the *existing* Research → CMO → Approval pipeline with real founder substance.
> Component detail: `docs/{founder-opinions-repo,vytal-insights-repo,objections-repo,voice-corpus-spec,provenance-gate-spec}.md`.

## 0. The headline call: design is approved; **sequence it lean-first.**
The full structured layer below is sound and ready to build — **but do not build all of it before any data exists.** The one unvalidated assumption is *supply*: whether a time-poor founder produces and sustains the input (the swarm estimates ~10% sustained past week 2). You don't de-risk an unproven input *supply* by building elaborate input *storage*. So: **Stage 1 (this week) = the leanest capture that tests supply + lifts output; Stage 2 = graduate into the full schema once data flows and the content measurably improves.** Everything in §1–§5 is the Stage-2 target the lean capture grows into.

---

## 1. Repository structures (reconciled)
```
memory/
  knowledge/
    opinions/    <id>.json     beliefs · lessons · predictions · observations · contrarian   (A)
    insights/    <id>.json     pain points · workflow failures · discoveries · pricing · impl (B)
    objections/  <id>.json     objections · concerns · fears · recurring questions            (C)
    notes.md                   STAGE-1 schema-less free-write (one thought/paragraph)          ← lean start
    _inbox/                    raw captures awaiting promotion (draft → active)
  voice/
    samples.jsonl              20–50 real Arnav posts (REPLACES the 2-sample samples.md)        (D)
    style.md                   do/don't rules (kept)
    anti-samples.md            corporate/AI voice to AVOID                                       (D)
  research/
    _opinions.json _insights.json _objections.json   ← PROJECTIONS the CMO already reads
```
- **Canonical CMO-integration (Technical reconciliation): one shared *projector***. Rich records stay under `memory/knowledge/<kind>/`; a single projector emits lean research-run files `memory/research/_<kind>.json` whose findings satisfy `validate_finding`, consumed by the **existing** `load_findings()` → `select_topics()` → `_brief_block()` with **zero change** to selection. This is the only pattern that (a) needs no new CMO selection code and (b) carries `provenance` (as a passthrough key the validators ignore) so the gate can read it. Drop A's `type="hook"` private mapping; the `FINDING_TYPES` "objection" value is optional (one line, only if you want a dedicated objection→reframe template).
- Filesystem markdown/JSON only. **No vector DB / embeddings / RAG / new agents** (confirmed across all 5 specs).

## 2. File contracts (base + per-repo, with Review-Board fixes)
**Base knowledge item:** `{ id, captured_at, type, title, body, tags[], provenance:{kind, source, verifiable, evidence}, status:"active|draft|retired", usage:{consumed, used_in[]}, anecdote? }`
- **FIX (Content, critical): add optional `anecdote`/`scene`** to insights + opinions — "the specific moment, in concrete detail." Narrative is what makes posts land and is what the voice corpus's `story` shape needs raw material for.
- **FIX (Content): collapse the 4 date fields → 2** (`captured_at`, `as_of`); `metric.baseline/window` optional; objection `audience` defaults hard (don't prompt).
- **FIX (Technical, blocking): C must not set `source_url=null`** (violates `validate_finding`'s non-empty rule) — use an internal URI `objection://<id>` (opinions `founder://opinion/<id>`, insights `vytal://insight/<id>`; these pass the URL guard, which only scans generated post text).
- **Opinions** (A): `+opinion_kind{belief,lesson,prediction,observation,contrarian}`, `claim`, `why`, `spice{low,med,high}`. `provenance.kind="founder_stated"`, `verifiable:false` (POV needs no stat).
- **Insights** (B): `+insight_kind{pain_point,workflow_failure,customer_discovery,pricing_learning,implementation,metric}`, `observation`, optional `metric{value,unit,direction,as_of}`, `provenance.kind∈{vytal_usage,customer_discovery,implementation}`. **A number is publishable only if `verifiable:true` + named `source` + `as_of` + matching `evidence`** — else the projector strips the number and it ships as a qualitative observation. Anonymized at capture (handle only; real-name map in gitignored `secrets/clinic-map.json` — DPDP).
- **Objections** (C): `+objection_kind{objection,concern,fear,recurring_question}`, `objection`, **`underlying_concern`** (the real fear beneath the words), **`arnav_response`** (his tested reframe — the moat), `frequency`. `provenance.kind∈{sales_call,demo,customer_question,observed}`.

## 3. Provenance gate specification (the keystone — reconciled)
- **Rule:** a content variant may contain a number/statistic ONLY IF it traces to a brief input with `provenance.verifiable==true` + real `source` + matching `evidence`. Gated = %, counts, money, "1 in 5", measured time spans. **Not** gated = "3 steps", "24/7", years, list counts, ordinals (false-positive guards).
- **Mechanism (deterministic, stdlib, no LLM-judge):** `main()` builds `verified_facts` from the selected inputs (`verifiable==true` only) — **voice samples are excluded**, so the `samples.md` "22%/18 patients" has zero fact-standing and **can never reach the queue** (the keystone, verified to hold). New signature `score_variant(variant, verified_facts)`: extract numeric claims from `variant.text`, normalize, check set membership.
- **FIX (Technical): pin the normalization table** (lakh/crore, `k`/`m` money suffixes, percent word-forms) before build — the only place a *real* verified value could mismatch and produce a false block. Residual risk is over-blocking, not leaking.
- **Default — staged (resolving DA + Content):** **Stage 1 = FLAG-AND-SURFACE** (extract + show the unsourced number on the approval card; don't hard-block) — because against a thin/empty insights corpus a hard block would no-op the entire pipeline and starve the good founder stats. **Stage 2 = HARD-BLOCK** exact unsourced stats, once insights are populated enough to gate real signal. Vague magnitude ("most") → soft flag in both stages.
- **Prompt reinforcement:** a FACTS-DISCIPLINE block in `build_system_prompt`: "use ONLY supplied facts; never invent or recall a number; voice samples show TONE ONLY — any number in them is OFF-LIMITS as a fact; attribute opinions to the founder." Steer toward qualitative phrasing so the gate is a rare backstop, not a frequent post-killer.

## 4. Voice corpus requirements (reconciled — fill this FIRST)
- **Target ≥20 (floor ≥12, healthy 30–50)** authentic Arnav posts across **6 shapes** (stat-hook, story, contrarian, how-to, build-in-public, list, ≥2 each), topic-matched to the CMO's priority tags, length-balanced.
- **Source order:** LinkedIn/X export (primary, biggest one-time win) > past drafts > **his unguarded real WhatsApp/Telegram messages** (gold for idiolect — Content reviewer: prefer these over hand-written sprints) > transcribed voice notes. **Hard rule: NO AI-assisted text and NO prior CMO output** (circular — entrenches the generic voice). This is the single most important rule in the phase.
- **Storage:** `memory/voice/samples.jsonl` (one record/line, append-only, fail-soft load) `{id,text,source,date,shape,topics[],length_chars,performance?,use_as_exemplar}` + `style.md` + `anti-samples.md`.
- **Selection (no embeddings):** score samples by `shape-match (×3) + topic-overlap + length-band` and pick a shape-diverse exemplar set per brief (a *story* brief gets *story* exemplars). **FIX (Technical/D): the trimmer must sacrifice voice LAST** — unified ladder `excerpt → secondary finding → truncate exemplars → anti-sample → exemplar2 (keep ≥1)` — fixing the current "drops sample 2 first" bug. Merge this with the gate/prompt edits into ONE trim ladder.

## 5. One-week data collection plan (lean-first — the actual recommendation)
The goal of week 1 is **not** to fill all five repos — it's to get *real signal into the pipeline* and test whether (a) Arnav sustains capture and (b) output measurably improves. Minimum-viable lift = **~15 voice samples + ~8 insights + ~6 opinions** (objections can lag).

| Day | Action (phone-friendly, "capture exhaust not new work") | Output |
|---|---|---|
| 1 | **LinkedIn + X data export** → drop into `samples.jsonl` (a one-time helper auto-fills shape/topic). Highest ROI, zero proprietary thinking, data already exists. | ~10–20 voice samples |
| 1 | Forward 5–10 of his own substantive past WhatsApp/Telegram messages into the corpus. | +5–10 samples (best idiolect) |
| 2–3 | **`memory/knowledge/notes.md` free-write** (or a dead-simple `/note <text>` capture) — one paragraph per real clinic observation / take, ZERO schema. An offline nightly parser proposes opinion/insight/objection candidates for one-tap confirm (structure = the machine's job). | ~8 insights + ~6 opinions (as candidates) |
| 2–7 | **Post-sales-call voice note** ("what did they push back on, and what's really underneath?") → `_inbox/`. | objections accrue |
| by 5 | Replace the 5–10 `example.com` research sources in `_sources.json` with real URLs. | real research substance |
| by 7 | **Provenance gate in FLAG-ONLY mode** + the trim-voice-last fix wired. | gate live, non-starving |
| 7 | **Measure:** run the pipeline on the real inputs; compare output quality/approval to the Phase-4 baseline (5.4/10, ~5–15%). | go/iterate signal |

**Then (earned, not assumed):** if supply sustains and output measurably improves → graduate the lean capture into the full structured repos (§1–§4) and flip the gate to **hard-block**. If supply doesn't come or output doesn't lift → you've spent a week, not built five shelfware repositories.

## Code-change scope (for the eventual build — small + localized)
`bin/cmo.py` (score_variant signature + `build_verified_facts`/`extract_claims`/`normalize`, JSONL `read_samples` + shape-scored selection, unified trim ladder, FACTS-DISCIPLINE prompt) · `lib/store.py` (NEW `load_knowledge` + the shared `project_knowledge_to_research` projector + `mark_*_used`) · `lib/contracts.py` (optional one-line `"objection"` enum). The **projector is the one net-new component** (owns anonymization + stat-strip + provenance passthrough) — keep it small. `select_topics`/`_brief_block`/`load_findings` stay unchanged.

## Verdict
**Design: APPROVE-WITH-FIXES** (anecdote field; objection URI; merged trim ladder; pinned normalization; staged gate). **Sequencing: LEAN-FIRST** — Stage 1 (voice export + schema-less notes + flag-only gate + real URLs, measure) before Stage 2 (the full structured layer + hard-block), which this phase has fully designed so it's ready to build the moment the data proves it's worth it.
