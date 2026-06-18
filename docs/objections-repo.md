# Objections Repository — Specialist C Design (Phase-5 Knowledge Capture)

> **Design only, no code.** This spec defines a filesystem-native repository of real
> sales objections, concerns, fears, and recurring questions Arnav hears from clinic
> owners — and how the existing CMO turns one of them into a high-conversion post.
>
> **Why this exists (Phase-4 verdict):** content is generic because the input is poor.
> An objection is the single richest content seed we have: it is a *real thing a
> prospect said*, it names a fear the reader already has, and answering it
> (objection → reframe → CTA) is the highest-converting content shape in B2B SaaS.
> This repo is the gold seam.

---

## 0. Integration contract (do not break)

Plugs into the EXISTING pipeline with **minimal change**. NO vector DB, NO RAG, NO new agents.

```
research.py ─┐
             ├─► cmo.py ──► discord approval ──► approved/
objections ──┘   (Finding contract, 1 primary, <6k prompt)

Filesystem memory: ~/.hermes/memory/{research,content,approvals,voice,approved}/
                   ~/.hermes/memory/knowledge/objections/   ◄── NEW (this spec)
```

- The CMO already consumes the **Finding contract**. We surface objections to it as a
  Finding (with one new `type`), so `cmo.py` needs only a loader tweak, not a rewrite.
- Conforms to the **pinned base knowledge-item contract** (extended below).
- Everything is flat JSON files on disk — same ergonomics as the rest of `~/.hermes/memory/`.

---

## 1. Repository structure

```
~/.hermes/memory/knowledge/objections/
├── index.json                     # lightweight roll-up: id → {title, kind, frequency, status, used_in_count}
├── records/
│   ├── obj-20260617-whatsapp-spammy.json
│   ├── obj-20260617-patients-not-on-wa.json
│   ├── obj-20260618-no-time-setup.json
│   └── ...
├── inbox/                         # raw, unprocessed captures (voice notes / quick text) awaiting promotion
│   ├── 20260617T2140-voice.json   # {audio_path | raw_text, captured_at, status:"draft"}
│   └── ...
└── retired/                       # objections no longer relevant (kept for provenance, never deleted)
    └── obj-20251101-old-pricing.json
```

### Naming

- **Record id / filename:** `obj-<YYYYMMDD>-<slug>.json`
  - `obj-` prefix namespaces objections vs other knowledge types (insights, opinions, stats).
  - date = capture date (sortable, dedupe-friendly); `slug` = 2-4 word kebab of the objection.
  - Example: `obj-20260617-whatsapp-spammy.json`.
- **Inbox captures:** `<ISO8601>-voice.json` or `-text.json` — timestamp-first so they sort chronologically and can't collide.

### Scaling (stays flat & fast for years)

- One JSON file per objection. At Arnav's volume (a few sales calls/week) this is **tens to low-hundreds of files** — a flat dir is correct; no sharding needed.
- `index.json` is the only file the CMO/selector reads to *choose* an objection; it loads one full record only after picking. Keeps the hot path O(1) on file reads.
- `inbox/` → `records/` is a **promotion** step (draft → active): keeps half-formed captures out of the CMO's candidate pool until Arnav adds the proprietary `arnav_response`.
- `retired/` preserves history without polluting selection. Nothing is ever hard-deleted (provenance integrity).
- If it ever grows past ~1k records, shard `records/` by year (`records/2026/`) — but YAGNI until then.

---

## 2. File contract — the objection record

Extends the **pinned base knowledge-item contract**. Objections are real things prospects
said, so `provenance.kind ∈ {sales_call, demo, customer_question, observed}` and
`source` is the **anonymized** context. **No stat required** unless the objection itself
cites a number (e.g. "WhatsApp open rates are made up").

### `objection_kind` enum (the four capture targets)

| `objection_kind` | What it captures | Example trigger |
|------------------|------------------|-----------------|
| `objection`      | A flat rejection / pushback on the offer | "This feels spammy." |
| `concern`        | A worry that isn't a hard no — needs reassurance | "Will this annoy my patients?" |
| `fear`           | An emotional risk: reputation, money, looking dumb | "What if a patient complains and it hurts my clinic's name?" |
| `recurring_question` | A question asked over and over in sales calls | "How is this different from just texting them myself?" |

> These map cleanly onto Phase-4's insight: *every* one of these is content gold, but
> they pull different emotional levers, so we tag them distinctly to vary the post angle.

### Schema (base fields + objection extension)

