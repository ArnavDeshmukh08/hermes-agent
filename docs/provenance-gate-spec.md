# Provenance Gate — Hard Specification

> **Status:** DESIGN ONLY (no code). Specialist E — Provenance Guardrail.
> **Gate class:** Hard pre-condition. The Phase-4 Review Board made this a
> **non-negotiable blocker for any deploy** of the Phase-5 knowledge-capture path.
> **Owner subsystem:** Stream C — CMO Agent (`bin/cmo.py` → `score_variant()`).
> **Project rule enforced:** *zero fake / placeholder data* (CLAUDE.md hard rules;
> healthcare-founder brand/credibility risk).

---

## 0. The defect this gate closes (verified Phase 4)

`bin/cmo.py::score_variant()` checks **form only**:

- banned words (`thrilled`, `delve`, `game-changer`) → caps `voice_match`
- length `200..2200` → caps `clarity`
- emoji count `> 2` → caps `voice_match`
- missing CTA → caps `cta`
- non-allowlisted URLs → `flagged_links` (surfaced, not blocking)

There is **no substance / provenance check**. Separately, the only real number
in the whole system — `~22%` of slots and `18 "lost" patients` — lives **only**
in `memory/voice/samples.md`, which `read_samples()` injects into the prompt as
a **VOICE SAMPLE** ("match this tone, do NOT copy"). It is *not* a research
finding and carries *no* provenance.

**The laundering path:** the LLM reads "22% of slots" / "18 patients" in a voice
sample, re-emits it as a *factual* claim in a variant, and that variant passes
**every** existing guard (right length, no banned words, has a CTA, no bad URL).
A fabricated-sounding stat reaches the approval queue, where only a fatigued
human stands between it and a healthcare founder's public feed. That is the exact
integrity risk this gate removes.

---

## 1. The rule (precise)

> **A content variant MAY contain a number / statistic / quantified claim ONLY
> IF that number traces to a supplied brief input whose provenance is
> `verifiable == true` with a real `source` and an `evidence` value the number
> matches. Any quantified claim that does not so trace causes the variant to be
> BLOCKED from the approval queue — not down-scored, blocked.**

This is a **hard binary gate**, not a weight. The existing guards cap a sub-score
downward; a variant with a capped score can still be the top variant and still
reach the queue. The provenance gate is different: an unsourced stat makes the
variant **ineligible**, regardless of its composite score.

### 1.1 What counts as a "number / quantified claim" (gated)

A token/phrase is a **quantified claim** if it asserts a magnitude, count, share,
rate, money amount, or time span as a matter of fact. Concretely:

| Category | Examples that ARE claims |
|---|---|
| Percentages | `22%`, `18 percent`, `a fifth`, `half of` (when factual), `1 in 5`, `2x`, `3×`, `doubled`, `tripled` |
| Counts | `18 patients`, `200 clinics`, `47 no-shows`, `thousands of appointments` (when stated as measured) |
| Money | `₹50,000`, `$2M`, `2 lakh`, `50k in revenue` |
| Time spans (as measured outcomes) | `in 3 weeks we recovered…`, `within 30 days`, `cut wait time by 12 minutes` |
| Vague magnitude quantifiers used factually | `most`, `the majority`, `nearly all`, `1 in 5` |

### 1.2 What is NOT a claim (NOT gated — avoid false positives)

These must pass untouched. The gate must be conservative here: a false block is
itself a failure (it silently strips good content and erodes trust in the gate).

| Pattern | Why it's allowed |
|---|---|
| Rhetorical/structural numbers | `3 steps`, `2 things I learned`, `one WhatsApp nudge`, `first / second / third` |
| Operational descriptors | `24/7`, `one-tap reschedule`, `version 2`, `Q3` |
| Ordinals & list counts | `the first time`, `my third startup` |
| Hyphenated idioms | `one-size-fits-all`, `day-one`, `zero-to-one` |
| Years / dates / handles | `2026`, `@vytal`, phone-like strings (also URL-guarded separately) |
| Numbers **inside** an allowlisted URL | already handled by URL guard; not a stat |

