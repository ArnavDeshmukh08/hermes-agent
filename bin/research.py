#!/usr/bin/env python3
"""Hermes Research Agent — Stage 1 harvester of the content pipeline.

Reads memory/research/_sources.json, collects findings from configured
sources/competitors/topics, gates them on keywords, summarizes (real mode)
or synthesizes deterministically (mock/offline mode), enforces the
source_url guardrail, validates against the frozen contract, and writes a
single immutable research run JSON to memory/research/<run_id>.json.

Deterministic path only (no agent loop). Designed to be relayed by a
`no_agent` cron to Discord/Telegram via the final stdout summary line.

CLI:  python bin/research.py [--topic "..."] [--offline]
Exit: 0 on success (including an empty run); non-zero only on fatal error
      (e.g. missing/malformed _sources.json).

Stdlib only.
"""

import sys
import os
import re
import json
import argparse
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import contracts, store, llm  # noqa: E402

# --- tuning constants (no magic numbers inline) -------------------------------
FETCH_TIMEOUT_SEC = 20
FETCH_USER_AGENT = "HermesResearchAgent/1.0 (+https://hermes.local)"
MAX_LLM_INPUT_CHARS = 6000          # keep LLM input lean (<~2k tokens)
RAW_EXCERPT_CHARS = 280             # short raw_excerpt kept on every finding
MAX_TOPIC_DERIVED_FINDINGS = 2      # 1-2 idea/hook findings derived from topics
DEFAULT_FINDING_TYPE = "trend"

# Map any non-enum source/finding type onto the closest contract type.
# FINDING_TYPES is the authority; this only handles aliases/fallbacks.
_TYPE_ALIASES = {
    "stat": "trend",
    "statistic": "trend",
    "news": "trend",
    "article": "idea",
    "blog": "idea",
    "post": "idea",
    "headline": "hook",
    "copy": "hook",
    "rival": "competitor",
    "company": "competitor",
}


# --- helpers ------------------------------------------------------------------
def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="research.py",
        description="Hermes Research Agent — collect findings into a research run.",
    )
    p.add_argument("--topic", default=None, help="Run topic (defaults to first topics[] entry).")
    p.add_argument("--offline", action="store_true", help="Force offline/mock mode (no network).")
    return p.parse_args(argv)


def is_offline(args):
    """Offline if --offline, or if HERMES_LLM_MOCK is truthy."""
    if args.offline:
        return True
    return _env_truthy(os.environ.get("HERMES_LLM_MOCK"))


def _env_truthy(val):
    if not val:
        return False
    return str(val).strip().lower() not in ("", "0", "false", "no", "off")


def map_type(raw_type):
    """Map an arbitrary source type onto the closest FINDING_TYPES value."""
    valid = set(getattr(contracts, "FINDING_TYPES", ("idea", "trend", "competitor", "hook")))
    if not raw_type:
        return DEFAULT_FINDING_TYPE if DEFAULT_FINDING_TYPE in valid else sorted(valid)[0]
    t = str(raw_type).strip().lower()
    if t in valid:
        return t
    if t in _TYPE_ALIASES and _TYPE_ALIASES[t] in valid:
        return _TYPE_ALIASES[t]
    return DEFAULT_FINDING_TYPE if DEFAULT_FINDING_TYPE in valid else sorted(valid)[0]


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s or "topic"


def strip_html(html):
    """Crude HTML -> text. Drop script/style, tags, collapse whitespace."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def keyword_tags(text, required):
    """Return required-keywords that appear in text (case-insensitive)."""
    low = (text or "").lower()
    return [kw for kw in required if kw and kw.lower() in low]


def passes_keyword_gate(text, required, blocked):
    """Keep only if >=1 required keyword present and 0 blocked keywords present."""
    low = (text or "").lower()
    if any(bk and bk.lower() in low for bk in blocked):
        return False
    if not required:
        return True
    return any(rk and rk.lower() in low for rk in required)


def has_url(value):
    """Guardrail check: non-empty real-looking URL."""
    if not value or not isinstance(value, str):
        return False
    v = value.strip()
    return v.startswith("http://") or v.startswith("https://")


def fetch_text(url):
    """Fetch a URL and return stripped text. Raises on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": FETCH_USER_AGENT})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        raw = resp.read()
    html = raw.decode(charset, errors="replace")
    return strip_html(html)