```jsonc
{
  // ---- base knowledge-item contract (conform) ----
  "id": "obj-20260617-whatsapp-spammy",
  "captured_at": "2026-06-17T21:40:00+05:30",
  "type": "objection",                 // knowledge-item type; the objection family
  "title": "WhatsApp feels spammy",    // human-scannable label (used in index + Discord)
  "body": "...",                        // optional long-form notes / call context
  "tags": ["whatsapp", "trust", "channel-fit"],
  "provenance": {
    "kind": "sales_call",              // sales_call | demo | customer_question | observed
    "source": "GP clinic owner, Pune, ~3 chairs, demo call",  // ANONYMIZED — no name/clinic
    "verifiable": true,                // it was really said; true by definition for sales_call/demo
    "evidence": "voice note 20260617T2140; paraphrased"       // pointer to inbox capture, not a citation
  },
  "status": "active",                  // active | draft | retired
  "usage": { "consumed": false, "used_in": [] },  // used_in = [content_ids] once a post ships

  // ---- objection extension (Specialist C) ----
  "objection_kind": "objection",       // objection | concern | fear | recurring_question
  "objection": "They said WhatsApp reminders feel spammy and might annoy patients.",
  "underlying_concern": "Owner fears damaging the personal, trusted relationship they have with patients — they equate 'automated message' with 'cold marketing blast'.",
  "arnav_response": "Reframe: it's not marketing, it's a service reminder patients already expect from a clinic (like an appointment SMS). One opt-in, low-frequency, patient-name-personalised. Spam is unsolicited + high-volume; a single 'see you Tuesday at 4' is the opposite. I show them the actual message copy — it reads like a receptionist, not an ad.",  // PROPRIETARY — Arnav's real rebuttal/reframe
  "frequency": "very_high",            // how often it recurs: one | occasional | common | very_high
  "audience": "clinic_owner"           // clinic_owner | receptionist | practice_manager | doctor
}
```

**Field rationale (the two that carry the value):**

- **`underlying_concern`** — the *real worry beneath the words*. "WhatsApp is spammy" is
  the surface; the truth is *fear of damaging a trusted patient relationship*. Content
  that answers the surface objection is forgettable; content that names the **underlying
  concern** makes the reader feel *seen*. This is the field that turns an objection into
  resonance.
- **`arnav_response`** — Arnav's **proprietary reframe**, in his voice. This is the moat:
  generic AI can guess an objection, but only Arnav has the *winning rebuttal he's tested
  on real calls*. The CMO uses this as the post's payoff, so content speaks with earned
  authority instead of LLM platitudes.

`frequency` and `audience` let the selector prioritise (answer the most common pain
first) and target voice (a post for an owner reads differently than one for a receptionist).

### Three example records

**Example A — `obj-20260617-whatsapp-spammy.json`** (the full record shown above)

**Example B — `obj-20260617-patients-not-on-wa.json`**

```jsonc
{
  "id": "obj-20260617-patients-not-on-wa",
  "captured_at": "2026-06-17T18:05:00+05:30",
  "type": "objection",
  "title": "My patients aren't on WhatsApp",
  "body": "Owner of an older-skewing dental practice; believes elderly patients don't use WhatsApp.",
  "tags": ["whatsapp", "audience-fit", "demographics"],
  "provenance": {
    "kind": "demo",
    "source": "Dental clinic owner, tier-2 city, older patient base",
    "verifiable": true,
    "evidence": "voice note 20260617T1805"
  },
  "status": "active",
  "usage": { "consumed": false, "used_in": [] },

  "objection_kind": "objection",
  "objection": "He insisted his patients are too old / not tech-savvy enough to be on WhatsApp.",
  "underlying_concern": "Owner assumes a new channel means re-educating patients — effort and friction he doesn't want. Underneath: 'this won't work for MY specific clinic, you don't understand my patients.'",
  "arnav_response": "India's WhatsApp penetration skews far higher than owners assume across all ages — but I don't argue stats, I de-risk: we run a 2-week pilot on his actual patient list. If the delivery/read rate is low for HIS clinic, we stop, no cost. Moves it from 'trust my claim' to 'check your own data.' Also: the fallback is SMS, so no patient is left out.",
  "frequency": "common",
  "audience": "clinic_owner"
}
```

**Example C — `obj-20260618-no-time-setup.json`**

