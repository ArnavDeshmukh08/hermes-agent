# Jack Memory Schema (USER.md)

Jack's living memory is a single human-readable text file (`USER.md`, deployed to
`~/.hermes/USER.md`). It is plain text so Arnav can open and edit it directly at
any time. Jack also updates it automatically after conversations.

## Format

The file opens with a short header (title, `Last updated:` date, one-line note),
then a series of sections. Each section starts with a header line of the form:

```
[SECTION NAME]
```

The delimiter convention: a section header is a line that is **exactly** an
uppercase label wrapped in square brackets, matching `^\[.+\]$`. Everything
between one `[SECTION]` line and the next belongs to that section. Sections use
plain lines and indented sub-bullets (`- ...`).

## Sections (in order)

1. `[IDENTITY]` — who the person is: name, age, location, schedule basics.
   Update rule: facts change slowly; append or correct a line when a stable fact
   changes (e.g. moved cities). Never rewrite the whole block.
2. `[RELATIONSHIPS]` — people in their life (partner, friends, family) and key
   facts about each. Update rule: add a new named person or a new sub-bullet
   under an existing person; keep existing bullets.
3. `[WORK & PROJECTS]` — current ventures, roles, status. Update rule: add new
   projects; append a status sub-bullet rather than overwriting history.
4. `[CURRENT PRIORITIES]` — what matters this week. Update rule: this is the one
   section that is naturally time-boxed; refresh the dated "Week of ..." block
   when the week/focus clearly changes.
5. `[DAILY ROUTINE — <CONTEXT>]` — the typical day for the person's current
   context (e.g. holidays vs term). Update rule: adjust when the routine clearly
   shifts; keep the context label accurate.
6. `[PREFERENCES]` — likes, dislikes, communication style, working approach.
   Update rule: append a clear new preference; never delete an existing one
   without an explicit instruction from Arnav.
7. `[GOALS]` — short-term and long-term aims. Update rule: append; mark progress
   as a sub-bullet rather than rewriting.
8. `[THINGS JACK HAS LEARNED]` — the running log Jack maintains itself.

## Append-only principle

Treat the file as **append-only by default**. Add new sub-bullets; do not
rewrite or delete existing content. The only exception is correcting a fact that
has genuinely changed (e.g. a new location), and even then prefer adding a
corrected line over erasing history. This keeps the memory trustworthy and lets
Arnav see how it evolved.

## The `[THINGS JACK HAS LEARNED]` rule

This section is special: **always append a single dated line per learning**,
newest at the bottom, in the form:

```
YYYY-MM-DD: <one concise sentence of what was learned>
```

Never edit or remove earlier dated lines. This is the audit trail of Jack's
learning.

## What triggers an update vs what is ignored

Update the memory only on **clear, specific, durable facts**, for example:

- "I moved back to Pune" → update `[IDENTITY]` / routine.
- "Spandan is now handling sales for Vytal" → `[WORK & PROJECTS]`.
- "I hate thin-crust pizza" → `[PREFERENCES]`.
- "My friend Darshil is getting married in December" → `[RELATIONSHIPS]`.

Ignore vague, transient, or low-signal impressions, for example:

- "seems busy this week" / "in a good mood today" — moods, not facts.
- One-off events with no lasting relevance.
- Speculation or inference Arnav did not confirm.
- Anything already recorded (avoid duplicate bullets).

When in doubt, prefer **not** writing over writing noise.

## Human-editable contract

The file must always stay clean, readable, and safe for Arnav to edit by hand:
keep the `[SECTION]` headers intact, keep one fact per line / sub-bullet, and do
not introduce machine-only encoding. If Arnav edits the file, Jack respects those
edits as ground truth.
