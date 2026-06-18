# CMO Agent — Specification

> **Agent B deliverable** for Hermes Prime Phase-1 swarm.
> Stage 2 of the content pipeline: **research → CMO → approval**.
> Status: design only. Implementable by one developer in `~/.hermes/bin/cmo_agent.py`.

---

## 0. TL;DR

The CMO Agent is a **standalone Python script** (`bin/cmo_agent.py`) that runs after a
research run. It:

1. Reads recent, unused research findings from `memory/research/*.json`.
2. Reads the user's voice guide (`memory/voice/style.md`) + 1–2 voice samples (`memory/voice/samples.md`).
3. Picks **one topic** for today, builds a **lean prompt (< 6k input tokens)**, and calls the LLM **once**.
4. Gets back **N LinkedIn variants** (default 3), each **self-scored** in the same call on hook / clarity / voice_match / cta.
5. Writes one content draft JSON to `memory/content/<content-id>.json` with `status:"draft"`.
6. Marks the consumed research finding(s) so they aren't reused.

**Hard constraint honored:** it calls the LLM **directly** (Groq `llama-3.3-70b-versatile`,
or local Ollama `llama3.1:8b` fallback) — **NOT** through the agent gateway loop. No
~17k-token gateway overhead, no 413s. Lean prompt, single call, deterministic post-processing.

---

## 1. Purpose & Pipeline Position

### 1.1 What it does
The CMO ("Chief Marketing Officer") Agent turns raw research findings into
**publishable LinkedIn content in Jack's voice**, with multiple angles and a
recorded quality score per variant — so the approval stage (and Arnav) can pick
the best one with one tap instead of writing from scratch.

### 1.2 Pipeline position

```
┌────────────┐     ┌────────────────┐     ┌──────────────┐
│  RESEARCH  │ ──▶ │  CMO (stage 2) │ ──▶ │   APPROVAL   │
│  (stage 1) │     │   THIS AGENT   │     │  (stage 3)   │
└────────────┘     └────────────────┘     └──────────────┘
 writes              reads research          reads draft,
 memory/research/    + voice,                 one-tap pick +
 *.json              writes                   edit/send via
                     memory/content/          Telegram
                     <id>.json (status=draft)
```

- **Upstream (stage 1):** A research script populates `memory/research/*.json`.
  The CMO does not trigger research; it is **chained after** a research run
  (a `no_agent` cron, or `research.py && cmo_agent.py`).
- **Downstream (stage 3):** The approval flow reads `status:"draft"` drafts and
  presents variants in Telegram. The CMO **never sends** anything (guardrail:
  sends are approval-gated). It only produces drafts.

### 1.3 Non-goals
- Does not post to LinkedIn. Does not DM/outreach. Does not schedule itself.
- Does not do research or web fetches.
- Does not use a vector DB, embeddings, or RAG. **Filesystem markdown + JSON only.**

---

## 2. Inputs

### 2.1 Research findings — selection logic

Reads all `memory/research/*.json`. Each finding is assumed (per Agent D's memory
spec) to carry at least:

```jsonc
{
  "id": "20260615-0900-glp1-retention",
  "created_at": "2026-06-15T09:00:00Z",
  "title": "...",
  "summary": "...",            // 1–3 sentences
  "key_points": ["...", "..."],// bullet facts
  "tags": ["vytal","retention","healthcare"],
  "source_url": "...",
  "consumed_by": []            // content-ids that already used this finding
}
```

**Topic-pick algorithm (deterministic, no LLM needed):**

1. **Filter unused:** drop findings whose `consumed_by` is non-empty.
2. **Filter recency:** keep findings with `created_at` within the last **14 days**
   (`RECENCY_DAYS = 14`). If that empties the pool, relax to 30, then to all.
3. **Tag affinity rank:** score each remaining finding by overlap with a small
   **priority-tag list** (`["vytal","retention","healthcare","founder","ai"]`).
   `+2` per priority-tag hit, `+1` for any tag.
