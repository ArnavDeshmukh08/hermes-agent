# Voice Corpus Spec — Specialist D (Hermes Prime Phase-5)

> **Design only. No code.** This spec defines the voice corpus that replaces the current
> 2-sample `memory/voice/samples.md`, the file contract, the CMO selection rule, and the
> phone-friendly capture process. It plugs into the EXISTING `bin/cmo.py` pipeline with
> minimal change — no vector DB, no embeddings, no new agents.

**Owner:** Specialist D — Voice Corpus Builder
**Status:** Proposed (P0)
**Last meaningful update:** 2026-06-17

---

## 0. Why this is P0 (the problem in one paragraph)

The MVP (`bin/cmo.py` → `read_samples(k=2)`) reads **only 2 generic example posts**, slices
1–2 of them into a lean `<6k` prompt, and the trimmer **drops sample 2 first** — so any
rich brief runs on **one** sample. The 70B/8B model then imitates that sample's *surface
template*, not Arnav. Phase-4 verdict: voice fidelity is **Low–Med** — output reads as "a
competent generic founder," not "Arnav." Two samples can pick a *genre* (LinkedIn founder
post); they cannot reproduce a *person's idiolect*. Most "revise" verdicts are really the
**"this doesn't sound like me"** reflex. A real corpus of authentic Arnav-written posts is
the single input that moves approval rate the fastest — it gives the model a *distribution*
to imitate (his openers, his rhythm, his tics, his vocabulary) instead of one template.

The current samples are also **not even Arnav's** — they're plausible Vytal-themed examples
written for the MVP. Step one is replacing example text with **authentic, human-written
Arnav text**.

---

## 1. Voice corpus requirements

### 1.1 Minimum size

| Threshold | Count | Meaning |
|-----------|-------|---------|
| Floor (do not ship below) | **≥ 12** | Bare minimum to beat the 2-sample baseline. Enough to span ~3 shapes. |
| **Target (useful)** | **≥ 20** | The number to aim for this week. Below this, shape-matched selection (§4) is starved on at least one shape. |
| Healthy | **30–50** | Good per-shape and per-topic coverage; selection almost always finds a same-shape exemplar. |
| Diminishing returns | **> 60** | More is fine but stops mattering — the prompt only ever shows 1–3 at a time, so curation quality beats raw count. |

**Rule of thumb:** 20 authentic posts > 50 padded/AI-assisted ones. Quality of *authorship*
dominates quantity. The corpus is an exemplar bank, not a training set.

### 1.2 Diversity required (this is what 2 samples cannot give)

The corpus must span **post shapes**, **topics**, and **lengths** so that §4 selection can
hand a *story* brief a *story* exemplar (and not a stat-hook).

**Post shapes** (target ≥ 2 examples each; the 6 the selector keys on):

| `shape` tag | What it is | Why it matters for voice |
|-------------|-----------|--------------------------|
| `stat-hook` | Opens with a number/metric, then unpacks it | Teaches his number framing & punchy openers |
| `story` | Narrative — "we watched a clinic in Pune…" | Teaches his pacing, specificity, anecdote shape |
| `contrarian` | "Everyone thinks X. Wrong." | Teaches his tension setup & take delivery |
| `how-to` | Steps / mechanism explainer | Teaches his instructional rhythm & list voice |
| `build-in-public` | Founder update — shipped / learned / failed | Teaches his candor, "we" voice, honesty about misses |
| `list` | Enumerated insights / lessons | Teaches his line-break cadence & parallelism |

**Topics** (tag freely; selector matches against these; mirror `PRIORITY_TAGS` in cmo.py):
`retention`, `no-show`, `whatsapp`, `clinic`, `healthcare`, `founder`, `building`,
`india`, `sales`, `product`, `college`/`personal` (off-topic-but-his-voice is still useful).

