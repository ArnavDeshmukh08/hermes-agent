# Memory Layer Spec — Hermes Prime Phase-1

> **Authoritative memory contract.** All stage specs (research, content, approval,
> voice, publishing) conform to THIS document. If a stage spec disagrees with this
> file, this file wins.
>
> Status: design only. Owner: Agent D — Memory System Architect.
> Last meaningful update: 2026-06-16.

---

## 0. One-line "why no DB"

**At this scale (tens-to-hundreds of small JSON/MD files per category), a directory
glob + a JSON field filter answers every query the pipeline actually asks — so a
vector DB, embeddings, RAG, or Supabase would add operational weight, a daemon, and
a failure mode for zero retrieval benefit.** The filesystem *is* the database.

---

## 1. Platform & base location

- `HERMES_HOME = ~/.hermes`
- **This workflow's store:** `~/.hermes/memory/` (singular, no `s`).
- **Framework's own store:** `~/.hermes/memories/` (plural) — **DO NOT TOUCH.** It
  exists already and belongs to the Hermes Agent framework. Our code never reads or
  writes it.

Every path below is relative to `~/.hermes/memory/` unless stated otherwise.

> **Env override:** code resolves the base as
> `${HERMES_MEMORY_DIR:-$HERMES_HOME/memory}` so tests can point at a temp dir.
> The default is always `~/.hermes/memory`.

---

## 2. Folder structure (formalized)

```
~/.hermes/memory/
├── research/                 # one JSON per research run + optional .md summary
│   ├── 20260616_clinic-no-shows.json
│   ├── 20260616_clinic-no-shows.md          (optional human-readable summary)
│   └── _archive/                            # research older than retention window
│       └── 20260101_old-topic.json
├── content/                  # one JSON per draft set (variants + scores + status)
│   └── 20260616-1432-no-show-hook.json
├── approvals/
│   ├── queue.json                           # CURRENT pending queue (single file, rewritten)
│   └── decisions.jsonl                      # append-only decision log (NEVER deleted)
├── voice/
│   ├── style.md                             # the voice guide (how Arnav/persona writes)
│   └── samples.md                           # example posts that exemplify the voice
└── approved/                 # the Approved Content Repository — one .md per approved item
    └── 20260616-1432-no-show-hook.md
```

Notes:
- **Content lives in `content/` for its whole pre-approval life** (status changes in
  place; the file does not move between folders while in `content/`). On approval, a
  rendered copy is **written** to `approved/` — the `content/` JSON remains as the
  source-of-truth record (see §6 Lifecycle).
- `_archive/` exists only under `research/`. Drafts and approved items are never
  archived (they are small and historically useful).
- No `index.json` is created up front. See §5.4 for the precise, narrow condition
  under which a per-folder index becomes worthwhile.

---

## 3. Naming conventions

### 3.1 Shared primitives

- **`<yyyymmdd>`** — UTC date, e.g. `20260616`.
- **`<hhmm>`** — UTC 24h time, e.g. `1432`.
- **`<slug>` / `<shortslug>`** — derived from the topic/headline:
  1. lowercase the source string,
  2. replace any run of non-`[a-z0-9]` characters with a single hyphen `-`,
  3. strip leading/trailing hyphens,
  4. collapse repeated hyphens,
  5. truncate to **40 chars** for `research` slugs, **24 chars** for content
     `shortslug` (truncate on a hyphen boundary if possible).
  - Example: `"Clinic no-shows & DPDP, 2026!"` → `clinic-no-shows-dpdp-2026`.

> **Rule of thumb:** hyphens (`-`) separate words *inside* a slug; underscores (`_`)
> separate the structural fields *of a filename*. This makes filenames mechanically
> splittable.

### 3.2 Research files — `research/`

```
<yyyymmdd>_<topic-slug>.json
<yyyymmdd>_<topic-slug>.md      (optional, same basename)
```
- Underscore separates date from slug; slug itself is hyphenated.
- If two runs land on the same date + slug, append `_<n>` before the extension:
  `20260616_clinic-no-shows_2.json`.

### 3.3 Content & approved files — `content/`, `approved/`

The **content-id** is the stable key linking a draft JSON to its approved MD:

```
content-id = <yyyymmdd>-<hhmm>-<shortslug>
content draft:   content/<content-id>.json
approved item:   approved/<content-id>.md
```
- Within a content-id, fields are hyphen-joined (no underscores) so the whole id is
  one URL-safe, filename-safe token: `20260616-1432-no-show-hook`.
- The content-id is generated **once** at draft creation and never changes — it is
  the join key across `content/`, `approved/`, `approvals/queue.json`, and
  `approvals/decisions.jsonl`.