```jsonc
{
  "id": "obj-20260618-no-time-setup",
  "captured_at": "2026-06-18T11:20:00+05:30",
  "type": "objection",
  "title": "I don't have time to set this up",
  "body": "Solo practitioner running front desk + clinical; says onboarding is one more thing she can't take on.",
  "tags": ["onboarding", "time", "effort", "solo-clinic"],
  "provenance": {
    "kind": "sales_call",
    "source": "Solo physiotherapist, runs own front desk",
    "verifiable": true,
    "evidence": "voice note 20260618T1120"
  },
  "status": "active",
  "usage": { "consumed": false, "used_in": [] },

  "objection_kind": "concern",
  "objection": "She said she has no time to set up or learn another tool.",
  "underlying_concern": "Not really about setup minutes — it's tool fatigue + fear of another half-implemented system she pays for and abandons. The real worry: 'I'll spend effort and it'll become dead weight.'",
  "arnav_response": "Reframe the labour: WE do the setup from her existing appointment data — she does zero configuration. 'You forward me one export, I send you a test reminder by tomorrow.' I anchor on the cost of NOT doing it: every no-show is ₹X of lost chair time, and she's losing that every week she 'doesn't have time.' Inaction is the expensive option.",
  "frequency": "very_high",
  "audience": "clinic_owner"
}
```

---

## 3. How the CMO consumes it

### Surfaced as a Finding (one new enum value)

The CMO consumes the **Finding contract**:
`{id, run_date, type:"idea|trend|competitor|hook", topic, summary, source_url, raw_excerpt, tags[], consumed}`.

**Gap:** the Finding `type` enum has **no `"objection"`**. Fix with the smallest possible change:

- **Extend the enum:** `type ∈ {idea, trend, competitor, hook, objection}`. One value, backward-compatible.
- A thin adapter maps an objection record → Finding (no schema rewrite of either side):

| Finding field | ← from objection record |
|---------------|--------------------------|
| `id`          | objection `id` |
| `run_date`    | selection date |
| `type`        | `"objection"` |
| `topic`       | objection `title` |
| `summary`     | composed brief (below) |
| `source_url`  | `null` (objections have no URL — `provenance.source` instead) |
| `raw_excerpt` | the verbatim `objection` field |
| `tags`        | objection `tags` |
| `consumed`    | mirrors `usage.consumed` |

> If touching the Finding enum is undesirable, the fallback is to pass objections under
> `type:"hook"` — but a distinct `"objection"` type is cleaner and lets the CMO pick the
> objection→reframe→CTA template deliberately. **Recommend extending the enum.**

### The brief the CMO receives (objection → reframe → CTA)

The selector picks **1 primary objection** (highest `frequency`, `status:active`,
`usage.consumed:false`) and hands the CMO a compact brief — well under the **<6k token**
budget (a single objection record is a few hundred tokens):

```
OBJECTION BRIEF
  Audience:    clinic_owner
  They said:   "WhatsApp reminders feel spammy."        ← objection (the hook)
  Real worry:  Fear of damaging the trusted patient      ← underlying_concern (the resonance)
               relationship; equating automation w/ cold marketing.
  Reframe:     It's a service reminder patients expect,   ← arnav_response (the authority/payoff)
               not marketing. Spam = unsolicited+bulk;
               one personalised "see you Tuesday" is the opposite.
  Post shape:  objection → name the real fear → reframe → CTA (book a 2-week pilot)
```

This drives a **post structure** the CMO already knows how to fill:

