# Founder Opinions Repository — Design

> Specialist A deliverable, Hermes Prime Phase-5 (knowledge capture).
> **Design only. No code in this phase.**
> Plugs into the EXISTING pipeline (`research.py → cmo.py → discord approval → approved/`)
> with the smallest possible change to `bin/cmo.py` and `lib/store.py`.

## 0. Why this exists (the Phase-4 verdict)

Phase-4's verdict: content reads generic because of **input poverty**. The CMO plumbing
(`bin/cmo.py`) is sound — selection, lean prompt, self-scoring, deterministic caps — but it
is fed only research findings, which are facts anyone can Google. A fact produces a **truism**
("reminders reduce no-shows"). A founder's *opinion about* that fact produces an **insight**
("everyone blames patients for no-shows; it's actually the front desk's reminder workflow that's
broken — we measured it").

**Opinions are Arnav's proprietary, un-Googleable edge.** This repository captures that edge as
durable, reusable knowledge items and surfaces ONE of them into every CMO brief, so each post
carries a real point of view instead of a restated statistic.

Critically, per the pinned contract: a founder **opinion needs no stat**. The POV *is* the value.
The provenance gate (`provenance.kind="founder_stated"`) therefore does **not** require evidence —
it only blocks an *unsourced number* that sneaks into the `claim`. The take itself is always allowed.

---

## 1. Repository structure

### Where it lives

```
memory/
  knowledge/
    opinions/
      <opinion-id>.json        # one JSON file per opinion
      _index.json              # optional cache (rebuildable; see §3)
```

`memory/knowledge/` is the shared parent for all Phase-5 knowledge repos (opinions, lessons,
positioning, etc.) so the repos compose under one root and a future `store.load_knowledge(kind=...)`
helper can scan them uniformly.

### Storage choice: one JSON file per opinion (NOT a single `opinions.jsonl`)

**Recommendation: one `<opinion-id>.json` per record.** Rationale, grounded in how the existing
code already behaves:

| Concern | `opinions/<id>.json` (chosen) | single `opinions.jsonl` |
| --- | --- | --- |
| **Mutating one record** (e.g. mark `usage.consumed=true`) | Atomic single-file rewrite via the existing `store.atomic_write_json` (tmp + `os.replace`) — the exact pattern `mark_consumed` already uses per run file. | Must rewrite the whole file to flip one flag → larger blast radius, not how the repo flips `consumed` anywhere today. |
| **Concurrency** | Single-writer-per-file holds naturally (one file = one opinion). Matches store.py's stated assumption. | Append is atomic but in-place mutation of a prior line is not; would need a compaction step. |
| **Consistency with research** | Mirrors `memory/research/<run_id>.json` — same glob-and-load shape `load_findings` already implements. | New pattern the codebase doesn't use for mutable records. |
| **Hand-editing / inspection** | One opinion = one readable file Arnav can open and tweak. | One long file; harder to eyeball. |
| **Scale to dozens** | A few dozen small JSON files glob in <1ms; trivially within scale. | Fine too, but loses the per-record atomicity above. |

`.jsonl` is the right tool for **append-only logs** (which is exactly why `decisions.jsonl` is jsonl).
Opinions are **mutable, addressable records** (status flips, usage tracking, retirement), so they
follow the **research-run/draft** pattern, not the decisions-log pattern.

### Naming

`opinion-id` = `op-<yyyymmdd>-<hhmm>-<slug>` using the existing IST helpers
(reuse `contracts.make_content_id`-style formatting; prefix `op-` to namespace from content ids).
Example: `op-20260617-1402-front-desk-owns-no-shows`.

### How it scales to dozens

- Dozens of files in one flat dir is well within OS + glob limits (the research dir already globs
  the same way). No sharding needed until hundreds.
- Selection cost is bounded by **filtering, not file count**: only `status="active"`,
  `usage.consumed=false` opinions are candidates, and the CMO pulls exactly **one** per run.
- An optional `_index.json` (id → {topics, spice, status, consumed, captured_at}) can be rebuilt
  from the files at any time to make ranking O(index) instead of O(read-all); it is a **cache, never
  the source of truth** (underscore-prefixed so `load_findings`-style globs skip it, exactly like
  `_sources.json`).

---

## 2. File contract — the opinion record schema

Extends the **pinned base knowledge-item contract**. New/specialized fields are marked **★**.

```jsonc
{
  "id": "op-20260617-1402-front-desk-owns-no-shows",
  "captured_at": "2026-06-17T14:02:00+05:30",   // IST ISO-8601 (contracts.now_iso)
  "type": "opinion",                             // fixed: distinguishes from research findings
  "opinion_kind": "observation",                 // ★ enum, see below
  "title": "Front desk owns no-shows, not patients",  // short human label

  "claim": "Clinics blame patients for no-shows. It's the front desk's broken reminder workflow.", // ★ the take, in HIS words
  "why":   "We instrumented 6 clinics. No-shows tracked reminder timing, not patient type. Fix the workflow, the no-shows follow.", // ★ reasoning / the un-Googleable angle
  "evidence_optional": "6 clinics, ~3 weeks of reminder-timing data (internal, not published).",   // ★ MAY be empty; NOT required

  "spice": "med",                                // ★ contrarian heat: "low" | "med" | "high"
  "topics": ["no-show", "front-desk", "workflow"], // ★ matchable tags (maps to finding/CMO tag space)
  "tags": ["clinic", "retention", "ops"],        // base contract tags[] (broader bucketing)

  "body": "Long-form context if voice-note transcript ran long; optional. claim+why is the payload.",

  "provenance": {
    "kind": "founder_stated",                    // gate: POV needs no stat; only an unsourced NUMBER is blocked
    "source": "voice-note 2026-06-17",
    "verifiable": false,                          // opinions are usually NOT independently verifiable — that's fine
    "evidence": ""                               // empty allowed; mirrors evidence_optional when present
  },

  "status": "active",                            // "active" | "draft" | "retired"
  "usage": {
    "consumed": false,                           // mirrors finding.consumed semantics for the CMO
    "used_in": []                                // content_ids of posts this opinion shaped
  }
}
```

### `opinion_kind` enum (the five capture shapes)

| `opinion_kind`   | Captures                                | Example seed prompt to Arnav            |
| ---------------- | --------------------------------------- | --------------------------------------- |
| `belief`         | A held conviction / principle           | "What do you believe about X that most founders don't?" |
| `lesson`         | Something learned the hard way          | "What did building Vytal teach you that surprised you?" |
| `prediction`     | A forward call on the market/tech       | "Where is clinic software heading in 2 years?" |
| `observation`    | A pattern he's noticed in the field     | "What do you keep seeing that nobody talks about?" |
| `contrarian`     | A take that cuts against consensus      | "What does everyone get wrong about Y?" |

`spice` (low/med/high) is **orthogonal** to kind: a `belief` can be high-spice, a `contrarian`
can be med. Spice drives selection bias (§3) so the CMO can prefer sharper takes for a punchier post.

### Conformance to existing validators

- `usage.consumed` is a bool with the **same meaning** as `finding.consumed`, so the CMO's
  "mark consumed after a clean write" discipline transfers unchanged.
- When an opinion is adapted into a pseudo-finding (§3) it is shaped to **pass
  `contracts.validate_finding` as-is** — no validator change required for the happy path.

### Example record 1 — a **belief** (in Arnav's voice)

```json
{
  "id": "op-20260612-0915-retention-not-acquisition",
  "captured_at": "2026-06-12T09:15:00+05:30",
  "type": "opinion",
  "opinion_kind": "belief",
  "title": "Clinics are losing money on retention, not acquisition",
  "claim": "Every clinic I talk to wants more new patients. They're bleeding from the patients they already have and never come back.",
  "why": "A repeat patient costs nothing to acquire and trusts you already. A clinic that recovers 15% of lapsed patients beats one that spends the same on ads — I've watched both happen side by side.",
  "evidence_optional": "Two clinics, same city, same month — the one that texted lapsed patients booked 22% more chairs without spending a rupee on ads.",
  "spice": "med",
  "topics": ["retention", "acquisition", "unit-economics"],
  "tags": ["clinic", "retention", "founder"],
  "body": "",
  "provenance": {
    "kind": "founder_stated",
    "source": "telegram one-liner 2026-06-12",
    "verifiable": false,
    "evidence": ""
  },
  "status": "active",
  "usage": { "consumed": false, "used_in": [] }
}
```

### Example record 2 — a **contrarian prediction** (in Arnav's voice)

```json
{
  "id": "op-20260616-2240-whatsapp-eats-clinic-crm",
  "captured_at": "2026-06-16T22:40:00+05:30",
  "type": "opinion",
  "opinion_kind": "prediction",
  "title": "WhatsApp will quietly eat the clinic CRM",
  "claim": "In two years nobody at an Indian clinic will log into a CRM dashboard. The whole patient relationship lives in WhatsApp, and the dashboard is for the founder, not the front desk.",
  "why": "The front desk already lives in WhatsApp all day. Software that makes them open a second tool loses. The winning product is invisible — it works inside the chat they're already in. Everyone's building dashboards; the dashboard is the legacy artifact.",
  "evidence_optional": "",
  "spice": "high",
  "topics": ["whatsapp", "clinic", "crm", "product"],
  "tags": ["whatsapp", "clinic", "founder", "prediction"],
  "body": "",
  "provenance": {
    "kind": "founder_stated",
    "source": "voice-note 2026-06-16",
    "verifiable": false,
    "evidence": ""
  },
  "status": "active",
  "usage": { "consumed": false, "used_in": [] }
}
```

Note record 2 has **empty `evidence_optional`** — a high-spice prediction with no stat. This is
explicitly allowed: the provenance gate passes because the `claim` carries no unsourced *number*;
the POV alone is the deliverable.

---

## 3. How the CMO consumes an opinion

**Goal:** every brief = **1 research finding (the "what") + 1 founder opinion (the "so what / POV")**,
still under the 6k input-token ceiling.

### The contract mismatch and the recommended fix

`FINDING_TYPES = {"idea","trend","competitor","hook"}` — there is **no `"opinion"` type**, and
`validate_finding` rejects anything else. Two integration routes:

- **Route A (zero-validator-change, "mapped type") — recommended.**
  An adapter converts an opinion into a **pseudo-finding shaped to pass `validate_finding` unchanged**,
  mapping `type → "hook"` (semantically apt: an opinion IS the post's hook/angle). The opinion's
  identity is preserved in a non-validated field so consumption can be tracked back.

  ```text
  opinion → pseudo-finding:
    id          = opinion.id                       (so we can mark the SOURCE opinion consumed)
    type        = "hook"                            (passes FINDING_TYPES, semantically = the angle)
    topic       = opinion.title
    summary     = opinion.claim                      (the take, in his words)
    source_url  = "founder://opinion/" + opinion.id  (non-empty; clearly internal, not web)
    raw_excerpt = opinion.why                        (the reasoning feeds the prompt)
    tags        = opinion.topics + opinion.tags      (so tag-affinity selection can match it)
    consumed    = opinion.usage.consumed
    _opinion    = true                               (adapter marker; ignored by validator)
    _spice      = opinion.spice                      (for prompt emphasis)
  ```

  `source_url="founder://..."` is intentionally non-web; `flag_unallowlisted_urls` only scans
  generated post text, so this internal URI never trips the link guard.

- **Route B (small CMO change, "read `memory/knowledge/`").**
  Add `store.load_opinions(...)` + a tiny CMO step that loads opinions natively (no type-mapping)
  and a dedicated `_opinion_block()` in the prompt. Cleaner long-term, but touches more of
  `bin/cmo.py`. Defer to Phase-6 unless Route A's `"hook"` mapping pollutes ranking.

**Recommendation: ship Route A.** It is the **minimal change** the brief demands. The only CMO edit
is: load opinions, adapt the chosen one to a pseudo-finding, and add a short POV line to the prompt.

### Selection — surface exactly ONE opinion per brief

A new `select_opinion(opinions, primary_finding)` (mirrors the existing `select_topics` shape):

1. **Candidates:** `status=="active"` and `usage.consumed==false`.
2. **Relevance:** rank by overlap between `opinion.topics ∪ tags` and the **primary finding's tags**
   (reuse the `_tag_affinity` idea), so the POV is on-topic for the post.
3. **Tie-breakers:** higher `spice` (sharper take wins), then more recent `captured_at`.
4. **Fallback:** if nothing overlaps the finding's tags, take the highest-spice active opinion
   (a strong general POV still beats a bare fact). If there are **zero** active opinions, the CMO
   behaves exactly as today (finding-only) — pure no-op, no regression.

### Prompt integration — one extra block, still < 6k tokens

The opinion enters as a short, capped block appended to the existing `_brief_block`:

```text
FOUNDER POV (anchor the post on THIS take — it's the edge, not a fact to cite):
Take: <opinion.claim>            # capped ~SUMMARY_MAX_CHARS (400)
Why:  <opinion.why>              # capped ~EXCERPT_MAX_CHARS (300)
```

Plus one system-prompt line: *"Lead with the FOUNDER POV. The research brief is supporting evidence;
the POV is the spine of the post."*

**Token budget (1 finding + 1 opinion fits):** the POV block costs ~700 chars ≈ ~180 tokens. The
existing `build_prompt_within_budget` already trims in the order **sample2 → secondary finding →
excerpt**. We **insert the POV ahead of the secondary finding** in priority (POV is higher-value than
a second fact), and add the POV's `why` to the same progressive-trim ladder if the estimate exceeds
`TOKEN_CEILING` (5000, under the 6k requirement). Net effect: a brief is now **1 finding + 1 opinion**
by dropping the rarely-pivotal secondary finding first — same ceiling, higher signal.

### Marking consumed (symmetry with findings)

After a **clean draft write only** (same discipline as `store.mark_consumed`):
- set the source opinion's `usage.consumed = true` and append the new `content_id` to
  `usage.used_in` via an atomic single-file rewrite (new `store.mark_opinion_used(id, content_id)`).
- If generation/write fails, **nothing is marked** — identical honesty-about-failure guarantee
  the CMO already enforces for findings.

Retirement is `status="retired"` (kept for provenance/audit, never re-selected) — distinct from
`consumed` (used at least once, but a strong evergreen opinion MAY be re-eligible later by resetting
`consumed`; that policy decision is left to a Phase-6 freshness rule).

---

## 4. Capture method — phone-friendly, < 60 seconds

**Principle: capture *exhaust*, not new work.** Arnav already has opinions mid-conversation; the job
is to catch them with near-zero friction, then let an offline step shape them into records. He never
hand-writes JSON.

### Path A — Discord/Telegram one-liner (fastest, ~10s)

A capture command in the existing bot:

```
/opinion <kind?> <spice?> | <the take in your own words>
/opinion contrarian high | everyone builds dashboards; the front desk lives in WhatsApp, dashboards are dead
```

- `kind` and `spice` are **optional** — omit them and an offline classifier infers `opinion_kind`
  and a default `spice="med"` from the text.
- The handler writes a **`status="draft"`** opinion (only `claim` filled). Drafts are **not** selected
  by the CMO until promoted, so a half-formed thought never reaches a post.

### Path B — voice note → transcript template (~30–60s, the primary path)

Reuses the existing voice memory lane (`memory/voice/`) and Whisper STT:

1. Arnav sends a voice note: *"Here's a take — clinics think no-shows are the patient's fault, it's
   actually the front desk's reminder workflow, we measured it across six clinics."*
2. Transcribe, then fill a fixed **slot template** (an LLM "exhaust-shaper", not a creative step):

   ```
   TITLE:  <≤8-word label>
   CLAIM:  <his take, lightly cleaned, first person, his words kept>
   WHY:    <his reasoning, verbatim where possible>
   KIND:   <belief|lesson|prediction|observation|contrarian>
   SPICE:  <low|med|high>
   TOPICS: <2–4 tags>
   EVIDENCE_OPTIONAL: <any number/anecdote he said, else empty>
   ```
3. Write `status="draft"`. A one-tap Discord confirm ("looks right? ✅ / ✏️ edit") promotes to
   `status="active"`. **No stat is invented** — if he didn't say a number, `evidence_optional` stays empty.

### Path C — weekly batch (catch-up, low pressure)

A scheduled weekly Discord nudge rotating ONE seed prompt from the `opinion_kind` table
(e.g. "What did this week teach you about Vytal?"). One voice note → one (or more) draft opinions.
This steadily fills the repo from reflection Arnav is doing anyway, without a daily chore.

All three paths converge on the **same draft → confirm → active** flow, so the only human action is
**talk for 30 seconds and tap ✅** — comfortably under 60s.

---

## 5. Quality lift — which content axes this moves, and why

The CMO scores variants on `hook / clarity / voice_match / cta`. Opinions move the axes that
**input poverty was starving**:

| Axis | Moved? | Why |
| --- | --- | --- |
| **Originality** (the Phase-4 gap) | **★ Most** | The opinion's `claim`/`why` is un-Googleable founder substance. It flips a post from "reminders cut no-shows" (truism) to "the front desk owns no-shows" (insight). This is the headline fix for the generic-content problem. |
| **`hook`** | **High** | High-`spice` contrarian takes are scroll-stoppers by construction; leading with the POV (not a stat) directly raises the hook sub-score the CMO already rewards. |
| **`voice_match`** | **High** | `claim` is captured *in Arnav's words*; feeding his phrasing into the brief makes generated text sound like him, not corporate. Reinforces the existing `memory/voice/` signal with topical, opinionated raw material. |
| **conversion** | **Medium** | A defensible POV invites agreement/disagreement → comments and DMs (the CMO's `cta` axis), and positions Vytal as a company with a *thesis*, which is what converts skeptical clinic owners over time. |
| **`clarity`** | Neutral/slight | One sharp POV per post enforces "one idea per post" (already a voice rule); risk of rambling is contained by capping `claim`/`why` and keeping the research finding as supporting evidence, not a competing thread. |

**Bottom line:** opinions are the single highest-leverage input for the originality problem Phase-4
diagnosed, and they ride the *existing* scoring machinery — no new scorer, no new agent, no RAG.

---

## RETURN — summary

- **Path:** `/Users/arnav/Documents/Hermes Agent/docs/founder-opinions-repo.md`
- **Storage choice:** **one JSON file per opinion** under `memory/knowledge/opinions/<id>.json`
  (NOT `opinions.jsonl`). Opinions are mutable, addressable records (status + usage flips), so they
  follow the research-run/draft per-file pattern and reuse `store.atomic_write_json` for atomic
  single-record mutation. `.jsonl` is reserved for append-only logs like `decisions.jsonl`.
- **Schema headline:** extends the pinned base knowledge-item contract with
  `opinion_kind ∈ {belief, lesson, prediction, observation, contrarian}`, plus `claim` (the take in his
  words), `why` (reasoning), `evidence_optional` (MAY be empty), `spice ∈ {low,med,high}`, and
  `topics[]`. `provenance.kind="founder_stated"`, `verifiable:false` is normal — **no stat required;
  the POV is the value.**
- **How the CMO uses an opinion:** select **one** active, unconsumed opinion whose `topics`/`tags`
  overlap the primary finding (tie-break by spice, then recency); adapt it into a **pseudo-finding
  mapped to `type="hook"`** so `validate_finding` passes **with no validator change** (Route A,
  minimal-change); inject a short capped **FOUNDER POV** block ahead of the secondary finding in the
  prompt so each brief = **1 finding + 1 opinion under the 6k ceiling**; mark the opinion
  `usage.consumed=true` + append `content_id` to `usage.used_in` **only after a clean write**.
- **<60s capture:** **voice note → Whisper → fixed slot template → one-tap ✅ promote** (primary), plus
  a `/opinion ... | <take>` one-liner and a weekly batch nudge. All converge on **draft → confirm →
  active**; human effort = talk 30s, tap confirm. "Capture exhaust, not new work."