def competitor_entries(sources_cfg):
    """Normalize competitors[] (strings or dicts) into {name, url} entries."""
    out = []
    for c in sources_cfg.get("competitors", []) or []:
        if isinstance(c, str):
            name = c.strip()
            if not name:
                continue
            out.append({"name": name, "url": "https://" + slug(name) + ".com"})
        elif isinstance(c, dict):
            name = (c.get("name") or "").strip()
            url = (c.get("url") or "").strip()
            if not name and not url:
                continue
            if not url:
                url = "https://" + slug(name) + ".com"
            out.append({"name": name or url, "url": url})
    return out


def make_finding(run_id, n, ftype, topic, summary, source_url, raw_excerpt, tags):
    return {
        "id": contracts.finding_id(run_id, n),
        "type": map_type(ftype),
        "topic": topic,
        "summary": summary,
        "source_url": source_url,
        "raw_excerpt": (raw_excerpt or "")[:RAW_EXCERPT_CHARS],
        "tags": list(tags or []),
        "consumed": False,
    }


# --- collection: mock / offline path -----------------------------------------
def collect_offline(run_id, topic, cfg):
    """Synthesize deterministic findings without touching the network."""
    required = cfg.get("keywords_required", []) or []
    candidates = []  # (ftype, summary, source_url, raw_excerpt, tags)

    # One finding per configured source.
    for src in cfg.get("sources", []) or []:
        url = (src.get("url") or "").strip()
        if not has_url(url):
            continue  # guardrail: no real URL -> drop
        ftype = map_type(src.get("type"))
        excerpt = (
            f"Signal on '{topic}' from {url}: relevant to "
            f"{', '.join(required[:3]) or 'clinic growth'}."
        )
        summary = (
            f"[{ftype}] {topic}: source {url} surfaces material relevant to "
            f"Vytal's content engine."
        )
        tags = keyword_tags(excerpt + " " + topic, required) or [slug(topic)]
        candidates.append((ftype, summary, url, excerpt, tags))

    # 1-2 idea/hook findings derived from topics, anchored to competitor URLs.
    comps = competitor_entries(cfg)
    topics = cfg.get("topics", []) or [topic]
    derived = 0
    for i, t in enumerate(topics):
        if derived >= MAX_TOPIC_DERIVED_FINDINGS:
            break
        ftype = "idea" if i % 2 == 0 else "hook"
        # anchor to a competitor URL (or fall back to a real-looking topic URL)
        if comps:
            comp = comps[derived % len(comps)]
            url = comp["url"]
            anchor = comp["name"]
        else:
            url = "https://example.com/" + slug(t)
            anchor = t
        if not has_url(url):
            continue
        excerpt = f"Angle for '{t}' (vs {anchor}) — content opportunity for clinics."
        summary = (
            f"[{ftype}] {t}: derived angle for Vytal positioning against {anchor}."
        )
        tags = keyword_tags(excerpt + " " + t, required) or [slug(t)]
        candidates.append((ftype, summary, url, excerpt, tags))
        derived += 1

    return _candidates_to_findings(run_id, topic, candidates)


# --- collection: real path ----------------------------------------------------
def collect_real(run_id, topic, cfg):
    """Fetch each source, gate on keywords, summarize via llm.complete."""
    required = cfg.get("keywords_required", []) or []
    blocked = cfg.get("keywords_blocked", []) or []
    candidates = []

    for src in cfg.get("sources", []) or []:
        url = (src.get("url") or "").strip()
        ftype = map_type(src.get("type"))
        if not has_url(url):
            print(f"[research] skip source (no url): {src!r}", file=sys.stderr)
            continue
        try:
            text = fetch_text(url)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
            print(f"[research] fetch failed for {url}: {e}", file=sys.stderr)
            continue  # per-source isolation: skip + continue, never crash

        if not text:
            print(f"[research] empty content for {url}", file=sys.stderr)
            continue
        if not passes_keyword_gate(text, required, blocked):
            print(f"[research] keyword-gated out: {url}", file=sys.stderr)
            continue

        excerpt = text[:RAW_EXCERPT_CHARS]
        try:
            summary = _summarize(topic, ftype, text)
        except Exception as e:  # noqa: BLE001 - never let summarization crash the run
            print(f"[research] summarize failed for {url}: {e}", file=sys.stderr)
            summary = f"[{ftype}] {topic}: {excerpt}"

        tags = keyword_tags(text, required) or [slug(topic)]
        candidates.append((ftype, summary, url, excerpt, tags))

    return _candidates_to_findings(run_id, topic, candidates)