1. **Open on the objection** (the reader's own words — instant recognition).
2. **Name the underlying concern** ("you're not worried about WhatsApp, you're worried about…") — the resonance beat.
3. **Deliver Arnav's reframe** — proprietary, specific, earned.
4. **CTA** — low-friction next step (pilot / see the actual message copy).

### Pairing (depth without bloat)

Phase-4 noted objections "pair well with an insight or opinion." The selector MAY attach
**one** supporting item (an insight Finding, or an `opinion`/`stat` knowledge-item from a
sibling repo) as a *secondary* under the primary objection — to add proof or a sharper
take. Still 1 primary; pairing stays optional and capped at one to protect the <6k budget
and the "1 primary finding, lean prompt" rule.

---

## 4. Capture method (phone-friendly, <60s, right after a call)

The constraint: Arnav just hung up a sales call, he's on his phone, he has 60 seconds
before the next thing. Friction kills capture. So capture is **dumb and fast**; enrichment
is **deferred**.

### Path A — Voice note (default, fastest)

1. Arnav sends a **Telegram/Discord voice note** answering one prompt:
   **"What did they push back on — and what's really underneath it?"**
2. The existing **voice pipeline (Whisper STT)** transcribes it; the raw transcript +
   audio path land in `knowledge/objections/inbox/` as a `status:"draft"` capture.
3. **Zero schema burden in the moment** — he just talks. Promotion to a full record
   happens later (Path C).

### Path B — Quick text template (when he can't speak)

A pinned one-line template he fires back in chat:

```
/obj <what they said> | <the real worry> | <my reframe>
```

Example: `/obj WhatsApp feels spammy | scared it ruins patient trust | it's a service reminder not marketing`

The three pipe-separated parts map directly to `objection` / `underlying_concern` /
`arnav_response`. Anything he omits is left blank for later. `objection_kind`, `frequency`,
`audience` default (`objection` / `occasional` / `clinic_owner`) and can be corrected on promotion.

### Path C — Promotion (draft → active, async, not on the phone)

Later (batched, e.g. a weekly review), Hermes presents each `inbox/` draft and asks the
3 enrichment questions Arnav skipped:
- *"Which kind — objection / concern / fear / recurring question?"*
- *"How often do you hear this — one-off, common, or every call?"* → `frequency`
- *"Is your reframe captured right?"* (confirm/refine `arnav_response`)

On confirm, the draft is written to `records/` as `status:"active"`, `index.json` updates,
and it enters the CMO candidate pool. **Approval-gated by design** — nothing proprietary
(`arnav_response`) ships to content without Arnav's confirmation, satisfying the project's
approval guardrail.

### The "what did they push back on?" nudge

To beat forgetting, Hermes can fire a **proactive prompt** shortly after a known sales
call (calendar-driven, later) or whenever Arnav tags a message `#salescall`:
*"What did they push back on? Drop a 20-sec voice note."* This makes capture a reflex, not a chore.

---

## 5. Quality / conversion lift — why objection-led content wins

**The thesis:** generic content broadcasts; objection content *answers a question the
reader is already asking in their head*. That is the difference between scroll-past and
"this person gets my clinic."

### Why it converts

- **Pre-qualified relevance.** The objection came from a real prospect in Arnav's exact
  ICP (clinic owner). If one owner said it on a call, hundreds are thinking it silently.
  The post meets a fear the reader already holds — no manufactured hook needed.
- **Resonance via `underlying_concern`.** Naming the *real* worry ("you're not afraid of
  WhatsApp, you're afraid of looking like a spammer to patients who trust you") triggers
  the "how did they know?" reaction that builds trust faster than any feature list.
- **Earned authority via `arnav_response`.** The reframe is battle-tested on real calls,
  not LLM-improvised. Content carries the weight of someone who has *handled* this
  objection live, which reads as credibility.
- **Built-in CTA.** Every objection answer ends in a natural next step (the same reframe
  Arnav uses to move a prospect forward), so the post has a conversion path baked in
  rather than a bolted-on "DM me."
- **Kills input poverty (the Phase-4 root cause).** The repo is a renewable seed bank:
  every sales call deposits new, specific, proprietary fuel — so content stops being
  generic because the *input* stopped being generic.

### Axes it moves

| Axis | Effect |
|------|--------|
| **Audience resonance** | ▲▲ High — speaks to a real, named hesitation in the reader's own words |
| **Conversion intent**  | ▲▲ High — objection→reframe→CTA is inherently a sales motion |
| **Authority / trust**  | ▲ Proprietary reframe = earned credibility, not LLM filler |
| **Specificity**        | ▲ Concrete clinic-context detail kills the "generic" smell |
| **Differentiation**    | ▲ Competitors can't replicate Arnav's tested rebuttals |
| **Reach / virality**   | ◐ Neutral-to-positive — niche-true beats broad-bland for ICP, but it's not a broad-appeal play |

**Net:** objection-led posts trade raw reach for **depth, trust, and conversion among the
exact people who can buy Vytal** — precisely the axis Phase-4 said the content was missing.

---

## Summary of decisions (locked for this spec)

1. **Storage:** flat JSON files at `~/.hermes/memory/knowledge/objections/` —
   `records/` (active), `inbox/` (raw drafts), `retired/` (history), `index.json` (selection roll-up). No DB, no RAG.
2. **Schema:** base knowledge-item contract + `{objection_kind, objection, underlying_concern, arnav_response, frequency, audience}`.
   The two value-carriers are **`underlying_concern`** (the real fear → resonance) and
   **`arnav_response`** (proprietary tested reframe → authority).
3. **CMO consumption:** extend the Finding `type` enum with `"objection"`; a thin adapter
   maps record→Finding; selector picks 1 primary by `frequency`, hands over an
   objection→reframe→CTA brief (<6k tokens), optionally pairs with one insight/opinion.
4. **Capture:** <60s voice note answering "what did they push back on, and what's really
   underneath it?" → Whisper → `inbox/` draft → async approval-gated promotion to active.
5. **Lift:** converts because it answers a fear the ICP already holds, in their words,
   with an earned reframe — fixing Phase-4's input-poverty root cause. Moves resonance,
   conversion, authority, and specificity.