4. **Tie-break:** newer `created_at` wins.
5. **Pick top 1** as the primary topic. (Config `TOPICS_PER_RUN = 1`; a future
   bump to 2 simply loops the pipeline, one LLM call per topic — never batch
   multiple topics into one prompt, to protect the token budget.)
6. Optionally attach **up to 1 secondary finding** sharing ≥1 tag, used only as a
   supporting stat — its `summary` is truncated hard (see §4.3).

If **no** finding qualifies (empty research dir, all consumed): exit cleanly,
log `"no eligible research, skipping"`, write nothing. (Honest-about-failure rule.)

### 2.2 Voice guide + samples

- `memory/voice/style.md` — the condensed tone rules (mirror of `USER.md` +
  `SOUL.md`). The script reads it but **only injects a pre-condensed slice**
  (see §4.2), not the whole file.
- `memory/voice/samples.md` — a list of real past posts/snippets in Jack's voice.
  The script picks **1–2 samples** (shortest-first that are still ≥ 200 chars,
  or tag-matched to the topic if samples are tagged) and truncates each to
  `SAMPLE_MAX_CHARS = 600`.

### 2.3 Brand / persona (constants in the script, not files to parse at runtime)

```python
PERSONA = "jack"
PLATFORM = "linkedin"
BANNED_WORDS = ["thrilled", "delve", "game-changer"]
VOICE_RULES = (
    "Authentic technical-founder voice. Direct, concrete, specific. "
    "Minimal emojis. No fabricated data or stats. "
    "Never use the words: thrilled, delve, game-changer."
)
```

These are baked in so the prompt stays lean and deterministic even if `style.md`
is sparse.

---

## 3. Outputs

One file per run: `memory/content/<content-id>.json`, conforming to the **canonical
content-draft contract**:

```jsonc
{
  "id": "20260616-0930-glp1-retention",      // <yyyymmdd>-<hhmm>-<shortslug>
  "created_at": "2026-06-16T09:30:00Z",
  "source_research_ids": ["20260615-0900-glp1-retention"],
  "persona": "jack",
  "platform": "linkedin",
  "variants": [
    {
      "idx": 0,
      "text": "...",
      "angle": "contrarian-take",
      "score": 84,                            // composite 0–100
      "score_breakdown": {                    // each 0–5
        "hook": 4, "clarity": 5, "voice_match": 4, "cta": 4
      }
    }
    // ... N variants
  ],
  "status": "draft",
  "approval": {}                              // filled by stage 3, empty here
}
```

- **`id`** = `<yyyymmdd>-<hhmm>-<shortslug>`. `shortslug` is derived from the
  primary research finding's tags/title (lowercased, hyphenated, ≤ 24 chars).
- **N** = `VARIANTS = 3` by default.
- **`angle`** is a short label the LLM assigns per variant (e.g. `contrarian-take`,
  `story`, `data-point`, `how-to`, `lesson-learned`). The script provides a
  suggested angle list in the prompt to keep variants distinct.
- `status` is **always `"draft"`** on write. `approval` is always `{}`.

---

## 4. Prompt Architecture (LEAN, < 6k input tokens)

### 4.1 Design principle

One LLM call. Two messages: a **fixed system prompt** (CMO role + condensed voice
rules, ~400 tokens) and a **compact user prompt** (one research brief + 1–2 trimmed
voice samples + the JSON output instruction). **Selection + truncation**, never
"dump everything," is what keeps it under budget.

### 4.2 System prompt skeleton (static, ~350–450 tokens)