### 3.4 General rules
- All filenames lowercase.
- ASCII only. Non-ASCII in source topics is transliterated/stripped during slugging.
- No spaces, ever.

---

## 4. Metadata format & the three pinned contracts

### 4.0 File-format decision

| File type | Format | Rationale |
|-----------|--------|-----------|
| `research/*.json` | **Pure JSON** | machine-consumed; one object with a `findings[]` array |
| `research/*.md`   | Plain markdown, no frontmatter | optional human summary only; never parsed |
| `content/*.json`  | **Pure JSON** | the source of truth for a draft set |
| `approved/*.md`   | **YAML frontmatter + markdown body** | human-readable + machine-readable header |
| `approvals/queue.json` | Pure JSON | rewritten atomically |
| `approvals/decisions.jsonl` | **JSON Lines** | append-only audit log |
| `voice/*.md`      | Plain markdown | prompt material |

**Decision: markdown files that need metadata use YAML frontmatter (no separate
sidecar).** Everything else is pure JSON. We do NOT pair a `.md` with a `.json`
sidecar — `approved/*.md` carries its own frontmatter, and the canonical machine
record already lives in `content/<id>.json`.

### 4.1 Contract A — Finding (inside `research/*.json`)

A research run file:
```json
{
  "run_id": "20260616_clinic-no-shows",
  "run_date": "2026-06-16",
  "topic": "clinic no-shows",
  "findings": [ /* array of Finding objects below */ ]
}
```

**Finding object:**
```json
{
  "id": "20260616_clinic-no-shows#1",
  "run_date": "2026-06-16",
  "type": "stat",
  "topic": "clinic no-shows",
  "summary": "No-show rates in Indian outpatient clinics run 20-30%.",
  "source_url": "https://example.org/study",
  "raw_excerpt": "…verbatim quote backing the summary…",
  "tags": ["no-shows", "india", "retention"],
  "consumed": false
}
```

| Field | Type | Req? | Notes |
|-------|------|------|-------|
| `id` | string | **required** | `<run_id>#<n>`, 1-based, unique within and across runs |
| `run_date` | string (ISO `YYYY-MM-DD`) | **required** | denormalized for date filtering without opening the run wrapper |
| `type` | enum string | **required** | one of `stat` \| `quote` \| `fact` \| `trend` \| `pain_point` \| `competitor` \| `source` |
| `topic` | string | **required** | matches run `topic` |
| `summary` | string | **required** | one-line claim, the thing content stages read |
| `source_url` | string (URL) | optional | null if synthesized/uncited |
| `raw_excerpt` | string | optional | verbatim support; null allowed |
| `tags` | string[] | **required** | may be empty `[]`; lowercase slugged tokens |
| `consumed` | boolean | **required** | dedup flag — see §5.3. Defaults `false` at write time |

### 4.2 Contract B — Content draft (`content/<content-id>.json`)

```json
{
  "id": "20260616-1432-no-show-hook",
  "created_at": "2026-06-16T14:32:05Z",
  "source_research_ids": ["20260616_clinic-no-shows#1", "20260616_clinic-no-shows#4"],
  "persona": "vytal-founder",
  "platform": "linkedin",
  "variants": [
    {
      "idx": 0,
      "text": "Most clinics lose 1 in 4 patients to no-shows…",
      "angle": "stat-led hook",
      "score": 0.82,
      "score_breakdown": { "hook": 0.9, "clarity": 0.85, "voice_match": 0.75, "cta": 0.8 }
    }
  ],
  "status": "pending",
  "approval": { "telegram_message_id": null, "chat_id": null }
}
```

| Field | Type | Req? | Notes |
|-------|------|------|-------|
| `id` | string | **required** | the content-id; equals the filename stem |
| `created_at` | string (ISO 8601 UTC, `Z`) | **required** | generation timestamp |
| `source_research_ids` | string[] | **required** | Finding `id`s consumed; may be `[]` but normally non-empty |
| `persona` | string | **required** | persona slug, e.g. `vytal-founder` |
| `platform` | enum string | **required** | `linkedin` \| `x` \| `instagram` \| `threads` \| `blog` |
| `variants` | object[] | **required** | ≥1 variant; schema below |
| `status` | enum string | **required** | `draft` \| `pending` \| `approved` \| `rejected` \| `posted` — see §6 |
| `approval` | object | **required** | `{telegram_message_id, chat_id}`; both null until queued |

**Variant object:**

| Field | Type | Req? | Notes |
|-------|------|------|-------|
| `idx` | int | **required** | 0-based position; stable, used by decisions log |
| `text` | string | **required** | the post body |
| `angle` | string | **required** | short label of the creative angle |
| `score` | number `0..1` | **required** | overall, derived from breakdown |
| `score_breakdown` | object | **required** | exactly the four keys below |
| `score_breakdown.hook` | number `0..1` | **required** | |
| `score_breakdown.clarity` | number `0..1` | **required** | |
| `score_breakdown.voice_match` | number `0..1` | **required** | |
| `score_breakdown.cta` | number `0..1` | **required** | |

