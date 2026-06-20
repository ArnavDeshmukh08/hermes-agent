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
# Reminder phrasing. Checked BEFORE lead/outreach so "remind me to find leads"
# is a reminder, not a lead. Split into three sub-actions so a bare "reminder"
# (which over-matches ordinary chat) never triggers on its own — every branch
# requires an explicit verb/phrase.
#
# list: queries about existing reminders ("what reminders do I have").
_REMINDER_LIST_RE = re.compile(
    r"\b(?:(?:what|which|any)\s+reminders?\b|"
    r"(?:list|show|see|view)\s+(?:my\s+|all\s+|the\s+)?reminders?\b|"
    r"my\s+reminders?\b)",
    re.IGNORECASE,
)
# cancel: removing an existing reminder ("cancel the call mom reminder").
_REMINDER_CANCEL_RE = re.compile(
    r"\b(?:cancel|delete|remove|forget|clear|drop)\b[^.!?]*\breminders?\b",
    re.IGNORECASE,
)
# set: creating a reminder. Requires an explicit "remind/reminder/alert/alarm"
# trigger with an accompanying verb or preposition — a bare "reminder" won't match.
_REMINDER_SET_RE = re.compile(
    r"\b(?:remind\s+me\b|"
    r"reminder\s+(?:for|to|about)\b|"
    r"don'?t\s+let\s+me\s+forget\b|"
    r"set\s+(?:up\s+)?an?\s+(?:alarm|reminder)\b|"
    r"alert\s+me\s+(?:to|when|about|that|if)\b)",
    re.IGNORECASE,
)
# Complaint / feedback markers. Checked FIRST in classify() — a complaint about a
# missed reminder ("you didn't remind me ...") contains "remind me" and would
# otherwise be misread as a NEW reminder command and set a bogus reminder.
_COMPLAINT_RE = re.compile(
    r"\b(?:you\s+didn'?t\b|"
    r"you\s+forgot\b|"
    r"why\s+didn'?t\s+you\b|"
    r"you\s+never\s+(?:reminded|told|said|asked|sent|warned|mentioned)\b|"
    r"you\s+said\s+you\s+would\b|"
    r"i\s+can'?t\s+believe\s+you\b|"
    r"you\s+were\s+supposed\s+to\b)",
    re.IGNORECASE,
)
# Calendar intents. The noun (calendar/schedule/agenda/appointment/meeting/event)
# is REQUIRED to avoid stealing ordinary chat ("what's happening today?").
# _CALENDAR_RE matches any calendar-management request (add OR list).
# _CALENDAR_LIST_RE matches list/query requests specifically so the handler can
# choose between "add" and "list" actions.
_CALENDAR_RE = re.compile(
    r"\b(?:"
    r"(?:add|put|create|book|insert|log|schedule)\b[^.!?]*"
    r"\b(?:calendar|appointment|meeting|event)\b"
    r"|"
    r"(?:what'?s?\s+on|show|list|view|check|see)\b[^.!?]*"
    r"\b(?:calendar|schedule|agenda)\b"
    r"|"
    r"\b(?:my\s+)?(?:calendar|agenda|schedule)\b[^.!?]*"
    r"\b(?:today|tomorrow|this\s+week|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r")",
    re.IGNORECASE,
)
_CALENDAR_LIST_RE = re.compile(
    r"\b(?:what'?s?\s+on|show|list|view|check|see)\b[^.!?]*"
    r"\b(?:calendar|schedule|agenda)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Self-config intents. Checked AFTER calendar/reminder so those still win.
# ---------------------------------------------------------------------------
# status: asking whether Jack/systems are up. Distinct from task-queue _STATUS_RE
# (which only matches bare single-word inputs like "status" or "queue").
_SELF_STATUS_RE = re.compile(
    r"\b(?:"
    r"are\s+you\s+(?:running\s+|doing\s+)?(?:ok|okay|alright|good)\b|"
    r"what'?s?\s+your\s+status\b|"
    r"you\s+(?:up|alive|online)\s*\??|"
    r"system\s+status\b|"
    r"everything\s+(?:ok|running)\b"
    r")",
    re.IGNORECASE,
)
# list: asking what settings are available.
_SELF_LIST_RE = re.compile(
    r"\b(?:"
    r"what\s+can\s+i\s+configure\b|"
    r"what\s+(?:can|do)\s+you\s+let\s+me\s+(?:change|configure)\b|"
    r"list\s+(?:your\s+)?settings\b|"
    r"(?:show|what\s+are)\s+your\s+settings\b"
    r")",
    re.IGNORECASE,
)
# set: a change request to a known setting noun + a config verb.
# Guard: message must reference a known setting noun AND a config verb.
_SELF_SETTING_NOUN_RE = re.compile(
    r"\b(?:briefing(?:\s+time)?|weather|news|memory|reminder\s+(?:poll|frequency|interval))\b",
    re.IGNORECASE,
)
_SELF_CONFIG_VERB_RE = re.compile(
    r"\b(?:change|set|update|turn\s+on|turn\s+off|enable|disable|switch\s+on|switch\s+off)\b",
    re.IGNORECASE,
)


def _reminder_action(t: str) -> str | None:
    """Return the reminder sub-action ('set' | 'list' | 'cancel') for `t`, or
    None if `t` is not a reminder request. Order matters: cancel and list are
    checked before set so "cancel the X reminder" / "list my reminders" win over
    a stray set-style match."""
    if _REMINDER_CANCEL_RE.search(t):
        return "cancel"
    if _REMINDER_LIST_RE.search(t):
        return "list"
    if _REMINDER_SET_RE.search(t):
        return "set"
    return None


@dataclass(frozen=True)
class Route:
    intent: str  # "lead" | "status" | "outreach" | "reminder" | "complaint" | "calendar" | "self_config" | "conversational"
    params: dict = field(default_factory=dict)


def classify(text: str) -> Route | None:
    """Classify a message into a Route.

    Operational intents (status / reminder / outreach / lead / calendar) match
    specific patterns; everything else non-empty becomes **conversational** so
    NOTHING falls through to the framework agent loop (which 413s on Groq's
    12k TPM). Only truly empty input returns None."""
    t = (text or "").strip()
    if not t:
        return None
    # Complaint guard FIRST: a feedback message like "you didn't remind me ..."
    # contains a reminder trigger but must never become a reminder/lead/status.
    if _COMPLAINT_RE.search(t):
        return Route("complaint", {"text": t})
    if _STATUS_RE.match(t):
        return Route("status")
    # Calendar branch — checked BEFORE reminder so "add dentist appointment to
    # my calendar" wins over any incidental reminder-style phrasing. The noun
    # guard (calendar/appointment/meeting/event) keeps ordinary chat out.
    if _CALENDAR_RE.search(t):
        action = "list" if _CALENDAR_LIST_RE.search(t) else "add"
        return Route("calendar", {"action": action, "text": t})
    action = _reminder_action(t)
    if action is not None:
        return Route("reminder", {"action": action, "text": t})
    if _OUTREACH_RE.search(t):
        params = {}
        mr = _ROW_RE.search(t)
        if mr:
            params["row"] = int(mr.group(1))
        return Route("outreach", params)
    if re.search(_LEAD_VERBS, t, re.IGNORECASE) and re.search(_LEAD_NOUNS, t, re.IGNORECASE):
        return Route("lead", _parse_lead(t))
    # Self-config branch — after all operational intents so calendar/reminder/lead
    # still win for their phrasings. Three actions: status / list / set.
    if _SELF_STATUS_RE.search(t):
        return Route("self_config", {"action": "status", "text": t})
    if _SELF_LIST_RE.search(t):
        return Route("self_config", {"action": "list", "text": t})
    if _SELF_CONFIG_VERB_RE.search(t) and _SELF_SETTING_NOUN_RE.search(t):
        return Route("self_config", {"action": "set", "text": t})
    return Route("conversational", {"text": t})


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
