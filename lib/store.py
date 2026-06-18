"""Hermes Prime — Stream A persistence layer.

Atomic JSON writes (tmp + os.replace), append-only JSONL, and the memory-tree
helpers. Single-writer-per-file is assumed by callers; we keep each write
atomic so a crash never leaves a half-written file.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from lib import contracts

# ---------------------------------------------------------------------------
# Roots / tree
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent


def memory_root():
    """env HERMES_MEMORY_ROOT, else <repo>/memory."""
    env = os.environ.get("HERMES_MEMORY_ROOT")
    if env:
        return Path(env)
    return _REPO_ROOT / "memory"


def research_dir():
    return memory_root() / "research"


def content_dir():
    return memory_root() / "content"


def approvals_dir():
    return memory_root() / "approvals"


def voice_dir():
    return memory_root() / "voice"


def approved_dir():
    return memory_root() / "approved"


def ensure_tree():
    """Idempotently create the memory tree + seed files. Safe from any stream."""
    for d in (research_dir(), content_dir(), approvals_dir(), voice_dir(), approved_dir()):
        d.mkdir(parents=True, exist_ok=True)
    q = queue_path()
    if not q.exists():
        atomic_write_json(q, {"updated_at": None, "pending": []})
    dj = approvals_dir() / "decisions.jsonl"
    if not dj.exists():
        # create an empty append-only log
        with open(dj, "a", encoding="utf-8"):
            pass


# ---------------------------------------------------------------------------
# Atomic primitives
# ---------------------------------------------------------------------------
def atomic_write_json(path, obj):
    """Write JSON to a temp file in the same dir, then os.replace into place."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def append_jsonl(path, obj):
    """Append one JSON line using O_APPEND for atomic single-line writes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _now_iso():
    return datetime.now(contracts.IST).isoformat()


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------
def save_research_run(run):
    errs = contracts.validate_research_run(run)
    if errs:
        raise ValueError("invalid research run: " + "; ".join(errs))
    ensure_tree()
    path = research_dir() / f"{run['run_id']}.json"
    atomic_write_json(path, run)
    return path


def _run_date_dt(run):
    raw = run.get("run_date")
    try:
        dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=contracts.IST)
    return dt


def load_findings(*, unconsumed_only=True, days=14):
    """Flatten findings across runs (newest run_date first).

    Attaches finding['_run_file'] (path str). Filters consumed when
    unconsumed_only; filters to runs within `days` when days is not None.
    """
    rdir = research_dir()
    if not rdir.exists():
        return []

    cutoff = None
    if days is not None:
        cutoff = datetime.now(contracts.IST).timestamp() - days * 86400

    runs = []
    for fp in rdir.glob("*.json"):
        if fp.name.startswith("_"):
            continue  # skip seeds like _sources.json
        try:
            run = read_json(fp)
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(run, dict) or "findings" not in run:
            continue
        dt = _run_date_dt(run)
        if cutoff is not None and dt is not None and dt.timestamp() < cutoff:
            continue
        runs.append((dt, fp, run))

    # newest run_date first; runs with unparsable dates sort last
    def _key(item):
        dt = item[0]
        return dt.timestamp() if dt is not None else float("-inf")

    runs.sort(key=_key, reverse=True)

    out = []
    for _dt, fp, run in runs:
        for finding in run.get("findings", []):
            if not isinstance(finding, dict):
                continue
            if unconsumed_only and finding.get("consumed"):
                continue
            f = dict(finding)
            f["_run_file"] = str(fp)
            out.append(f)
    return out


def mark_consumed(finding_ids):
    """Set consumed=True for matching finding ids across run files (atomic rewrite)."""
    targets = set(finding_ids)
    if not targets:
        return
    rdir = research_dir()
    if not rdir.exists():
        return
    for fp in rdir.glob("*.json"):
        if fp.name.startswith("_"):
            continue
        try:
            run = read_json(fp)
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(run, dict) or "findings" not in run:
            continue
        changed = False
        for finding in run.get("findings", []):
            if isinstance(finding, dict) and finding.get("id") in targets and not finding.get("consumed"):
                finding["consumed"] = True
                changed = True
        if changed:
            atomic_write_json(fp, run)


# ---------------------------------------------------------------------------
# Content / drafts
# ---------------------------------------------------------------------------
def content_path(content_id):
    return content_dir() / f"{content_id}.json"


def save_draft(draft):
    errs = contracts.validate_draft(draft)
    if errs:
        raise ValueError("invalid draft: " + "; ".join(errs))
    ensure_tree()
    path = content_path(draft["id"])
    atomic_write_json(path, draft)
    return path


def load_draft(content_id):
    path = content_path(content_id)
    if not path.exists():
        return None
    return read_json(path)


def update_draft(content_id, mutate):
    """Load, apply mutate(draft) (may return new draft or mutate in place),
    validate, atomic-write, and return the result."""
    draft = load_draft(content_id)
    if draft is None:
        raise FileNotFoundError(f"draft not found: {content_id}")
    result = mutate(draft)
    if result is None:
        result = draft
    errs = contracts.validate_draft(result)
    if errs:
        raise ValueError("invalid draft after mutate: " + "; ".join(errs))
    atomic_write_json(content_path(content_id), result)
    return result


def iter_drafts(*, status=None):
    cdir = content_dir()
    if not cdir.exists():
        return []
    out = []
    for fp in sorted(cdir.glob("*.json")):
        try:
            d = read_json(fp)
        except (json.JSONDecodeError, OSError):
            continue
        if status is not None and d.get("status") != status:
            continue
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------
def queue_path():
    return approvals_dir() / "queue.json"


def read_queue():
    path = queue_path()
    if not path.exists():
        return {"updated_at": None, "pending": []}
    q = read_json(path)
    q.setdefault("updated_at", None)
    q.setdefault("pending", [])
    return q


def _write_queue(q):
    q["updated_at"] = _now_iso()
    atomic_write_json(queue_path(), q)


def enqueue(item):
    ensure_tree()
    q = read_queue()
    q["pending"].append(item)
    _write_queue(q)


def dequeue(content_id):
    q = read_queue()
    before = len(q["pending"])
    q["pending"] = [p for p in q["pending"] if p.get("content_id") != content_id]
    if len(q["pending"]) != before:
        _write_queue(q)


def find_pending(content_id):
    for p in read_queue().get("pending", []):
        if p.get("content_id") == content_id:
            return p
    return None


def append_decision(decision):
    errs = contracts.validate_decision(decision)
    if errs:
        raise ValueError("invalid decision: " + "; ".join(errs))
    ensure_tree()
    append_jsonl(approvals_dir() / "decisions.jsonl", decision)


def decisions():
    return read_jsonl(approvals_dir() / "decisions.jsonl")


# ---------------------------------------------------------------------------
# Approved repo
# ---------------------------------------------------------------------------
def approved_path(content_id):
    return approved_dir() / f"{content_id}.md"


def approved_exists(content_id):
    return approved_path(content_id).exists()


def _frontmatter_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(str(x) for x in v) + "]"
    return str(v)


def write_approved(content_id, frontmatter, body):
    """Write '--- k: v ... --- \\n body' to approved/<id>.md (atomic)."""
    ensure_tree()
    lines = ["---"]
    for k, v in frontmatter.items():
        lines.append(f"{k}: {_frontmatter_value(v)}")
    lines.append("---")
    lines.append("")
    text = "\n".join(lines) + str(body)
    if not text.endswith("\n"):
        text += "\n"
    path = approved_path(content_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


# ---------------------------------------------------------------------------
# Smoke check
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    ensure_tree()
    assert queue_path().exists()
    assert (approvals_dir() / "decisions.jsonl").exists()

    rid = contracts.make_run_id("smoke topic")
    finding = {
        "id": contracts.finding_id(rid, 0),
        "type": "idea",
        "topic": "smoke topic",
        "summary": "a finding",
        "source_url": "https://example.com",
        "raw_excerpt": "...",
        "tags": ["x"],
        "consumed": False,
    }
    run = {"run_id": rid, "run_date": contracts.now_iso(), "topic": "smoke topic", "findings": [finding]}
    save_research_run(run)
    flat = load_findings(unconsumed_only=True, days=14)
    assert any(f["id"] == finding["id"] and "_run_file" in f for f in flat), "load_findings failed"
    mark_consumed([finding["id"]])
    assert all(f["id"] != finding["id"] for f in load_findings(unconsumed_only=True))

    cid = contracts.make_content_id("smoke")
    draft = contracts.new_draft(
        cid,
        [finding["id"]],
        [{"idx": 0, "text": "hello", "angle": "story", "score": 0.5,
          "score_breakdown": {"hook": 0.5, "clarity": 0.5, "voice_match": 0.5, "cta": 0.5}}],
    )
    save_draft(draft)
    assert load_draft(cid)["id"] == cid

    def _approve(d):
        d["status"] = "approved"
        return d

    assert update_draft(cid, _approve)["status"] == "approved"
    assert any(d["id"] == cid for d in iter_drafts(status="approved"))

    enqueue({"content_id": cid, "nonce": "abc"})
    assert find_pending(cid) is not None
    append_decision({"ts": contracts.now_iso(), "content_id": cid, "action": "approve",
                     "decided_by": "arnav", "variant_idx": 0, "note": "ok"})
    assert decisions()[-1]["content_id"] == cid
    dequeue(cid)
    assert find_pending(cid) is None

    write_approved(cid, {"id": cid, "persona": "jack", "approved": True}, "Body text here.\n")
    assert approved_exists(cid)

    # cleanup smoke artifacts so repeated runs stay clean
    for p in (content_path(cid), approved_path(cid), research_dir() / f"{rid}.json"):
        try:
            p.unlink()
        except OSError:
            pass
    print("store OK", file=sys.stderr)
    print("store OK")
