#!/usr/bin/env python3
"""Founder brain-dump capture → nightly parse → knowledge items (Phase 6, lean).

Flow (no new infra, no vector DB / embeddings / RAG):
  1. CAPTURE  — every message in the Discord #brain-dump channel is appended raw to
     `memory/knowledge/braindump.jsonl` (unstructured; founder touches no schema).
  2. PARSE    — a nightly pass reads new entries, classifies + extracts knowledge items
     (LLM in real mode; a deterministic keyword heuristic in mock/offline/fallback),
     assigns a confidence, stamps provenance, and writes one JSON per item into the
     existing knowledge layer.
  3. REPORT   — a morning summary lists new opinions/insights/objections + low-confidence
     items needing review.

Every extracted item preserves provenance: the source message, its timestamp, the Discord
message id, and the extraction confidence.
"""

import sys
import os
import json
import re
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import store, contracts, llm  # noqa: E402

# memory/knowledge/ layout (reuses the store's memory_root)
KNOWLEDGE = "knowledge"
DIR_BY_TYPE = {"opinion": "opinions", "insight": "insights", "objection": "objections"}
REVIEW_DIR = "_review"                       # for "other" / low-confidence catch-all
BRAINDUMP_LOG = "braindump.jsonl"
CURSOR = "_parsed_cursor.json"
TYPES = ("opinion", "insight", "objection", "other")
DEFAULT_CONFIDENCE_THRESHOLD = 0.5           # below → status "needs_review"


# --------------------------------------------------------------------------
# paths / tree
# --------------------------------------------------------------------------

def knowledge_root():
    return store.memory_root() / KNOWLEDGE


def ensure_tree():
    root = knowledge_root()
    for sub in list(DIR_BY_TYPE.values()) + [REVIEW_DIR]:
        (root / sub).mkdir(parents=True, exist_ok=True)
    log = root / BRAINDUMP_LOG
    if not log.exists():
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("", encoding="utf-8")
    return root


def _braindump_path():
    return knowledge_root() / BRAINDUMP_LOG


def _cursor_path():
    return knowledge_root() / CURSOR


# --------------------------------------------------------------------------
# 1) CAPTURE — append a raw brain-dump entry (no schema for the founder)
# --------------------------------------------------------------------------

def record_braindump(message_id, text, *, author="founder", ts=None):
    """Append one raw #brain-dump message. Idempotent on message_id. Returns the entry
    (or None if blank / a duplicate)."""
    ensure_tree()
    text = (text or "").strip()
    if not text:
        return None
    mid = str(message_id)
    for entry in store.read_jsonl(_braindump_path()):
        if str(entry.get("message_id")) == mid:
            return None  # already captured
    entry = {
        "message_id": mid,
        "ts": ts or contracts.now_iso(),
        "author": author,
        "text": text,
    }
    store.append_jsonl(_braindump_path(), entry)
    return entry


# --------------------------------------------------------------------------
# 2) PARSE — classify + extract knowledge items
# --------------------------------------------------------------------------

# (signal, weight). Explicit INTENT markers weigh 1.0; ambient TOPIC words weigh 0.4 so
# they tip a tie but never overrule a clear "I think" / "they won't" / "we noticed".
_SIGNALS = {
    "objection": [(s, 1.0) for s in (
        "won't", "wont", "too expensive", "no time", "not on whatsapp", "spammy",
        "don't want", "dont want", "worried", "concern", "push back", "objection",
        "they said", "told me", "hesitant", "afraid", "but what about", "skeptical",
        "not sure i", "pushback", "complain")],
    "opinion": [(s, 1.0) for s in (
        "i think", "i believe", "in my opinion", "imo", "hot take", "unpopular",
        "i predict", "i bet", "convinced", "my take", "honestly", "we should",
        "i'd argue", "contrarian", "undervalue", "overrated", "underrated")],
    "insight": ([(s, 1.0) for s in (   # strong observation/learning/data markers
        "noticed", "found that", "data shows", "dashboard", "no-show", "no show",
        "recovered", "we saw", "turns out", "learned that", "%", "percent", "churn rate")]
        + [(s, 0.4) for s in (         # ambient topic words — weak, never decisive alone
        "churn", "retention", "clinic", "patients", "pricing", "price", "customers")]),
}