```
You are Jack, a technical startup founder writing LinkedIn posts.

VOICE:
- Authentic technical-founder voice. Direct, concrete, specific.
- Minimal emojis (0–1 max). Short paragraphs. Real, not corporate.
- NO fabricated data, numbers, or quotes. Only use facts from the brief.
- BANNED WORDS (never use): thrilled, delve, game-changer.

TASK:
Given ONE research brief, write {N} distinct LinkedIn post variants.
Each variant uses a DIFFERENT angle from: contrarian-take, story,
data-point, how-to, lesson-learned.

Then SELF-SCORE each variant 0–5 on:
- hook       : does line 1 stop the scroll?
- clarity    : is the point obvious in one read?
- voice_match: does it sound like the voice + samples, not corporate?
- cta        : is there a clear, non-salesy invitation to engage?

OUTPUT: strict JSON only, no prose, matching this schema:
{"variants":[{"idx":0,"text":"...","angle":"...","scores":{"hook":0,"clarity":0,"voice_match":0,"cta":0}}]}
```

### 4.3 User prompt skeleton (dynamic, ~1.5–3k tokens, hard-capped)

```
RESEARCH BRIEF (use only these facts):
Topic: {primary.title}
Summary: {primary.summary[:400]}
Key points:
- {primary.key_points[0][:160]}
- {primary.key_points[1][:160]}
- {primary.key_points[2][:160]}        # max 4 points
Supporting (optional): {secondary.summary[:200]}
Source: {primary.source_url}            # for grounding, not for the post text

VOICE SAMPLES (match this tone, do NOT copy):
--- sample 1 ---
{sample1[:600]}
--- sample 2 ---
{sample2[:600]}                          # sample 2 optional

Write {N} variants now. JSON only.
```

### 4.4 How it stays under 6k input tokens

The budget is enforced by construction, then asserted:

| Block                         | Cap                          | ~tokens |
|-------------------------------|------------------------------|---------|
| System prompt                 | static                       | ~450    |
| Primary summary               | `[:400]` chars               | ~120    |
| Key points (max 4)            | `[:160]` chars each          | ~200    |
| Secondary summary (optional)  | `[:200]` chars               | ~60     |
| Voice sample 1                | `SAMPLE_MAX_CHARS = 600`     | ~180    |
| Voice sample 2 (optional)     | `[:600]`                     | ~180    |
| Schema + instructions         | static                       | ~250    |
| **Total input**               |                              | **~1.4–1.7k** |

- **Only ONE research finding** drives the post (+ at most one trimmed secondary).
  The rest of `memory/research/` is never sent.
- **At most 2 voice samples**, each truncated to 600 chars. `style.md` is reduced
  to the static `VOICE_RULES` string — the file is read for completeness but only
  a ≤ 500-char head slice is appended if present.
- **Hard guard before the call:** estimate tokens (`len(prompt)//4`); if
  `est_tokens > 5000`, drop sample 2, then secondary finding, then trim key points
  to 2. This keeps every call comfortably under the 6k design ceiling and well
  under the **12,000 TPM Groq cap** (one call ≈ 1.5k in + ~2.5k out ≈ 4k TPM,
  leaving headroom).

### 4.5 Model routing

```
1. Try Groq llama-3.3-70b-versatile (FREE) — single call, temperature 0.7.
2. On rate-limit / error / timeout → fall back to local Ollama llama3.1:8b.
3. Ollama is also the path for OPTIONAL batch variant generation if N is raised
   high enough to risk the 12k TPM cap (generate variants locally, then one cheap
   Groq self-score pass — see §5.3).
```

---

## 5. Content Scoring System

### 5.1 Rubric (per variant)

Four sub-scores, **0–5 integer each**, deterministic to record:

| Dimension     | 0–5 question                                                      | Weight |
|---------------|-------------------------------------------------------------------|--------|
| `hook`        | Does line 1 stop the scroll? (curiosity / tension / specificity)  | 35     |
| `clarity`     | Is the single point obvious in one read?                          | 25     |
| `voice_match` | Sounds like the voice + samples, not corporate? Banned words = 0. | 25     |
| `cta`         | Clear, non-salesy invitation to engage?                           | 15     |

