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
    # Activity bookings: "add a gym session", "log a workout", "create a yoga class".
    r"(?:add|put|create|book|insert|log|schedule)\b[^.!?]*"
    r"\b(?:session|workout|class|practice|training|rehearsal|lesson)\b"
    r"|"
    # Bare "schedule a X" / "book a X" (a meeting, a call, the dentist) — guarded so
    # "schedule a reminder/alarm" still falls through to the reminder branch.
    r"(?:schedule|book)\s+(?:a|an|my|the)\s+(?!reminder\b|alarm\b)\w+"
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
# Proactive control intents. Checked AFTER self_config and BEFORE conversational.
# ---------------------------------------------------------------------------
_PROACTIVE_CONTROL_RE = re.compile(
    r"\b(?:stop|pause|mute|silence|quiet|disable|turn\s+off|enable|turn\s+on|resume|unmute|unpause)\b"
    r"[^.!?]*\b(?:proactive|nudge|nudges|nudging|check[\s-]?ins?|pings?)\b"
    r"|"
    r"\b(?:turn\s+)?(?:proactive|nudges?|check[\s-]?ins?)\b[^.!?]*\b(?:on|off|stop|pause|mute|enable|disable)\b",
    re.IGNORECASE,
)
_PROACTIVE_STATUS_RE = re.compile(
    r"\b(?:are\s+(?:proactive\s+)?nudges?\s+(?:on|off|enabled|active|disabled)|"
    r"(?:my\s+|show\s+my\s+)?(?:proactive|nudge)\s+(?:status|settings)|"
    r"how\s+many\s+(?:proactive\s+)?(?:nudges?|check[\s-]?ins?)\s+(?:today|so\s+far)|"
    r"are\s+you\s+(?:going\s+to\s+)?nudg\w+\s+me)\b",
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
# Generic settings-change phrasing: any "change/set/update/modify/adjust the X setting",
# or a "configure ..." request. Routes even UNKNOWN settings to self_config so Jack can
# answer honestly ("I don't have that setting — here's what I can configure") instead of
# the agent brain refusing with a capability denial. Requires the literal word
# "setting(s)" (or the verb "configure") so ordinary chat like "change my plan" stays
# conversational.
_SELF_GENERIC_RE = re.compile(
    r"\b(?:"
    r"(?:change|set|update|modify|adjust|configure)\b[^.!?]*\bsettings?\b"
    r"|"
    r"configure\s+(?:my\s+|the\s+)?\w+"
    r")",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Health / Garmin intents. Checked AFTER proactive and BEFORE goal intents.
# Requires explicit health/device noun to avoid false positives.
# ---------------------------------------------------------------------------
_HEALTH_QUERY_RE = re.compile(
    r"\b(?:"
    # Sleep queries
    r"how\s+did\s+i\s+sleep\b|"
    r"my\s+sleep\s+score\b|"
    r"how\s+was\s+my\s+sleep\b|"
    r"sleep\s+(?:data|stats?|last\s+night)\b|"
    r"analyze\s+my\s+sleep\b|"
    # Steps / activity — require a time/count qualifier to avoid stealing "my steps goal"
    r"my\s+steps\s+(?:today|so\s+far|this\s+week|count)\b|"
    r"how\s+many\s+steps\s+today\b|"
    r"how\s+active\s+was\s+i\b|"
    r"my\s+workouts?\s+today\b|"
    # Garmin-specific
    r"my\s+garmin\b|"
    r"garmin\s+(?:data|stats?)\b|"
    r"(?:can\s+you\s+read|check|access)\s+my\s+garmin\b|"
    # Metrics
    r"body\s+battery\b|"
    r"my\s+heart\s+rate\b|"
    r"my\s+stress\s+(?:level|today)\b|"
    r"calories\s+(?:today|burned)\b|"
    # General health data
    r"health\s+(?:data|stats?|metrics?)\b"
    r")",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Goal intents. Checked AFTER calendar/reminder/self_config/proactive and
# BEFORE the conversational fallthrough. Query first (read-only), then update
# (mutating, needs an explicit verb), then create (broadest).
# ---------------------------------------------------------------------------
_GOAL_QUERY_RE = re.compile(
    r"\b(?:"
    r"how\s+am\s+i\s+(?:tracking|doing|progressing)\b|"
    r"what\s+are\s+my\s+goals\b|"
    r"(?:show|list|view)\s+(?:me\s+)?(?:my\s+)?goals?\b|"
    r"goal\s+(?:progress|status)\b|"
    r"am\s+i\s+on\s+track\b|"
    # "my goals" — plural only to avoid snagging "my goal is ..." (create phrasing)
    r"my\s+goals\b"
    r")",
    re.IGNORECASE,
)
# Mutating: requires an explicit action verb tied to the word 'goal'
# (or a bare 'goal done'), so ordinary chat never mutates a goal.
_GOAL_UPDATE_RE = re.compile(
    r"\b(?:"
    r"mark\b[^.!?]*\bgoal\b[^.!?]*\b(?:done|complete[d]?|finished)\b|"
    r"\bgoal\b\s+(?:done|complete[d]?|finished)\b|"
    r"(?:pause|resume|unpause|update|delete|remove)\b[^.!?]*\bgoal\b|"
    r"\bgoal\b[^.!?]*\b(?:done|complete[d]?|finished|paused?)\b"
    r")",
    re.IGNORECASE,
)
# Create: explicit goal-setting phrasings. "i want to / i'm trying to" require a
# following intent verb so they don't steal "i want to know the weather".
_GOAL_CREATE_RE = re.compile(
    r"\b(?:"
    r"new\s+goal\s*:|"
    r"my\s+goal\s+is\b|"
    r"set\s+(?:a\s+)?goal\b|"
    r"help\s+me\s+track\b|"
    r"i'?m\s+trying\s+to\s+(?:track|achieve|work\s+on|train|save|hit|reach|build|run|lose|gain|finish)\b|"
    r"i\s+want\s+to\s+(?:track|achieve|work\s+on|train|save|hit|reach|build)\b"
    r")",
    re.IGNORECASE,
)


def _proactive_control_value(t: str) -> str:
    """Return 'off' for stop/pause/mute/disable verbs, 'on' for enable/resume verbs.

    Checks for explicit on/off indicators or infers from verb choice."""
    off_verbs = re.compile(r"\b(stop|pause|mute|silence|quiet|disable)\b", re.IGNORECASE)
    on_verbs = re.compile(r"\b(enable|resume|unmute|unpause)\b", re.IGNORECASE)
    # Check for explicit 'on' or 'off' at the end or as a standalone word
    off_explicit = re.compile(r"\b(off|turn\s+off)\b", re.IGNORECASE)
    on_explicit = re.compile(r"\b(on|turn\s+on)\b", re.IGNORECASE)

    if off_verbs.search(t) or off_explicit.search(t):
        return "off"
    if on_verbs.search(t) or on_explicit.search(t):
        return "on"
    return "off"  # default for ambiguous cases like "nudges off"


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
    intent: str  # "lead" | "status" | "outreach" | "reminder" | "complaint" | "calendar" | "self_config" | "proactive" | "health_query" | "goal" | "conversational"
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
    if (_SELF_CONFIG_VERB_RE.search(t) and _SELF_SETTING_NOUN_RE.search(t)) or _SELF_GENERIC_RE.search(t):
        return Route("self_config", {"action": "set", "text": t})
    # Proactive control/status — after all operational intents
    # Status checked first so "are nudges on?" is classified as status, not control.
    if _PROACTIVE_STATUS_RE.search(t):
        return Route("proactive", {"action": "status", "text": t})
    if _PROACTIVE_CONTROL_RE.search(t):
        return Route("proactive", {"action": "control", "control": _proactive_control_value(t), "text": t})
    # Health / Garmin branch — after proactive intents, before goal intents.
    if _HEALTH_QUERY_RE.search(t):
        return Route("health_query", {"text": t})
    # Goal branch — after all other operational intents, before conversational.
    # Query checked first (read-only, harmless false-positive), then update
    # (requires explicit verb + "goal"), then create (broadest).
    if _GOAL_QUERY_RE.search(t):
        return Route("goal", {"action": "query", "text": t})
    if _GOAL_UPDATE_RE.search(t):
        return Route("goal", {"action": "update", "text": t})
    if _GOAL_CREATE_RE.search(t):
        return Route("goal", {"action": "create", "text": t})
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