def _summarize(topic, ftype, text):
    lean = text[:MAX_LLM_INPUT_CHARS]
    system = (
        "You are a market-research summarizer for a healthcare-SaaS content engine. "
        "Summarize the page in 1-2 lean sentences focused on the topic. No preamble."
    )
    user = f"Topic: {topic}\nFinding type: {ftype}\n\nPage text:\n{lean}"
    out = llm.complete(system, user)
    out = (out or "").strip()
    return out or f"[{ftype}] {topic}: {text[:RAW_EXCERPT_CHARS]}"


def _candidates_to_findings(run_id, topic, candidates):
    """Apply the source_url guardrail and assign deterministic ids."""
    findings = []
    n = 0
    for ftype, summary, url, excerpt, tags in candidates:
        if not has_url(url):
            print(f"[research] DROP finding (no source_url): {summary[:60]!r}", file=sys.stderr)
            continue
        findings.append(make_finding(run_id, n, ftype, topic, summary, url, excerpt, tags))
        n += 1
    return findings


# --- main ---------------------------------------------------------------------
def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    store.ensure_tree()
    sources_path = store.memory_root() / "research" / "_sources.json"

    if not sources_path.exists():
        print(f"[research] FATAL: missing sources config: {sources_path}", file=sys.stderr)
        return 2
    try:
        cfg = store.read_json(sources_path)
    except (json.JSONDecodeError, ValueError, OSError) as e:
        print(f"[research] FATAL: malformed _sources.json: {e}", file=sys.stderr)
        return 2
    if not isinstance(cfg, dict):
        print("[research] FATAL: _sources.json must be a JSON object", file=sys.stderr)
        return 2

    # 1. pick the run topic
    topics = cfg.get("topics", []) or []
    topic = args.topic or (topics[0] if topics else "clinic growth")

    run_id = contracts.make_run_id(topic)
    offline = is_offline(args)
    mode = "mock" if offline else "real"

    # 2. collect findings (per-source isolation inside collectors)
    try:
        if offline:
            findings = collect_offline(run_id, topic, cfg)
        else:
            findings = collect_real(run_id, topic, cfg)
    except Exception as e:  # noqa: BLE001 - collection must never crash the run
        print(f"[research] collection error (continuing with empty run): {e}", file=sys.stderr)
        findings = []

    # 3. cap to max_findings_per_run
    cap = cfg.get("max_findings_per_run")
    if isinstance(cap, int) and cap >= 0:
        findings = findings[:cap]

    # 4. build run object
    run = {
        "run_id": run_id,
        "run_date": contracts.now_iso(),
        "topic": topic,
        "findings": findings,
    }

    # 5. validate against the frozen contract
    errors = contracts.validate_research_run(run)
    if errors:
        print("[research] FATAL: research run failed contract validation:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    # 6. persist
    try:
        out_path = store.save_research_run(run)
    except Exception as e:  # noqa: BLE001
        print(f"[research] FATAL: failed to write research run: {e}", file=sys.stderr)
        return 1

    # 7. final one-line summary (relayed to Telegram by a no_agent cron)
    counts = {}
    for f in findings:
        counts[f["type"]] = counts.get(f["type"], 0) + 1
    count_str = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"
    if not findings:
        print(
            f"[research] {mode} run for '{topic}': 0 findings "
            f"(empty but valid run) -> {out_path}"
        )
    else:
        print(
            f"[research] {mode} run for '{topic}': {len(findings)} findings "
            f"({count_str}) -> {out_path}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