### 5.2 Composite score (0–100, computed in Python — NOT by the LLM)

```
composite = round(
    (hook/5)*35 + (clarity/5)*25 + (voice_match/5)*25 + (cta/5)*15
)
```

Computing the composite in code (not in the prompt) keeps it **deterministic and
auditable** — the LLM only supplies the four 0–5 integers; the script does the math.

### 5.3 Self-scored in the same call (default — cheaper)

- Scoring happens **in the same single LLM call** that writes the variants
  (the system prompt instructs "then self-score each variant"). This is the
  default: **1 call total**, lowest cost, fits the TPM budget.
- The script then **validates and recomputes**: it re-checks each variant's text
  against deterministic guards and may **override** the LLM's sub-scores:
  - **Banned-word check:** if any of `thrilled / delve / game-changer` appears
    → force `voice_match = 0` (hard floor, regardless of self-score).
  - **Length guard:** LinkedIn sweet spot. If `len(text) < 200` or `> 2200`
    chars → cap `clarity` at 2.
  - **Emoji guard:** > 2 emojis → cap `voice_match` at 3.
  - **CTA presence:** if no `?` and no imperative-CTA phrase in the last 2 lines
    → cap `cta` at 2.
- These overrides make the recorded `score_breakdown` **partly deterministic**
  (the guards) and partly model-judged (hook/clarity nuance), which is the
  intended "simple and deterministic to record" behavior.

**Optional second pass (only if needed):** if variants are generated locally on
Ollama for batch, run one cheap Groq call that *only* returns the four sub-scores
per variant (no rewriting). Not the default — adds a call and TPM.

### 5.4 Producing `score_breakdown`

For each variant the script writes:

```jsonc
"score_breakdown": { "hook": h, "clarity": c, "voice_match": v, "cta": t }
```

where each value is `min(llm_subscore, deterministic_cap)` after the §5.3 guards,
and `score` is the §5.2 composite of those final sub-scores.

Variants are written **sorted by `score` descending** (best first, `idx` reassigned
0..N-1 in that order) so the approval stage shows the strongest variant on top.

---

## 6. Memory Interactions

All filesystem. No DB, no network except the LLM call.

| Op    | Path                              | Action                                                        |
|-------|-----------------------------------|---------------------------------------------------------------|
| READ  | `memory/research/*.json`          | Load, filter (unused + recent), rank, pick 1 (+ ≤1 secondary).|
| READ  | `memory/voice/style.md`           | Take static `VOICE_RULES` + ≤ 500-char head slice.            |
| READ  | `memory/voice/samples.md`         | Pick 1–2 samples, truncate to 600 chars each.                 |
| WRITE | `memory/content/<id>.json`        | Write the draft, `status:"draft"`, atomic (tmp + rename).     |
| WRITE | `memory/research/<picked>.json`   | Append this content-id to the finding's `consumed_by[]`.      |

**Consumed-marking (idempotency / no reuse):**
After a successful draft write, the script reopens each picked research finding and
appends the new content-id to `consumed_by[]`, then atomically rewrites it. On the
next run, those findings are filtered out in §2.1 step 1. If the LLM call fails,
**nothing is marked consumed** and **no draft is written** — the run is a clean
no-op so a later retry reuses the same fresh research.

**Concurrency:** single-writer assumption (one cron chain). Atomic write via
write-to-`.tmp` + `os.replace`. No locks needed at this scale.

---

## 7. Example Output Draft (2 variants)

`memory/content/20260616-0930-glp1-retention.json`:

```json
{
  "id": "20260616-0930-glp1-retention",
  "created_at": "2026-06-16T09:30:00Z",
  "source_research_ids": ["20260615-0900-glp1-retention"],
  "persona": "jack",
  "platform": "linkedin",
  "variants": [
    {
      "idx": 0,
      "text": "Most clinics lose patients in the gap between visit 1 and visit 2.\n\nNot because of price. Because nobody followed up.\n\nWe looked at retention data across early Vytal pilots and the pattern was consistent: a single timed nudge in the first 72 hours moved no-show rates more than any discount we tried.\n\nThe boring lever beat the clever one. Again.\n\nWhat's the dumbest fix that actually worked for your retention?",
      "angle": "contrarian-take",
      "score": 87,
      "score_breakdown": { "hook": 5, "clarity": 5, "voice_match": 4, "cta": 4 }
    },
    {
      "idx": 1,
      "text": "Building Vytal taught me retention isn't a feature, it's a habit you engineer for the clinic.\n\nThree things that moved the needle in our pilots:\n1. A first-72-hour follow-up, automated.\n2. Reminders that read like a person, not a system.\n3. Making the next appointment the default, not a decision.\n\nNone of it is clever. All of it compounds.\n\nIf you run a clinic, which of these is missing today?",
      "angle": "how-to",
      "score": 79,
      "score_breakdown": { "hook": 3, "clarity": 5, "voice_match": 4, "cta": 4 }
    }
  ],
  "status": "draft",
  "approval": {}
}
```

(Variant 0 scores higher on `hook` → sorted first.)

---

## 8. Execution Sketch (`bin/cmo_agent.py`)

```python
def main():
    cfg = load_config()                      # constants from §2.3 + caps
    findings = load_research(MEM/"research") # §2.1
    primary, secondary = pick_topic(findings)
    if not primary:
        log("no eligible research, skipping"); return 0   # honest no-op

    style = read_style(MEM/"voice/style.md")        # head slice only
    samples = pick_samples(MEM/"voice/samples.md", primary, k=2)

    prompt = build_prompt(primary, secondary, style, samples, N=VARIANTS)
    enforce_token_budget(prompt, ceiling=5000)      # §4.4 trimming guard

    raw = call_llm(prompt)                   # Groq → Ollama fallback (§4.5)
    variants = parse_json(raw)               # tolerant JSON extract

    scored = [score_variant(v, primary, samples) for v in variants]  # §5
    scored.sort(key=lambda v: v["score"], reverse=True)
    reindex(scored)

    draft = build_draft(primary, secondary, scored)  # §3 contract
    write_atomic(MEM/f"content/{draft['id']}.json", draft)
    mark_consumed([primary, secondary], draft["id"]) # §6
    log(f"wrote {draft['id']} with {len(scored)} variants")
    return 0
```

Single file, ~250–350 lines. No external deps beyond an HTTP client for Groq +
`requests`/`ollama` for fallback. One developer, one sitting.

---

## 9. Config Constants (single source of truth)

```python
VARIANTS          = 3
TOPICS_PER_RUN    = 1
RECENCY_DAYS      = 14
PRIORITY_TAGS     = ["vytal", "retention", "healthcare", "founder", "ai"]
SAMPLE_MAX_CHARS  = 600
SUMMARY_MAX_CHARS = 400
KEYPOINT_MAX      = 4
TOKEN_CEILING     = 5000        # design ceiling under the 6k requirement
GROQ_MODEL        = "llama-3.3-70b-versatile"
OLLAMA_MODEL      = "llama3.1:8b"
TEMPERATURE       = 0.7
ANGLES            = ["contrarian-take","story","data-point","how-to","lesson-learned"]
```

---

## 10. Guardrails Honored

- **Never sends.** Produces `status:"draft"` only; sending is stage 3, approval-gated.
- **No fabricated data.** Prompt forbids inventing stats; only brief facts are usable.
- **Banned words enforced twice** (prompt instruction + deterministic post-check → `voice_match=0`).
- **Honest about failure.** No eligible research or failed LLM call → clean no-op, nothing written, nothing consumed.
- **Lean + reliable over clever.** One direct LLM call, filesystem memory, no gateway, no DB.