### 4.3 Contract C — Decision (one line in `approvals/decisions.jsonl`)

One JSON object per line, append-only:
```json
{"ts":"2026-06-16T15:01:22Z","content_id":"20260616-1432-no-show-hook","variant_idx":0,"action":"approve","note":"ship it","decided_by":"arnav"}
```

| Field | Type | Req? | Notes |
|-------|------|------|-------|
| `ts` | string (ISO 8601 UTC) | **required** | decision time |
| `content_id` | string | **required** | the content-id |
| `variant_idx` | int \| null | **required** | which variant; null for whole-set actions (e.g. `reject_all`) |
| `action` | enum string | **required** | `approve` \| `reject` \| `edit` \| `defer` \| `reject_all` |
| `note` | string | optional | free text; `""` allowed |
| `decided_by` | string | **required** | `arnav` (human) or an agent id |

### 4.4 `approvals/queue.json`

The live pending set — a thin pointer list, rewritten atomically on every change:
```json
{
  "updated_at": "2026-06-16T14:33:00Z",
  "pending": [
    {
      "content_id": "20260616-1432-no-show-hook",
      "queued_at": "2026-06-16T14:33:00Z",
      "telegram_message_id": 5512,
      "chat_id": 100200300
    }
  ]
}
```
Queue holds pointers only; the full draft is always in `content/<id>.json`. Removing
an item from `pending[]` does not delete anything — the decision is already recorded
in `decisions.jsonl` and the draft's `status` is updated in place.

### 4.5 `approved/<content-id>.md` (YAML frontmatter + body)

```markdown
---
content_id: 20260616-1432-no-show-hook
approved_at: 2026-06-16T15:01:22Z
persona: vytal-founder
platform: linkedin
variant_idx: 0
source_research_ids: [20260616_clinic-no-shows#1, 20260616_clinic-no-shows#4]
score: 0.82
---

Most clinics lose 1 in 4 patients to no-shows…
```
Body = the approved variant's final `text` (post any edit).

---

## 5. Retrieval strategy (no DB)

All retrieval = **glob the directory → open candidate files → filter on JSON fields.**
File counts are small; a full scan of any folder is milliseconds.

### 5.1 Research by date / topic
- **By date:** glob `research/<yyyymmdd>_*.json` (filename prefix is the date — no
  file open needed to filter by day).
- **By date range:** glob `research/*.json`, split filename on `_`, compare the date
  field. To read findings, open and walk `findings[]`.
- **By tag/type:** open candidate run files, filter `findings[]` on `tags`/`type`.
- `_archive/` is excluded from default globs; include it explicitly only for history.

### 5.2 Content by status
- Glob `content/*.json`, open each, filter on `status`.
- Common reads: `status == "pending"` (anything awaiting approval),
  `status in {approved, posted}` (the publish stage), `status == "draft"`.
- Typical folder size is small; if it ever isn't, see §5.4.

### 5.3 Dedup — never reuse a finding, never re-post a draft
Two mechanisms, both pure-filesystem:

1. **Findings — `consumed` flag.** When a content draft is *created* from findings,
   the creating stage sets `consumed: true` on each referenced Finding in its
   `research/*.json` file (single-writer rewrite of that one file). Content
   selection only draws from findings where `consumed == false`. This prevents the
   same stat being turned into two posts.
   - The link is also recorded forward via `source_research_ids` on the draft, so the
     relationship is auditable from both sides.

2. **Drafts — `status` + the approved repo.** A draft is never re-posted because:
   - publishing only acts on `status == "approved"`, and flips it to `posted` (single
     write) when done — a `posted` item is skipped thereafter;
   - the existence of `approved/<content-id>.md` is itself an idempotency marker:
     before publishing, check the file exists and its frontmatter `content_id`
     matches; the content-id is unique per draft so there is exactly one approved
     artifact per item.

3. **Cross-run finding identity.** Finding `id` is `<run_id>#<n>` and globally unique,
   so `source_research_ids` and `consumed` are unambiguous even across multiple
   research runs on the same topic.

### 5.4 Optional `index.json` — when (and only when) it earns its place
**Default: omit it.** A per-folder `index.json` is justified ONLY if a folder routinely
exceeds **~500 files** AND a hot path scans it on a tight loop (e.g. a publish worker
polling `content/` every few seconds). In that case add a single
`content/index.json`:
```json
{ "updated_at": "…", "items": [ {"id":"…","status":"…","platform":"…","created_at":"…"} ] }
```
Rules if adopted: it is a **derived cache, never source of truth**; it is rebuilt by a
full scan on any inconsistency; the per-file JSON always wins. Until the 500-file
threshold is actually hit, building it is premature optimization (YAGNI) and is
explicitly NOT done in Phase-1.

