"""Deterministic intent classifier — NO LLM.

Classifies a (mention-stripped) Discord message into an operational Route, or
returns None so the message falls through to the normal Hermes agent loop.

Design rule (from the Review Board): be a binary "is this OBVIOUSLY operational?"
gate, not a mini-NLP. A lead request needs BOTH a verb and a target noun; anything
ambiguous returns None and lets the agent handle it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_COUNT = 100
DEFAULT_COUNT = 12

# A lead request must contain a verb AND a target noun (keeps false positives out).
_LEAD_VERBS = r"\b(find|scrape|get|source|research|pull|gather|collect)\b"
_LEAD_NOUNS = (
    r"\b(clinic|clinics|lead|leads|doctor|doctors|dentist|dentists|physio\w*|"
    r"hospital|hospitals|therapist|therapists|practice|practices|compan\w+|"
    r"business|businesses|prospect|prospects|provider|providers|salon|salons|gym|gyms)\b"
)
_STATUS_RE = re.compile(
    r"^\s*(status|queue|running\s+tasks?|tasks?\s+status|what'?s\s+running|jobs?)\s*\??\s*$",
    re.IGNORECASE,
)
# Specific enough to avoid stealing ordinary "write a message to my team" chat:
# the noun must be outreach/pitch/cold-email-or-message/personalized-email.
_OUTREACH_RE = re.compile(
    r"\b(generate|create|draft|write|compose|make)\b.{0,20}"
    r"\b(outreach|pitch|cold[\s-]*(?:email|message|dm)|personali[sz]ed\s+(?:email|message|pitch))\b",
    re.IGNORECASE,
)
_ROW_RE = re.compile(r"\brow\s+(\d+)\b", re.IGNORECASE)
_COUNT_RE = re.compile(r"\b(\d{1,6})\b")
_FILLER_RE = re.compile(r"\b(me|some|a|an|the|all|please|for|us|of|new|good|top)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    intent: str  # "lead" | "status"
    params: dict = field(default_factory=dict)


def classify(text: str) -> Route | None:
    """Return a Route for operational messages, else None (→ agent)."""
    t = (text or "").strip()
    if not t:
        return None
    if _STATUS_RE.match(t):
        return Route("status")
    if _OUTREACH_RE.search(t):
        params = {}
        mr = _ROW_RE.search(t)
        if mr:
            params["row"] = int(mr.group(1))
        return Route("outreach", params)
    if re.search(_LEAD_VERBS, t, re.IGNORECASE) and re.search(_LEAD_NOUNS, t, re.IGNORECASE):
        return Route("lead", _parse_lead(t))
    return None


def _parse_lead(t: str) -> dict:
    """Extract {count, target, location} with a count grab + ' in ' split.
    Conservative — leaves a usable target even when parsing is imperfect."""
    count = None
    m = _COUNT_RE.search(t)
    if m:
        count = max(1, min(MAX_COUNT, int(m.group(1))))

    body = re.sub(_LEAD_VERBS, " ", t, flags=re.IGNORECASE)
    if count is not None:
        body = _COUNT_RE.sub(" ", body, count=1)
    body = _FILLER_RE.sub(" ", body)
    body = re.sub(r"\s+", " ", body).strip(" .,!?")

    location = None
    target = body
    mloc = re.search(r"\bin\s+(.+)$", body, re.IGNORECASE)
    if mloc:
        location = mloc.group(1).strip(" .,!?") or None
        target = body[: mloc.start()].strip(" .,!?")

    return {
        "count": count if count is not None else DEFAULT_COUNT,
        "target": target or "clinics",
        "location": location,
    }