def _heuristic_extract(text):
    """Deterministic offline classifier: 1 item per message. Returns [item]."""
    low = text.lower()
    scores = {}
    matched = {}
    for kind, sigs in _SIGNALS.items():
        hits = [(s, w) for s, w in sigs if s in low]
        scores[kind] = round(sum(w for _, w in hits), 2)
        matched[kind] = [s for s, _ in hits]
    best = max(scores, key=lambda k: scores[k])
    if scores[best] < 1.0:   # no strong intent marker fired → unclassified
        kind = "other"
        confidence = 0.3
        tags = []
    else:
        kind = best
        confidence = min(0.55 + 0.12 * scores[best], 0.9)
        tags = sorted({re.sub(r"[^a-z]", "", h.replace(" ", "-")) for h in matched[best]})[:5]
    if kind != "other" and ("%" in text or re.search(r"\b\d{2,}\b", text)):
        tags = sorted(set(tags + ["has-number"]))
    title = " ".join(text.split())[:70]
    return [{"type": kind, "title": title, "body": text.strip(),
             "tags": tags, "confidence": round(confidence, 2), "_extractor": "heuristic"}]


_PARSER_SYSTEM = (
    "You extract structured knowledge from a startup founder's raw brain-dump note. "
    "The founder builds Vytal (clinic patient-retention). Return JSON ONLY: "
    '{"items":[{"type":"opinion|insight|objection|other","title":"<=70 chars",'
    '"body":"the substance, lightly cleaned","tags":["..."],"confidence":0.0-1.0}]}. '
    "type: opinion=a belief/take/prediction; insight=a real observation/learning/number "
    "about clinics or Vytal; objection=a prospect concern/fear/pushback; other=anything else. "
    "confidence = how clearly it is that type AND how useful for content. Be conservative; "
    "one note may yield multiple items. Never invent facts or numbers not in the note."
)


def _llm_extract(text):
    """Real-mode extraction via the LLM. Falls back to the heuristic on any failure."""
    try:
        raw = llm.complete(_PARSER_SYSTEM, text, json_only=True, max_tokens=700)
        data = json.loads(raw) if isinstance(raw, str) else raw
        items = data.get("items") if isinstance(data, dict) else None
        out = []
        for it in (items or []):
            t = it.get("type")
            if t not in TYPES:
                t = "other"
            body = (it.get("body") or text).strip()
            title = (it.get("title") or body)[:70]
            try:
                conf = float(it.get("confidence"))
            except (TypeError, ValueError):
                conf = 0.5
            conf = max(0.0, min(1.0, conf))
            tags = [str(x) for x in (it.get("tags") or [])][:6]
            out.append({"type": t, "title": title, "body": body,
                        "tags": tags, "confidence": round(conf, 2), "_extractor": "llm"})
        return out or _heuristic_extract(text)
    except Exception:
        return _heuristic_extract(text)


def _is_mock():
    val = os.environ.get("HERMES_LLM_MOCK", "")
    return val.strip().lower() not in ("", "0", "false", "no", "off")


def extract_items(text):
    """Mock/offline → deterministic heuristic; real → LLM (heuristic fallback)."""
    if _is_mock():
        return _heuristic_extract(text)
    return _llm_extract(text)


# --------------------------------------------------------------------------
# storage of extracted items (conforms to the Phase-5 base knowledge contract)
# --------------------------------------------------------------------------

def _load_cursor():
    cur = store.read_json(_cursor_path()) if _cursor_path().exists() else None
    if not isinstance(cur, dict):
        cur = {}
    cur.setdefault("parsed_ids", [])
    return cur


def _save_cursor(cur):
    store.atomic_write_json(_cursor_path(), cur)


def _item_id(kind, captured_at, title, message_id, n):
    stamp = captured_at.replace("-", "").replace(":", "").replace("T", "").replace("+", "")[:14]
    # message_id makes the id collision-proof: two same-text notes in the same second
    # (or the same note yielding several items via n) never overwrite each other.
    return "{0}_{1}_{2}_{3}_{4}".format(
        kind[:3], stamp, contracts.slugify(str(message_id), 16) or "m",
        contracts.slugify(title, 20) or "item", n)