**Boundary heuristic (the discriminator):** a number is a *factual stat* when it
quantifies an **outcome, population, or measurement about the world** ("22% of
slots", "18 patients recovered"). It is *rhetorical/structural* when it organizes
the prose or names a thing ("3 steps", "one nudge", "24/7"). When the extractor
cannot confidently classify, see §1.3.

### 1.3 The "vague magnitude" boundary — `most`, `nearly all`, `1 in 5`

These are quantified claims (they assert a real-world proportion) but they have
no exact `evidence` value to string-match against. **Recommendation: FLAG, do not
hard-block.** Hard-blocking every "most clinics…" would gut legitimate founder
voice and produce false positives. Treat them as a separate, softer tier:

- **Exact stats** (`22%`, `18 patients`, `₹50k`) → **hard gate** (§1.1, block if unsourced).
- **Vague magnitude** (`most`, `nearly all`, `the majority`, `1 in 5`) →
  **soft flag** `VAGUE_MAGNITUDE` surfaced to Arnav, variant still eligible.
  Rationale: rounded rhetorical phrasing is normal founder voice and carries far
  less fabrication risk than a precise fake number; but Arnav should still *see* it.

---

## 2. The mechanism (deterministic — NO LLM judge)

The gate is a new **deterministic guard** inside the `score_variant()` path. No
embeddings, no second LLM call, no semantic similarity. Pure string/number
extraction and set-membership against the brief's verified evidence. This keeps
it auditable, reproducible under `HERMES_LLM_MOCK=1`, and immune to the very
model whose fabrications it is policing.

### 2.1 New data the scorer needs: the verified-facts set

Today `score_variant(raw_variant)` receives **only the variant** — it has no view
of the brief, so it *cannot* check provenance. The required change is purely
additive to the call signature / data flow:

```
score_variant(raw_variant, verified_facts)   # new 2nd arg (design)
```

`verified_facts` is a **set of normalized numeric/claim tokens** derived from the
brief inputs *that passed the provenance bar*. The CMO builds it once per run, in
`main()`, from the selected inputs — and **only** from inputs where
`provenance.verifiable == true` AND `provenance.source` is a real non-empty value.

```
verified_facts = build_verified_facts(selected_inputs)
  for each input (finding / Phase-5 insight) in the brief:
      prov = input.get("provenance")
      if not prov: continue                         # legacy/no-provenance → NOT a fact source
      if prov.get("verifiable") is not True: continue
      if not _is_nonempty_str(prov.get("source")): continue
      evidence = prov.get("evidence")               # the actual numbers behind the claim
      verified_facts |= extract_numeric_tokens(evidence)   # normalized forms
```

**Critical exclusion:** voice samples (`read_samples()` / `memory/voice/*`) are
**NOT** an input to `build_verified_facts`. They are style material, never a fact
source. This is what structurally re-blocks the "22%/18 patients" leak (§5.4).

### 2.2 Claim extraction from the variant

```
extract_claims(variant.text) -> list[Claim]
  - tokenize/regex-scan text for numeric & magnitude patterns (§1.1)
  - DROP non-claims per the allow-list (§1.2): ordinals, "N steps",
    "24/7", years, list counts, numbers inside URLs
  - classify each survivor as EXACT or VAGUE_MAGNITUDE (§1.3)
  - normalize each EXACT claim (see §2.4)
```

### 2.3 The check

```
for claim in extract_claims(variant.text):
    if claim.tier == VAGUE_MAGNITUDE:
        variant.flags.append(("VAGUE_MAGNITUDE", claim.raw))   # soft
        continue
    if normalize(claim) not in verified_facts:
        variant.provenance_fail = True
        variant.unsourced_claims.append(claim.raw)             # hard
# a variant with provenance_fail=True is BLOCKED downstream (§3)
```

### 2.4 Matching strategy (string/number, no embeddings)

Match is on **normalized numeric value**, not raw substring, to avoid trivially
defeating the gate by reformatting:

- strip surrounding punctuation/whitespace; lowercase words
- normalize `%` ↔ `percent`; `18 patients` → numeric `18` + unit `patients`
- collapse separators: `₹50,000` / `50k` / `50000` → one canonical money token
- compare the variant's normalized numeric token against the set of normalized
  numeric tokens extracted from every verified `evidence`
- a claim **passes** iff its normalized value is present in `verified_facts`

This is exact-value membership. A real `22%` evidence value admits a `22%` claim
(and `22 percent`); it does **not** admit `21%`, `~20%`, or a freshly-invented
`30%`. No fuzzy/semantic match — that would reintroduce an LLM-judge-shaped
failure mode.

### 2.5 Data flow summary

```
research findings + (Phase-5) founder insights
        │  each carries provenance:{kind, source, verifiable, evidence}
        ▼
select_topics() / Phase-5 selection  ──►  selected_inputs (the brief)
        │
        ├──►  build_prompt_within_budget()  ──►  LLM  ──►  raw_variants
        │
        └──►  build_verified_facts(selected_inputs)  ──►  verified_facts  (set)
                                                              │
raw_variants ──►  score_variant(v, verified_facts) ──────────┘
                       │
                       ├─ form guards (existing)        → cap sub-scores
                       └─ PROVENANCE GATE (new)          → provenance_fail / flags
                                                              │
                                                              ▼
                                  drop blocked variants BEFORE the approval queue
```

The single load-bearing change to the existing code path: `main()` must build
`verified_facts` from the **same selected inputs** it already passes to the
prompt builder, and thread it into `score_variant`. Everything else is additive.

---

## 3. Behavior on failure — BLOCK (recommended default)

Two options were on the table:

- **(A) Block** — drop the variant so it never reaches the approval queue.
- **(B) Flag** — surface it tagged `UNSOURCED STAT — do not publish`.

### 3.1 Recommendation: **BLOCK by default** for exact unsourced stats

A fabricated precise stat in a healthcare founder's feed is a *credibility/brand*
risk, and the project rule is **zero fake data**. The safe default is to make it
**structurally impossible to surface** an unsourced exact stat — do not rely on a
tired human to veto it in the queue. So:

- Variant with `provenance_fail == True` (≥1 unsourced **exact** claim) →
  **excluded from `scored`** before sorting / before the draft is written. It is
  never offered for approval.
- If **all** variants fail the gate → **clean no-op**, consistent with the
  agent's existing honesty contract: print a clear reason, mark **nothing**
  consumed, write **no** draft, exit non-fatally. (Mirrors the existing
  "no eligible research" and "llm returned no variants" no-op paths.)

### 3.2 Observability — never silently drop

Blocking must be **loud**, not silent (silent dropping hides a real signal that
research is starving the pipeline — see MEMORY obs 1332). On every block:

- log per-variant: `provenance_block idx=<i> unsourced=<["22%","18 patients"]>`
- include a run-level counter in the one-line summary, e.g.
  `… | provenance_blocked=2 | …`
- the draft envelope records `provenance_blocked_count` + the blocked claim
  strings for audit (so a starved/leaky run is visible after the fact).

### 3.3 The soft tier still reaches the human — flagged

Surviving variants that carry a **`VAGUE_MAGNITUDE`** flag (or any flagged-but-not-
blocked signal) are eligible, but the **approval message must surface the flag
explicitly** so Arnav decides with eyes open. The approval card should render, per
variant, a line like:

```
⚠ CHECK CLAIMS: "most clinics" (vague magnitude — verify before publishing)
```

If a future policy chooses option (B) for a given claim class, that variant must
likewise carry a prominent, non-collapsible:

```
🚫 UNSOURCED STAT — DO NOT PUBLISH: "30% of clinics"
```

But the **default for exact unsourced stats is block (A)**, so that line should
normally never appear — the variant is gone before approval.

---

## 4. Prompt-side reinforcement (defense in depth)

The gate is the *structural* guarantee; the prompt is the *first* line that
reduces how often the gate has to fire. `build_system_prompt()` in `bin/cmo.py`
must carry an explicit, unambiguous facts-discipline block. The current
`_VOICE_RULES` only says *"Never invent stats or quotes; only use facts from the
brief."* — strengthen it to name the laundering path and the opinion/fact split:

> **FACTS DISCIPLINE (hard rule):**
> Use ONLY numbers, statistics, percentages, counts, money amounts, and time
> spans that appear in the RESEARCH BRIEF's supplied facts. NEVER invent, guess,
> round, or recall a number from memory or from the voice samples. The voice
> samples show TONE ONLY — any number in them is OFF-LIMITS as a fact; do not
> reuse it. If you have no verified number for a point, make the point
> qualitatively without a number. Attribute opinions and predictions to the
> founder ("I think…", "my bet is…"), and never dress an opinion up as a measured
> statistic. A post with zero numbers is preferable to a post with one invented
> number.

This makes the model's intended behavior explicit; the §2 gate then *enforces* it
deterministically regardless of whether the model complies.

---

## 5. Edge cases

### 5.1 Real stat from a verified insight → PASSES (and should cite/imply source)
A Phase-5 insight `{provenance:{kind:"measured", source:"Pune derm clinic pilot,
Mar 2026", verifiable:true, evidence:["22% of slots","18 patients"]}}`. Its
numbers enter `verified_facts`. A variant saying "we cut no-shows on ~22% of
slots" → normalized `22%` ∈ `verified_facts` → **passes**. The post should
*cite or imply the source* ("in our Pune pilot…") — recommend the prompt nudge
the model to ground the number in its source so the published claim is defensible.

### 5.2 Opinion with no number → PASSES
`{provenance:{kind:"founder_stated", verifiable:false, evidence:null}}`,
text: "Retention is a systems problem, not a charisma problem." No quantified
claim → `extract_claims` returns nothing → gate is a no-op → **passes**. Opinions
are explicitly **not** subject to the stat gate (an opinion is `founder_stated`
POV with no number). `verifiable:false` is fine *because there is no number to
verify*; the gate only fires on numbers.

### 5.3 Vague "most clinics" → FLAG, not block (recommended)
Per §1.3: `most` is a magnitude quantifier with no exact evidence to match →
tier `VAGUE_MAGNITUDE` → **soft flag**, variant stays eligible, flag surfaced in
the approval card. Not hard-blocked, to avoid gutting natural founder voice and
to keep false positives near zero.

### 5.4 The voice-sample stat leaking again → NOW BLOCKED (state explicitly)
This is the keystone case. `memory/voice/samples.md` contains `~22% of slots` and
`18 "lost" patients`. `read_samples()` injects them as VOICE SAMPLES. **Voice
samples are deliberately NOT part of `build_verified_facts` (§2.1).** So if no
*research finding / verified insight* in the brief supplies `22%` or `18`, then
those numbers are **absent from `verified_facts`**. A variant that re-emits "22%
of slots" as a fact → `normalize("22%") ∉ verified_facts` → `provenance_fail` →
**BLOCKED**. The laundering path is closed by construction: a number's presence in
a style sample grants it **zero** standing as a fact. (If the same number *also*
exists as a verified finding, it passes — but on the strength of the finding, not
the sample.)

### 5.5 Number inside an allowlisted URL → not a claim
e.g. a campaign link `vytal.app/q3-2026`. The `2026`/`q3` are inside a URL token;
the extractor skips URL-internal numbers (§1.2). URLs are already governed by the
separate `flag_unallowlisted_urls` guard.

### 5.6 Legacy findings without a `provenance` block → treated as NON-verified
Today's `validate_finding` has **no** `provenance` field; existing findings
predate Phase 5. A finding with no `provenance` (or `verifiable != true`, or empty
`source`) contributes **nothing** to `verified_facts`. Consequence: during the
transition, any number must come from a *Phase-5-provenanced* input or the variant
is blocked. This is the safe direction (fail closed), and it also pressures the
research stage to attach real provenance instead of shipping substance-free
findings (MEMORY obs 1332).

---

## 6. Why this is the keystone

Every other guard in `score_variant()` is a **down-scorer**: it nudges a number
and a bad variant can still win and still surface. The integrity of the *only real
claim category in the product* currently rests on **a fatigued human catching a
laundered stat in the approval queue** — a control the Phase-4 board correctly
judged unacceptable for a healthcare founder's public feed.

This gate changes the failure mode from **detection** to **prevention**:

- It is **deterministic** (no LLM judging an LLM), so it is auditable and stable
  under `HERMES_LLM_MOCK=1`.
- It is **fail-closed**: unknown provenance contributes no facts, so the default
  is "block the number," not "trust the number."
- It **severs the laundering channel** structurally — voice samples carry zero
  fact-standing, so a stat that exists only in a sample can never be published.
- It converts the project's hard rule ("zero fake data") from an aspiration that
  *depends on a tired human* into a property that is **structurally impossible to
  violate at the surface**: an unsourced exact stat cannot reach the approval
  queue at all.

That conversion — from human-vigilance to structural-impossibility — is exactly
the non-negotiable pre-condition the Review Board demanded, which is why this gate
is the keystone of the Phase-5 deploy.

---

## 7. Implementation checklist (for the build phase — not built here)

- [ ] Extend the knowledge-item / finding contract with
      `provenance:{kind, source, verifiable:bool, evidence}` and validate it.
- [ ] `build_verified_facts(selected_inputs)` — facts ONLY from inputs with
      `verifiable==true` + real `source`; **exclude voice samples**.
- [ ] `extract_claims(text)` — numeric/magnitude scan + §1.2 allow-list +
      EXACT vs VAGUE_MAGNITUDE classification.
- [ ] `normalize()` for numeric/percent/money/count tokens.
- [ ] Thread `verified_facts` into `score_variant(raw_variant, verified_facts)`.
- [ ] Block variants with `provenance_fail`; all-fail → clean no-op (no consume).
- [ ] Loud logging + `provenance_blocked` counter in the one-line summary + draft
      audit fields.
- [ ] Strengthen `build_system_prompt()` with the §4 FACTS DISCIPLINE block.
- [ ] Approval card surfaces `VAGUE_MAGNITUDE` flags (and any flagged claims).
- [ ] Tests (deterministic, mock LLM): laundered `22%`/`18 patients` blocked;
      verified-insight stat passes; opinion passes; "3 steps"/"24/7"/years pass;
      "most clinics" flagged-not-blocked; all-fail → no draft, nothing consumed.