### 5.5 Concurrency
- **Single-writer per file.** Each JSON/MD file is owned by one writer at a time.
  Writes are atomic: write to `*.tmp` in the same directory, `fsync`, then
  `os.replace()` over the target. No partial reads.
- **`decisions.jsonl` is append-only**: open with `O_APPEND` and write one complete
  line per record (a single `write()` of a sub-PIPE_BUF line is atomic on POSIX). The
  log is never rewritten or truncated.
- **`queue.json` is rewritten atomically** by its single owner (the approval stage).
  Other stages read it but do not write it.
- No file locks library required at this scale; the atomic-replace + single-writer
  discipline is sufficient. If two stages might ever write the same file, route both
  writes through the one stage that owns that folder.

---

## 6. Lifecycle / retention

### 6.1 Content status machine
```
draft ──> pending ──> approved ──> posted
              │
              └────> rejected            (deferred re-enters as pending later)
```

| Status | Set by | Where the item lives | Side effects |
|--------|--------|----------------------|--------------|
| `draft` | content stage | `content/<id>.json` | findings marked `consumed` on creation |
| `pending` | approval stage | `content/<id>.json` + pointer in `queue.json` | Telegram msg sent; `approval.*` filled |
| `approved` | approval stage (on human approve) | `content/<id>.json` + new `approved/<id>.md` | decision appended to `decisions.jsonl`; removed from `queue.pending[]` |
| `rejected` | approval stage (on human reject) | `content/<id>.json` | decision appended; removed from `queue.pending[]` |
| `posted` | publish stage | `content/<id>.json` | publish receipt; idempotency via existing `approved/<id>.md` |

- `edit` action: the chosen variant's text is updated, decision logged as `edit`, then
  the item proceeds to `approved` (the edited text is what lands in `approved/*.md`).
- `defer`: logged; item stays `pending` (or is re-queued) — no terminal transition.

### 6.2 Retention & what is NEVER deleted
- **Research:** files older than **90 days** are moved (not deleted) from `research/`
  into `research/_archive/`. They remain globbable for history; default queries skip
  the archive. Archiving is a move, never a delete.
- **Content drafts:** retained indefinitely in Phase-1 (small JSON). May be archived
  later under the same `_archive/` convention if the folder grows; not done now.
- **NEVER deleted, ever:**
  - `approvals/decisions.jsonl` — the append-only audit trail (compliance: every send
    decision is traceable).
  - `approved/*.md` — the Approved Content Repository (the durable record of what was
    cleared to publish).
  - `voice/style.md` and `voice/samples.md` — curated voice assets.

---

## 7. Deliberately NOT doing (and why)

| Not doing | Why it's unnecessary at this scale |
|-----------|-----------------------------------|
| Vector database | No semantic-similarity query exists in the pipeline; filtering is by date/status/tag — exact-match field filters, not nearest-neighbor. |
| Embeddings / RAG | Stages read explicit `source_research_ids`; they don't "retrieve relevant context" by similarity. Glob + field filter covers 100% of reads. |
| Supabase / Postgres / any server | Adds a daemon, network dependency, and a new failure mode. The data is a few hundred small files on the same box the agents run on. |
| ORM / migrations | Schemas are versionable in this doc + validated in code at the boundary (per project coding rules). |
| Per-file locks library | Single-writer-per-folder + atomic `os.replace` + append-only jsonl removes the need. |
| Up-front `index.json` | Premature optimization until a folder crosses ~500 files on a hot loop (§5.4). |

**The filesystem is sufficient because** every access pattern reduces to: *list a
directory, parse small JSON, compare a few fields.* That is exactly what a glob and a
loop do — adding a database would move the same data behind a heavier interface for no
query the pipeline can't already answer.

---

## 8. Conformance checklist for other stage specs
- [ ] Reads/writes only under `~/.hermes/memory/` (never `~/.hermes/memories/`).
- [ ] Uses the content-id format `<yyyymmdd>-<hhmm>-<shortslug>` as the cross-folder join key.
- [ ] Validates JSON against Contracts A/B/C at the boundary before writing.
- [ ] Sets `consumed: true` on findings when it turns them into a draft.
- [ ] Appends to `decisions.jsonl` (never rewrites it) for every approve/reject/edit.
- [ ] Writes files atomically (`*.tmp` → `os.replace`); owns its folder as the single writer.
- [ ] Never deletes the decisions log, the approved repo, or the voice assets.