def _store_item(item, entry, n, threshold):
    captured_at = contracts.now_iso()
    kind = item["type"]
    extractor = item.get("_extractor", "heuristic")
    item_id = _item_id(kind, captured_at, item["title"], entry["message_id"], n)
    confidence = item["confidence"]
    status = "active" if (confidence >= threshold and kind != "other") else "needs_review"
    record = {
        "id": item_id,
        "captured_at": captured_at,
        "type": kind,
        "title": item["title"],
        "body": item["body"],
        "tags": item.get("tags", []),
        "provenance": {
            "kind": "brain_dump",
            "source": entry["text"],                 # the original message
            "source_message_id": entry["message_id"],  # discord message id
            "timestamp": entry["ts"],                  # message timestamp
            "verifiable": False,
            "evidence": None,
            "confidence": confidence,                  # extraction confidence
            "extractor": extractor,                    # "llm" | "heuristic" (degraded path)
        },
        "status": status,
        "usage": {"consumed": False, "used_in": []},
    }
    errs = contracts.validate_knowledge_item(record)
    if errs:
        raise ValueError("knowledge item failed validation: " + "; ".join(errs))
    subdir = DIR_BY_TYPE.get(kind, REVIEW_DIR)
    path = knowledge_root() / subdir / (item_id + ".json")
    store.atomic_write_json(path, record)
    return record


def parse_new(*, confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD):
    """Process all not-yet-parsed brain-dump entries. Returns a run summary."""
    ensure_tree()
    cursor = _load_cursor()
    parsed = set(str(x) for x in cursor["parsed_ids"])
    created = []
    processed_msgs = 0
    for entry in store.read_jsonl(_braindump_path()):
        mid = str(entry.get("message_id"))
        if not mid or mid in parsed:
            continue
        items = extract_items(entry.get("text", ""))
        for i, item in enumerate(items):
            created.append(_store_item(item, entry, i, confidence_threshold))
        parsed.add(mid)
        processed_msgs += 1
    cursor["parsed_ids"] = sorted(parsed)
    cursor["last_run"] = contracts.now_iso()
    _save_cursor(cursor)
    by_type = {t: 0 for t in TYPES}
    needs_review = 0
    for r in created:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
        if r["status"] == "needs_review":
            needs_review += 1
    return {"processed_messages": processed_msgs, "items_created": len(created),
            "by_type": by_type, "needs_review": needs_review, "items": created}


# --------------------------------------------------------------------------
# 3) REPORT — morning summary
# --------------------------------------------------------------------------

def _iter_items():
    root = knowledge_root()
    for sub in list(DIR_BY_TYPE.values()) + [REVIEW_DIR]:
        d = root / sub
        if not d.exists():
            continue
        for f in sorted(d.glob("*.json")):
            if f.name.startswith("_"):
                continue
            rec = store.read_json(f)
            if isinstance(rec, dict):
                yield rec


def _today_ist():
    return datetime.now(contracts.IST).date().isoformat()


def morning_report(*, since_date=None):
    """Text report of items captured on/after since_date (default: today IST)."""
    since = since_date or _today_ist()
    buckets = {"opinion": [], "insight": [], "objection": [], "other": []}
    low_conf = []
    for rec in _iter_items():
        if (rec.get("captured_at") or "")[:10] < since:
            continue
        buckets.setdefault(rec.get("type", "other"), []).append(rec)
        if rec.get("status") == "needs_review":
            low_conf.append(rec)

    def _fmt(recs):
        return [
            "  • [{0:.0%}] {1}".format(r["provenance"]["confidence"], r["title"])
            for r in recs
        ] or ["  (none)"]

    lines = ["🧠 Brain-dump digest — {0}".format(since), ""]
    lines.append("New opinions ({0}):".format(len(buckets["opinion"])))
    lines += _fmt(buckets["opinion"])
    lines.append("New insights ({0}):".format(len(buckets["insight"])))
    lines += _fmt(buckets["insight"])
    lines.append("New objections ({0}):".format(len(buckets["objection"])))
    lines += _fmt(buckets["objection"])
    lines.append("")
    lines.append("⚠ Low-confidence / needs review ({0}):".format(len(low_conf)))
    if low_conf:
        for r in low_conf:
            p = r["provenance"]
            # show the FULL source text + msg id so review needs no digging
            lines.append("  • [{0:.0%}] ({1}) {2}".format(p["confidence"], r["type"], r["title"]))
            lines.append("      ↳ msg {0} ({1}): {2}".format(
                p["source_message_id"], p.get("extractor", "?"), p["source"]))
    else:
        lines.append("  (none)")
    return "\n".join(lines)