**Lengths** (chars; stay inside cmo's `MIN_LEN=200`..`MAX_LEN=2200` for exemplars):
- Short punchy: ~200–500 (≈ ⅓ of corpus)
- Medium: ~500–1100 (≈ ⅓)
- Long-form: ~1100–2200 (≈ ⅓)

A corpus that is all 1500-char story posts will only ever teach long story voice.

### 1.3 Quality bar (authorship is the spec)

Ranked best → acceptable. Each sample records its `source`; the selector and curator prefer
higher tiers.

1. **Real published posts** (`linkedin`, `x`) — Arnav actually posted them. Best signal:
   public, finished, his real voice, sometimes with engagement data.
2. **His drafts** (`draft`) — wrote but didn't post. Still his voice, minus polish.
3. **Long messages he wrote** (`message`) — substantive WhatsApp/Telegram messages
   (≥ ~200 chars of real opinion, not "ok sounds good"). His unfiltered idiolect.
4. **Transcribed voice notes** (`voicenote`) — how he *talks*; lightly cleaned (remove
   "um", keep word choice). Great for rhythm, weaker for written structure.

**Hard exclusions (never enter the corpus as exemplars):**
- AI-assisted / AI-generated text (including prior CMO outputs). This is circular — the model
  would imitate itself and entrench the generic voice. **Banned.**
- Text written by someone else (forwarded, quoted, co-authored).
- The 2 current placeholder samples (they're examples, not authentic). Retire them.

> If a borderline item is genuinely his thinking but lightly tidied by a tool, mark
> `source: draft` and set `use_as_exemplar: false` — keep it for reference, don't feed it.

---

## 2. Where to source them (realistic for a founder without 20 polished posts)

### 2.1 Primary sources (mine these first)

1. **LinkedIn post history** — Settings → Data Privacy → **Get a copy of your data** →
   *Posts*. Export arrives as a file with post text + dates. Highest-value source.
2. **X / Twitter post history** — Settings → **Download an archive of your data**
   (`tweets.js`), or manually copy standout threads/posts. Threads → join into one sample.
3. **Past drafts** — LinkedIn drafts, Notes app, Google Docs, the repo's own draft folders.
4. **Long WhatsApp/Telegram messages he wrote** — search his sent messages for substantive
   ones (pitches to clinics, explainers to cofounders, opinionated rants). These are gold for
   idiolect because they're unguarded.
5. **Voice notes transcribed** — forward existing voice notes (or record new 60–90s ones)
   through the Hermes Telegram voice layer (Whisper STT) and treat the transcript as a sample.

### 2.2 Realistic fallback — when he has < 20 real posts

A pre-launch founder may have 3–8 real posts. Don't block. Assemble to ≥ 12 (then grow to 20):

- **Curate from messages first** — promote his best long sent messages into `message` samples.
  This usually closes most of the gap without writing anything new.
- **Voice-note batch** — one sitting: he talks through 8–10 takes (one per shape/topic) into
  Telegram; transcripts become `voicenote` samples. ~20 min of talking ≈ 8–10 samples.
- **Write-a-batch sprint (last resort, human-written)** — he hand-writes 5–8 posts across the
  missing shapes. Must be *his* writing, not AI-drafted — the whole point is authenticity.
- **Backfill over time** — every time he actually posts something real, it gets appended
  (see §5). The corpus grows organically and real posts gradually replace fallbacks.

**Priority order to hit ≥ 20 fastest:** published posts → drafts → long messages →
voice-note batch → hand-written batch. Mark fallback-origin samples so they can be swapped
for real posts later (`use_as_exemplar` stays true, but real published posts outrank them in §4).

---

## 3. Storage structure + file contract

### 3.1 Layout (replaces the single `samples.md`)

```
memory/voice/
├── style.md            # KEEP — the do/don't rules (unchanged shape, see §3.4)
├── anti-samples.md     # NEW — corporate/AI voice to AVOID (see §3.3)
├── samples.jsonl       # NEW — the corpus, one JSON object per line (one sample per line)
└── samples.md          # DEPRECATED — retire once samples.jsonl is populated
```

**Chosen format: `samples.jsonl`** (one JSON record per line) over a `samples/` directory of
files. Rationale: append-only capture is trivial (one line per new post — phone-friendly),
it's git-diff-friendly, structured metadata lives next to the text, and the selector can
stream + tag-filter cheaply with stdlib `json` — no new deps, consistent with cmo.py's
"pure stdlib" rule. A per-file directory adds filesystem ceremony for no selection benefit.

### 3.2 Sample record schema (one JSON object per line in `samples.jsonl`)

```jsonc
{
  "id": "ln-2026-03-retention-gap",   // stable slug: <source>-<yyyy-mm>-<short-slug>
  "text": "Most clinics lose patients in the boring gap between visits...",
  "source": "linkedin",               // linkedin | x | draft | message | voicenote
  "date": "2026-03-14",               // ISO date (yyyy-mm-dd); best-effort, "" if unknown
  "shape": "story",                   // stat-hook|story|contrarian|how-to|build-in-public|list
  "topics": ["retention", "clinic", "whatsapp"],   // lowercase tags, match cmo PRIORITY_TAGS
  "length_chars": 412,                // len(text); used for length-band selection
  "performance": { "likes": 37, "comments": 9 },   // OPTIONAL; omit/null if unknown
  "use_as_exemplar": true             // false = keep for reference, never feed to prompt
}
```

**Field rules**
- `id` — stable, unique, human-readable. Never reuse. Used for dedupe + provenance in drafts.
- `text` — verbatim authentic text. For voice notes: lightly cleaned transcript. No AI rewrite.
- `source` — drives the §4 quality tiebreak (published > draft > message > voicenote).
- `shape` — **the primary selection key** in §4. Required. If unsure, best-fit one shape.
- `topics[]` — keyword match against the brief's tags. Lowercase; reuse cmo's tag vocabulary.
- `length_chars` — lets the selector prefer an exemplar whose length matches the desired post.
- `performance` — optional engagement; a light bonus in §4 ranking. Never required.
- `use_as_exemplar` — the on/off switch. `false` excludes from prompts but keeps the record.

**Validation at load (fail-soft, mirrors cmo's defensive style):** skip any line that isn't
valid JSON, is missing `text`, has `len(text) < MIN_LEN` (200) or `> MAX_LEN` (2200), or has
`use_as_exemplar == false`. A malformed corpus must **degrade to the old behavior** (use what
parses), never crash the run — same honesty-about-failure contract as the rest of the pipeline.

### 3.3 Anti-samples (NEW — what to AVOID)

`memory/voice/anti-samples.md` holds 3–6 short snippets of the **corporate / AI / generic
founder** voice the model must NOT produce. These are negative exemplars — the "don't sound
like this" half that `style.md`'s banned-words list can't fully capture. Examples of what to
include (each 1–3 lines):

- LinkedIn-broetry with empty inspiration ("Here's a hard truth nobody tells you 👇…").
- AI tells: "delve", "game-changer", "in today's fast-paced world", "unlock", "leverage
  synergies", "I'm thrilled to announce".
- Hashtag-stuffed, emoji-spammed openers.
- Vague no-specifics claims ("we're revolutionizing healthcare").

The CMO prompt should include a **short anti-sample block** ("AVOID this voice:") alongside
the positive exemplars (~1 anti-sample, tightly capped, see §4.4) so the contrast is explicit.

### 3.4 `style.md` — keep, lightly extend

Keep the existing do/don't rules and `BANNED WORDS` list as-is (cmo bakes the hard rules in
as constants for determinism — `BANNED_WORDS`, `MIN_LEN/MAX_LEN`, `MAX_EMOJIS`). Optionally
add a one-line pointer that positive exemplars live in `samples.jsonl` and negatives in
`anti-samples.md`. No structural change required.

---

## 4. How the CMO selects from a 20–50 corpus

Replaces the current `read_samples(k=2)` "first 2 blocks" logic. **No embeddings** —
deterministic tag/keyword/shape match, consistent with the existing stdlib pipeline.

### 4.1 Inputs the selector already has

In `main()`, cmo selects a `primary` finding with `topic` + `tags`, and the model is asked to
write 2 variants whose **angles** map onto shapes (the prompt already says "contrarian,
story"). So the selector can match the corpus against: the brief's **tags** (→ `topics[]`)
and the **target shapes** for this post.

### 4.2 The selection rule (deterministic, scored)

For each corpus sample with `use_as_exemplar == true`, compute a match score against the
current brief:

```
score(sample, brief) =
      3 * (1 if sample.shape in target_shapes else 0)     # SHAPE match dominates
    + 1 * |sample.topics ∩ brief.tags|                    # topic overlap (count)
    + 0.5 * (1 if length_band(sample) == desired_band else 0)  # length affinity
    + tiebreak_source(sample.source)                       # published>draft>message>voicenote
    + 0.25 * perf_bonus(sample.performance)                # tiny nudge if engagement known
```

- `target_shapes`: the shapes cmo intends for its 2 variants (e.g. derive from the
  brief — default `["contrarian", "story"]`, the angles already in the prompt; or map from
  the primary finding's nature). Shape match is weighted highest because **shape mismatch is
  the loudest "not me" tell** — a story brief answered with stat-hook exemplars reads wrong.
- `length_band(sample)`: short/medium/long per §1.2; `desired_band` from the brief's intended
  post length (default medium).
- `tiebreak_source`: small additive bonus, e.g. `linkedin/x = 0.4, draft = 0.3, message = 0.2,
  voicenote = 0.1` — prefers authentic *published* voice when scores tie.
- `perf_bonus`: e.g. normalized likes, capped — never lets a viral off-shape post outrank a
  same-shape exemplar.

**Pick `k` exemplars** (default `k=2`) as the **top-scored, shape-diverse** set: take the best
sample for each target shape so the 2 variants get matched exemplars (one story, one
contrarian), rather than 2 of the same shape. Break ties by recency (`date`) then `id`
(stable, deterministic).

### 4.3 Fix the "trimmer drops sample 2 first" problem

Today `build_prompt_within_budget` drops sample 2 first, then secondary, then trims excerpt —
so rich briefs run on ONE sample, gutting voice exactly when the post is meatiest. **Reorder
the drop priority so voice exemplars are the *last* thing sacrificed:**

**New trim order (most → least expendable):**
1. Trim the **research excerpt** (`excerpt_chars`: 300 → 120 → 0).
2. Drop the **secondary** finding.
3. **Truncate** exemplars (lower `SAMPLE_MAX_CHARS` 600 → ~350) before dropping any.
4. Drop the **anti-sample** block.
5. Only as a last resort, drop **exemplar 2** (keep ≥ 1 exemplar always).

Rationale: the brief carries *facts* and can be lossily trimmed; the **exemplars carry the
voice** and are the thing Phase-4 says is broken. Voice should be the **last** budget casualty,
not the first. Keep the `<6k` ceiling intact (`TOKEN_CEILING ≈ 5000`) — just change *what*
gets cut first.

> Net effect: a rich brief now keeps **2 shape-matched exemplars + an anti-sample** and trims
> research detail instead, which is the correct trade for voice fidelity.

### 4.4 Prompt assembly (within the existing 6k budget)

The `_samples_block` becomes a **matched-exemplar block**:

```
VOICE EXEMPLARS (match this tone & rhythm, do NOT copy facts):
--- exemplar 1 (shape: story) ---
<text, ≤ SAMPLE_MAX_CHARS>
--- exemplar 2 (shape: contrarian) ---
<text, ≤ SAMPLE_MAX_CHARS>

AVOID this voice (corporate/AI):
<1 anti-sample, ≤ ~200 chars>
```

Labeling each exemplar with its `shape` helps the model align the matching variant's angle to
the matching exemplar. The anti-sample block is small and is the second-to-last thing trimmed.

---

## 5. Capture / curation process (phone-friendly, assemble this week)

Goal: get from **2 placeholder samples → ≥ 20 authentic samples** with the least friction,
mostly from the phone. The corpus is **append-only**: one JSON line per post.

### 5.1 The lowest-friction path (this week)

1. **Paste the exports (desktop, 20 min, biggest win).** Request the LinkedIn + X data
   exports (§2.1). When they arrive, a one-time helper turns each post into a `samples.jsonl`
   line — auto-fills `text`, `date`, `source`, `length_chars`, and *suggests* `shape`/`topics`
   for Arnav to confirm. This alone often gets close to 20.
2. **Forward old posts/messages (phone, ongoing).** Add a Hermes Telegram capture command,
   e.g. `/voice` — Arnav forwards or pastes a past post/long message; Hermes asks two quick
   one-tap questions (which **shape?** which **topics?**), auto-computes the rest, and appends
   one line to `samples.jsonl`. Source defaults by where it came from (forwarded post → its
   platform; typed → `message`).
3. **Record voice notes (phone, fills gaps).** For missing shapes, Arnav sends a 60–90s voice
   note to Hermes; Whisper transcribes (voice layer already in the stack), Hermes light-cleans
   it, asks shape/topics, appends as `source: voicenote`.

### 5.2 Capture contract (what `/voice` collects per item)

Minimum taps to add one sample: **text** (forward/paste/voice) + **shape** (one tap from 6) +
**topics** (multi-tap from the standard tag set). Everything else (`id`, `date`,
`length_chars`, `source`) is auto-derived. `use_as_exemplar` defaults `true`;
`performance` left null unless he pastes numbers. Keep it to ≤ 3 interactions per sample —
friction is the enemy of corpus size.

### 5.3 Ongoing curation (keep it healthy, not a chore)

- **Backfill real posts:** whenever Arnav actually publishes, Hermes nudges "add to voice
  corpus?" → one tap appends it (real published posts gradually replace fallback samples).
- **Coverage check:** a tiny report (also surfaced as a `/voice status` command) counts
  samples per shape and flags thin shapes ("only 1 `how-to` — record one?"). Drives the
  corpus toward the §1.2 balance.
- **Prune circular drift:** never let CMO outputs or AI-assisted text back in (`use_as_exemplar:
  false` or exclude). The corpus stays a human-authored ground truth.
- **Demote, don't delete:** if a sample reads off, set `use_as_exemplar: false` rather than
  removing — preserves provenance and is reversible.

---

## 6. Integration summary (minimal change to cmo.py)

| Area | Today | Change |
|------|-------|--------|
| Corpus file | `samples.md`, 2 generic blocks | `samples.jsonl`, ≥ 20 authentic records (§3) |
| `read_samples()` | first 2 `---` blocks | parse JSONL, filter `use_as_exemplar`, validate fail-soft |
| Selection | first 2 | shape + topic + length scored match to brief (§4.2) |
| Negative voice | none | `anti-samples.md` → 1 anti-sample in prompt (§3.3/§4.4) |
| Trim order | sample2 → secondary → excerpt | **excerpt → secondary → truncate → anti → sample2** (§4.3) |
| Budget | `<6k` (`TOKEN_CEILING≈5000`) | **unchanged** — same ceiling, voice trimmed last |
| Deps | pure stdlib | **unchanged** — stdlib `json`, no embeddings/vector DB |

No new agents, no embeddings, no schema migration beyond the new files. The smallest change
that turns "competent generic founder" into "Arnav."

---

## 7. Acceptance criteria

- [ ] `memory/voice/samples.jsonl` exists with **≥ 20** records, all `source ∈ {linkedin, x,
      draft, message, voicenote}`, none AI-assisted.
- [ ] Shape coverage: **≥ 2** samples for each of the 6 shapes (or a logged gap + plan).
- [ ] Length spread roughly thirds (short / medium / long) per §1.2.
- [ ] `memory/voice/anti-samples.md` exists with 3–6 negative snippets.
- [ ] CMO selects exemplars by shape+topic+length match (not "first 2") and keeps ≥ 1
      exemplar + the anti-sample even on the richest brief, still under the 6k ceiling.
- [ ] Trim order verified: research excerpt is sacrificed before voice exemplars.
- [ ] `/voice` capture path adds a sample in ≤ 3 phone interactions.
- [ ] Phase-5 re-eval: voice fidelity rises from Low–Med toward "sounds like Arnav," and the
      "this doesn't sound like me" share of revise verdicts drops.
