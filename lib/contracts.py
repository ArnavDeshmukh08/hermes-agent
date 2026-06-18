"""Hermes Prime — Stream A core contracts.

Pure stdlib data contracts, ID helpers, URL helpers, validators, and the
canonical draft constructor. No I/O here (see lib/store.py for persistence).
"""

import re
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Enums / constants
# ---------------------------------------------------------------------------
FINDING_TYPES = {"idea", "trend", "competitor", "hook"}
STATUSES = {"pending", "approved", "rejected", "revise", "dropped"}
ACTIONS = {"approve", "reject", "revise"}
URL_ALLOWLIST = {"vytal.app", "vytal.in", "vytal.health"}  # CMO/dispatch link-flagging

# IST (Asia/Kolkata) is fixed at +05:30; no DST.
IST = timezone(timedelta(hours=5, minutes=30))

_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+", re.IGNORECASE)
_HOST_RE = re.compile(r"https?://([^/\s:?#]+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Slug / ID helpers
# ---------------------------------------------------------------------------
def slugify(text, maxlen=40):
    """lowercase, non-alnum -> '-', collapse repeats, trim, truncate."""
    s = str(text).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if maxlen and len(s) > maxlen:
        s = s[:maxlen].rstrip("-")
    return s


def _resolve_day(day):
    """Return a datetime for the given `day` (None -> now in IST)."""
    if day is None:
        return datetime.now(IST)
    return day


def make_run_id(topic, day=None):
    """'<yyyymmdd>_<topic-slug>' using IST day."""
    d = _resolve_day(day)
    return f"{d.strftime('%Y%m%d')}_{slugify(topic)}"


def finding_id(run_id, n):
    """'<run_id>#<n>'."""
    return f"{run_id}#{n}"


def make_content_id(slug, when=None):
    """'<yyyymmdd>-<hhmm>-<slug>' using IST clock."""
    d = _resolve_day(when)
    return f"{d.strftime('%Y%m%d')}-{d.strftime('%H%M')}-{slugify(slug)}"


def now_iso():
    """Timezone-aware ISO 8601 in Asia/Kolkata (+05:30)."""
    return datetime.now(IST).isoformat()


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------
def find_urls(text):
    """Extract all http(s) URLs from `text`."""
    if not text:
        return []
    return _URL_RE.findall(str(text))


def _host_of(url):
    m = _HOST_RE.match(url)
    if not m:
        return ""
    host = m.group(1).lower()
    # strip credentials / port if any slipped through
    if "@" in host:
        host = host.split("@", 1)[1]
    return host


def _host_allowed(host):
    return any(host == d or host.endswith("." + d) for d in URL_ALLOWLIST)


def flag_unallowlisted_urls(text):
    """Return URLs whose host is not on the allowlist (nor a subdomain of one)."""
    flagged = []
    for url in find_urls(text):
        host = _host_of(url)
        if not _host_allowed(host):
            flagged.append(url)
    return flagged


# ---------------------------------------------------------------------------
# Validators — return list[str] of error messages ([] == valid)
# ---------------------------------------------------------------------------
def _is_str(v):
    return isinstance(v, str)


def _is_nonempty_str(v):
    return isinstance(v, str) and v.strip() != ""


def _is_unit_float(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= float(v) <= 1.0


def validate_finding(d):
    errs = []
    if not isinstance(d, dict):
        return ["finding must be a dict"]
    if not _is_nonempty_str(d.get("id")):
        errs.append("finding.id must be a non-empty string")
    t = d.get("type")
    if t not in FINDING_TYPES:
        errs.append(f"finding.type must be one of {sorted(FINDING_TYPES)}")
    if not _is_nonempty_str(d.get("topic")):
        errs.append("finding.topic must be a non-empty string")
    if not _is_nonempty_str(d.get("summary")):
        errs.append("finding.summary must be a non-empty string")
    if not _is_nonempty_str(d.get("source_url")):
        errs.append("finding.source_url must be a non-empty string")
    if "raw_excerpt" not in d or not _is_str(d.get("raw_excerpt")):
        errs.append("finding.raw_excerpt must be a string")
    if not isinstance(d.get("tags"), list):
        errs.append("finding.tags must be a list")
    if not isinstance(d.get("consumed"), bool):
        errs.append("finding.consumed must be a bool")
    return errs


def validate_research_run(d):
    errs = []
    if not isinstance(d, dict):
        return ["research_run must be a dict"]
    if not _is_nonempty_str(d.get("run_id")):
        errs.append("run.run_id must be a non-empty string")
    if not _is_nonempty_str(d.get("run_date")):
        errs.append("run.run_date must be a non-empty string")
    if not _is_nonempty_str(d.get("topic")):
        errs.append("run.topic must be a non-empty string")
    findings = d.get("findings")
    if not isinstance(findings, list):
        errs.append("run.findings must be a list")
    else:
        for i, f in enumerate(findings):
            for e in validate_finding(f):
                errs.append(f"findings[{i}]: {e}")
    return errs


def _validate_variant(v, i):
    errs = []
    if not isinstance(v, dict):
        return [f"variants[{i}] must be a dict"]
    if not isinstance(v.get("idx"), int) or isinstance(v.get("idx"), bool):
        errs.append(f"variants[{i}].idx must be an int")
    if not _is_str(v.get("text")):
        errs.append(f"variants[{i}].text must be a string")
    if not _is_str(v.get("angle")):
        errs.append(f"variants[{i}].angle must be a string")
    if not _is_unit_float(v.get("score")):
        errs.append(f"variants[{i}].score must be a float in 0..1")
    sb = v.get("score_breakdown")
    if not isinstance(sb, dict):
        errs.append(f"variants[{i}].score_breakdown must be a dict")
    else:
        for k in ("hook", "clarity", "voice_match", "cta"):
            if not _is_unit_float(sb.get(k)):
                errs.append(f"variants[{i}].score_breakdown.{k} must be a float in 0..1")
    return errs


def validate_draft(d):
    errs = []
    if not isinstance(d, dict):
        return ["draft must be a dict"]
    if not _is_nonempty_str(d.get("id")):
        errs.append("draft.id must be a non-empty string")
    if not _is_nonempty_str(d.get("created_at")):
        errs.append("draft.created_at must be a non-empty string")
    if not isinstance(d.get("source_research_ids"), list):
        errs.append("draft.source_research_ids must be a list")
    if not _is_nonempty_str(d.get("persona")):
        errs.append("draft.persona must be a non-empty string")
    if not _is_nonempty_str(d.get("platform")):
        errs.append("draft.platform must be a non-empty string")
    if d.get("status") not in STATUSES:
        errs.append(f"draft.status must be one of {sorted(STATUSES)}")
    variants = d.get("variants")
    if not isinstance(variants, list) or len(variants) == 0:
        errs.append("draft.variants must be a non-empty list")
    else:
        for i, v in enumerate(variants):
            errs.extend(_validate_variant(v, i))
    if not isinstance(d.get("approval"), dict):
        errs.append("draft.approval must be a dict")
    return errs


def validate_decision(d):
    errs = []
    if not isinstance(d, dict):
        return ["decision must be a dict"]
    if not _is_nonempty_str(d.get("ts")):
        errs.append("decision.ts must be a non-empty string")
    if not _is_nonempty_str(d.get("content_id")):
        errs.append("decision.content_id must be a non-empty string")
    if d.get("action") not in ACTIONS:
        errs.append(f"decision.action must be one of {sorted(ACTIONS)}")
    if not _is_nonempty_str(d.get("decided_by")):
        errs.append("decision.decided_by must be a non-empty string")
    vidx = d.get("variant_idx")
    if not (vidx is None or (isinstance(vidx, int) and not isinstance(vidx, bool))):
        errs.append("decision.variant_idx must be int or None")
    if not _is_str(d.get("note")):
        errs.append("decision.note must be a string")
    return errs


KNOWLEDGE_TYPES = {"opinion", "insight", "objection", "other"}
KNOWLEDGE_STATUSES = {"active", "needs_review", "retired"}


def validate_knowledge_item(d):
    """Phase-6 founder-knowledge item (brain-dump derived). Returns list[str] of errors."""
    errs = []
    if not isinstance(d, dict):
        return ["knowledge item must be a dict"]
    if not _is_nonempty_str(d.get("id")):
        errs.append("knowledge.id must be a non-empty string")
    if not _is_nonempty_str(d.get("captured_at")):
        errs.append("knowledge.captured_at must be a non-empty string")
    if d.get("type") not in KNOWLEDGE_TYPES:
        errs.append(f"knowledge.type must be one of {sorted(KNOWLEDGE_TYPES)}")
    if not _is_str(d.get("title")):
        errs.append("knowledge.title must be a string")
    if not _is_str(d.get("body")):
        errs.append("knowledge.body must be a string")
    if not isinstance(d.get("tags"), list):
        errs.append("knowledge.tags must be a list")
    if d.get("status") not in KNOWLEDGE_STATUSES:
        errs.append(f"knowledge.status must be one of {sorted(KNOWLEDGE_STATUSES)}")
    p = d.get("provenance")
    if not isinstance(p, dict):
        errs.append("knowledge.provenance must be a dict")
    else:
        if not _is_nonempty_str(p.get("source")):
            errs.append("provenance.source (the original message) is required")
        if not _is_nonempty_str(str(p.get("source_message_id") or "")):
            errs.append("provenance.source_message_id (discord id) is required")
        if not _is_nonempty_str(p.get("timestamp")):
            errs.append("provenance.timestamp is required")
        c = p.get("confidence")
        if not (isinstance(c, (int, float)) and not isinstance(c, bool) and 0.0 <= c <= 1.0):
            errs.append("provenance.confidence must be a number in [0,1]")
    return errs


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------
def new_draft(content_id, source_research_ids, variants, persona="jack", platform="linkedin"):
    """Build a canonical draft dict (status='pending', approval skeleton)."""
    return {
        "id": content_id,
        "created_at": now_iso(),
        "source_research_ids": list(source_research_ids),
        "persona": persona,
        "platform": platform,
        "status": "pending",
        "variants": list(variants),
        "approval": {
            "transport": None,        # "discord" once dispatched
            "message_id": None,       # transport-neutral (Discord message id)
            "channel_id": None,       # the private approval channel
            "nonce": None,
            "dispatched_at": None,
            "decided_at": None,
            "decision": None,
            "revise_count": 0,
            "revise_note": None,
        },
    }


# ---------------------------------------------------------------------------
# Smoke check
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    assert slugify("Clinic Patient Retention!!") == "clinic-patient-retention"
    rid = make_run_id("no-show reduction", datetime(2026, 6, 16, tzinfo=IST))
    assert rid == "20260616_no-show-reduction", rid
    assert finding_id(rid, 3) == f"{rid}#3"
    cid = make_content_id("retention post", datetime(2026, 6, 16, 9, 5, tzinfo=IST))
    assert cid == "20260616-0905-retention-post", cid
    assert now_iso().endswith("+05:30")

    assert find_urls("see https://vytal.app/x and http://evil.com") == [
        "https://vytal.app/x",
        "http://evil.com",
    ]
    assert flag_unallowlisted_urls("https://vytal.app/x https://evil.com") == ["https://evil.com"]
    assert flag_unallowlisted_urls("https://docs.vytal.in/page") == []

    good_finding = {
        "id": f"{rid}#0",
        "type": "idea",
        "topic": "no-show reduction",
        "summary": "Reminders cut no-shows.",
        "source_url": "https://example.com/x",
        "raw_excerpt": "...",
        "tags": ["clinic"],
        "consumed": False,
    }
    assert validate_finding(good_finding) == []
    assert validate_finding({**good_finding, "type": "bogus"})

    run = {"run_id": rid, "run_date": now_iso(), "topic": "no-show reduction", "findings": [good_finding]}
    assert validate_research_run(run) == []

    draft = new_draft(
        cid,
        [good_finding["id"]],
        [
            {
                "idx": 0,
                "text": "post",
                "angle": "contrarian",
                "score": 0.8,
                "score_breakdown": {"hook": 0.8, "clarity": 0.8, "voice_match": 1.0, "cta": 0.8},
            }
        ],
    )
    assert validate_draft(draft) == [], validate_draft(draft)
    assert draft["status"] == "pending" and draft["approval"]["revise_count"] == 0

    decision = {"ts": now_iso(), "content_id": cid, "action": "approve", "decided_by": "arnav", "variant_idx": 0, "note": ""}
    assert validate_decision(decision) == []
    print("contracts OK")
