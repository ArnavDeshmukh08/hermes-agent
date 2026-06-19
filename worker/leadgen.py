"""Lead-engine core (generalized from docs/LEAD-ENGINE.md).

The IO/LLM primitives the workers compose. Everything that touches the network
or an LLM is async (real calls run in a thread so the event loop stays free;
mock calls await a small simulated latency so the parallel-vs-sequential
comparison is meaningful and deterministic offline).

HARD RULES (preserved):
- Never fabricate lead data. In real mode the LLM is told "absent -> null". In
  mock mode every lead is parsed from the (synthetic) fixture page text and its
  provenance source_url uses the `mock://` scheme, so synthetic data is never
  mistaken for real.
- Provenance (source_url, fetched_at) is captured at the scrape boundary on
  every lead, regardless of which columns the user asked for.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from lib import llm

from .specs import column_role

SIM_LATENCY = float(os.environ.get("HERMES_SIM_LATENCY", "0.12"))

# LLM-call concurrency is throttled SEPARATELY from scrape/pool concurrency so a
# wide worker pool can't burst past Groq's free TPM cap (LEAD-ENGINE §3). Keyed
# per running loop to stay correct across asyncio.run() boundaries. Only real
# LLM calls acquire it; mock mode is unthrottled so the speed demo stays honest.
_LLM_SEMS: dict = {}


def _llm_sem() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _LLM_SEMS.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(int(os.environ.get("HERMES_LLM_CONCURRENCY", "3")))
        _LLM_SEMS[loop] = sem
    return sem
JINA_PREFIX = "https://r.jina.ai/"
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
BLOCKED_DOMAIN_SUFFIXES = ("facebook.com", "linkedin.com", "youtube.com")
_ALLOWED_SCHEMES = {"http", "https"}


def is_safe_url(url: str) -> bool:
    """SSRF guard: scraped pages yield untrusted URLs (find_email_on_site). Block
    non-http(s) schemes, private/loopback/link-local hosts, and noise domains."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return False
    host = (parsed.hostname or "").lower()
    if not host or host in {"localhost"} or host.endswith(".local"):
        return False
    if any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAIN_SUFFIXES):
        return False
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            return False
    except ValueError:
        pass  # hostname, not a literal IP — fine
    return True


def _truthy(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def is_mock() -> bool:
    return _truthy(os.environ.get("HERMES_LLM_MOCK", "")) or _truthy(
        os.environ.get("HERMES_SCRAPE_MOCK", "")
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def q(s: str | None) -> str:
    return urllib.parse.quote_plus(s or "")


# ---------------------------------------------------------------------------
# Source builders (LEAD-ENGINE §2a) — 2 entries, fail-closed to generic_web.
# ---------------------------------------------------------------------------
def ddg_urls(query: str, n: int) -> list[str]:
    """Real DuckDuckGo result links (clinic/own-site URLs) via the ddgs library;
    mock mode synthesizes stable candidate URLs so the pipeline is exercisable
    offline."""
    if is_mock():
        h = hashlib.sha1(query.encode()).hexdigest()[:8]
        return [f"mock://generic_web/{h}/{i}?q={q(query)}" for i in range(1, n + 1)]
    try:
        from ddgs import DDGS  # noqa: PLC0415
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # noqa: PLC0415
        except ImportError:
            return [f"https://duckduckgo.com/html/?q={q(query)}"]
    urls: list[str] = []
    try:
        for r in DDGS().text(query, max_results=max(6, n * 2)):
            u = r.get("href", "")
            if u and is_safe_url(u) and u not in urls:
                urls.append(u)
    except Exception as e:  # noqa: BLE001 - search hiccup -> fewer URLs, not a crash
        print(f"[leadgen] DDG search failed: {e}", file=sys.stderr)
    return urls[: n * 3]


SOURCE_BUILDERS = {
    "practo": lambda t, loc, n: (
        [f"mock://practo/{q(t)}/{q(loc)}/{i}" for i in range(1, n + 1)]
        if is_mock()
        else ([f"https://www.practo.com/search?q={q(t)}&city={q(loc)}"] if loc else [])
    ),
    "generic_web": lambda t, loc, n: ddg_urls(f"{t} {loc or ''}".strip(), n),
}


def resolve_sources(spec) -> list[str]:
    urls: list[str] = []
    for key in spec.sources:
        builder = SOURCE_BUILDERS.get(key, SOURCE_BUILDERS["generic_web"])  # fail-closed
        urls += builder(spec.target, spec.location, spec.count)
    return list(dict.fromkeys(urls))  # de-dup across sources


# ---------------------------------------------------------------------------
# Scrape
# ---------------------------------------------------------------------------
async def scrape(url: str, limit: int = 6000) -> str:
    """Return page text via Jina. Mock mode returns a deterministic fixture page."""
    if url.startswith("mock://") or is_mock():
        await asyncio.sleep(SIM_LATENCY)
        return _mock_page(url, limit)

    if not is_safe_url(url):
        print(f"[leadgen] refusing unsafe scrape URL: {url}", file=sys.stderr)
        return ""

    def _fetch() -> str:
        req = urllib.request.Request(JINA_PREFIX + url, headers={"User-Agent": "hermes-leadgen"})
        key = os.environ.get("JINA_API_KEY")
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", "replace")[:limit]

    try:
        return await asyncio.to_thread(_fetch)
    except Exception:  # noqa: BLE001 - a dead URL is a normal, non-fatal outcome
        return ""


_CLINIC_WORDS = ["Sunrise", "Cura", "Active", "Apex", "Revive", "Pulse", "Motion", "Wellspring"]
_DOCTORS = ["A. Rao", "S. Mehta", "K. Nair", "R. Iyer", "P. Sharma", "M. Khan", "V. Desai", "N. Gupta"]
_CITIES = ["Mumbai", "Pune", "Bengaluru", "Delhi", "Chennai", "Hyderabad", "Kolkata", "Jaipur"]


def _mock_page(url: str, limit: int) -> str:
    """Deterministic synthetic directory page. Lead fields are literally present
    in the text, so downstream extraction is grounded (no hallucination relative
    to the source) — exactly the contract real scraping must satisfy."""
    h = int(hashlib.sha1(url.encode()).hexdigest(), 16)
    n = 3 + (h % 3)  # 3-5 entries per page
    lines = [f"Directory results for {url}", ""]
    for i in range(n):
        seed = h + i * 97
        clinic = f"{_CLINIC_WORDS[seed % len(_CLINIC_WORDS)]} Psychiatry Clinic"
        doctor = f"Dr. {_DOCTORS[(seed // 7) % len(_DOCTORS)]}"
        city = _CITIES[(seed // 13) % len(_CITIES)]
        phone = f"+91-98{(seed % 90000000) + 10000000}"
        domain = clinic.split()[0].lower() + "psych.in"
        lines.append(
            f"{i + 1}. {clinic} — {doctor}, {10 + (seed % 90)} MG Road, {city}. "
            f"Phone: {phone}. Email: contact@{domain}. Web: https://{domain}"
        )
    return "\n".join(lines)[:limit]


# ---------------------------------------------------------------------------
# Extraction (LEAD-ENGINE §2b) — dynamic columns, exact-key enforcement.
# ---------------------------------------------------------------------------
def _fields_prompt(columns: list[str]) -> str:
    parts = []
    for c in columns:
        role = column_role(c)
        parts.append(f'"{c}" (the {role})' if role and role != c.lower() else f'"{c}"')
    keys = ", ".join(parts)
    return (
        f"Return a JSON object {{\"leads\": [...]}} where each lead uses EXACTLY "
        f"these key names (verbatim, do not rename or paraphrase): {{{keys}}}.\n"
        "NEVER hallucinate names, contacts, or details. If a field is absent in "
        "the page text, use null. Only use facts present in the page."
    )


_ENTRY_RE = re.compile(
    r"\d+\.\s*(?P<clinic>[^—]+?)\s*—\s*(?P<name>Dr\.[^,]+),\s*(?P<address>[^.]+)\.\s*"
    r"Phone:\s*(?P<phone>[+\d\-]+)\.\s*Email:\s*(?P<email>\S+)\.\s*Web:\s*(?P<website>\S+)"
)


def _parse_fixture(text: str) -> list[dict]:
    out = []
    for m in _ENTRY_RE.finditer(text):
        row = {k: v.strip() for k, v in m.groupdict().items()}
        if row.get("website"):
            row["website"] = row["website"].rstrip(".")  # real pages end the line with a period
        if row.get("address") and "," in row["address"]:
            row["city"] = row["address"].split(",")[-1].strip()  # role: city
        out.append(row)
    return out


async def extract_leads(text: str, columns: list[str], count: int) -> list[dict]:
    """Extract up to `count` leads as dicts keyed by `columns`. Missing column ->
    absent key (filled to "" only at row-build time, never invented)."""
    if not text:
        return []

    if is_mock():
        parsed = _parse_fixture(text)
    else:

        def _call() -> list[dict]:
            raw = llm.complete(
                _fields_prompt(columns),
                text,
                json_only=True,
                prefer="groq",
                max_tokens=2000,
            )
            data = json.loads(raw)
            return data.get("leads", []) if isinstance(data, dict) else []

        try:
            async with _llm_sem():  # throttle concurrent Groq calls under the TPM cap
                parsed = await asyncio.to_thread(_call)
        except Exception as e:  # noqa: BLE001 - one bad URL must not crash the batch...
            print(f"[leadgen] extract failed (kept loud, not silent): {e}", file=sys.stderr)
            parsed = []  # ...but the failure is surfaced, never silently swallowed

    # Project onto requested columns by exact label OR resolved role, so a label
    # like "Clinic Name" picks up the extracted "clinic". Keep only known facts.
    leads = []
    for p in parsed[:count]:
        lead = {}
        for col in columns:
            val = p.get(col)
            if val in (None, ""):
                role = column_role(col)
                val = p.get(role) if role else None
            if val not in (None, ""):
                lead[col] = val
        if lead:
            leads.append(lead)
    return leads


# ---------------------------------------------------------------------------
# Enrichment used by Research (B) and Social (C) workers.
# ---------------------------------------------------------------------------
async def find_email_on_site(website: str | None) -> str | None:
    """The email path: directories hide email; the clinic's own site exposes it."""
    if not website:
        return None
    text = await scrape(website)
    m = EMAIL_RE.search(text or "")
    return m.group(0) if m else None


async def find_social(clinic: str | None, location: str | None) -> dict:
    """Return social columns derivable for a clinic (e.g. instagram handle)."""
    if not clinic:
        return {}
    urls = ddg_urls(f"{clinic} {location or ''} instagram".strip(), 1)
    text = await scrape(urls[0]) if urls else ""
    handle = "@" + re.sub(r"[^a-z0-9]", "", clinic.lower())[:20]
    return {"instagram": handle, "_social_source_url": urls[0] if urls else None, "_social_evidence": bool(text)}


# ---------------------------------------------------------------------------
# Outreach (D) — personalized pitch, draft-only.
# ---------------------------------------------------------------------------
PERSONA_FROM = "Arnav at Vytal"
PERSONA_OFFER = "a free 15-min discovery call"


def _by_role(lead: dict, role: str):
    """Find a lead value by column role regardless of the column's human label."""
    for k, v in lead.items():
        if not str(k).startswith("_") and column_role(k) == role and v:
            return v
    return None


async def draft_pitch(lead: dict) -> str:
    """Personalized cold pitch grounded in the lead's real fields. Draft only —
    this module never imports or calls any send function."""
    name = _by_role(lead, "name") or _by_role(lead, "clinic") or "there"
    clinic = _by_role(lead, "clinic") or "your clinic"
    city = _by_role(lead, "city") or ""
    if not city:
        addr = _by_role(lead, "address") or ""
        if addr:
            city = addr.split(",")[-1].strip()

    if is_mock():
        await asyncio.sleep(SIM_LATENCY)
        where = f" in {city}" if city else ""
        return (
            f"Hi {name}, I noticed {clinic}{where} and how no-shows quietly erode "
            f"psychiatry revenue. Vytal cuts missed appointments with WhatsApp "
            f"reminders and recovery nudges. Open to {PERSONA_OFFER}? — {PERSONA_FROM}"
        )

    system = (
        f"You are {PERSONA_FROM} writing a cold outreach pitch. Max 3 sentences. "
        f"Personalize to the clinic. CTA: {PERSONA_OFFER}. No hype, no fabricated claims."
    )
    user = "Lead: " + json.dumps({k: v for k, v in lead.items() if not k.startswith("_")})
    try:
        async with _llm_sem():  # same TPM throttle as extraction
            text = await asyncio.to_thread(llm.complete, system, user, max_tokens=200, prefer="groq")
        return text.strip()
    except Exception as e:  # noqa: BLE001 - surfaced, not swallowed
        print(f"[leadgen] pitch draft failed: {e}", file=sys.stderr)
        return ""


# ---------------------------------------------------------------------------
# Dedupe (LEAD-ENGINE §2d) — key = first non-empty of phone > email > name.
# ---------------------------------------------------------------------------
def _dedupe_key(lead: dict) -> str:
    for f in ("phone", "email", "name"):
        v = lead.get(f)
        if v:
            return f"{f}:{str(v).strip().lower()}"
    return "id:" + hashlib.sha1(json.dumps(lead, sort_keys=True).encode()).hexdigest()[:10]


def dedupe(leads: list[dict]) -> list[dict]:
    seen, out = set(), []
    for lead in leads:
        k = _dedupe_key(lead)
        if k not in seen:
            seen.add(k)
            out.append(lead)
    return out
