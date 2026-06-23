"""jack_goals/intents.py — LLM-powered parsing for goal management requests.

Design rules:
- lib.llm is NEVER imported here; the complete function is always injected so
  tests can stub it with no network.
- json and re are the only non-stdlib imports used.
- All three public functions NEVER raise — they return deterministic fallbacks.
- Confirmation strings are NOT built here — they live in the handler (Jack's voice).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

_logger = logging.getLogger("jack_goals.intents")

# ---------------------------------------------------------------------------
# Type / metric normalisation helpers
# ---------------------------------------------------------------------------
_VALID_TYPES = frozenset({"fitness", "financial", "project", "habit", "other"})
_VALID_METRICS = frozenset({"garmin_steps", "garmin_runs", "calendar", "manual", "none"})

_STEPS_RE = re.compile(r"\bsteps?\b", re.IGNORECASE)
_RUNS_RE = re.compile(r"\b(?:run|runs|running|marathon|5k|10k|km|jog|jogging|race|sprint)\b", re.IGNORECASE)
_CALENDAR_RE = re.compile(r"\b(?:session|sessions|class|classes|schedule|scheduled|appointment|appointments)\b", re.IGNORECASE)


def _normalize_type(t: str | None) -> str:
    if not t:
        return "other"
    t = t.strip().lower()
    return t if t in _VALID_TYPES else "other"


def _infer_metric(text: str, goal_type: str, existing: str | None) -> str:
    """Return the best metric label.

    Deterministic signal always wins for fitness goals when the text clearly
    mentions steps or running — this prevents a small model from returning
    "manual" when Garmin tracking is obviously the right choice.  For
    non-fitness goals and non-Garmin signals, trust the LLM when it returned
    a specific non-generic metric (i.e. not "manual" or "none").
    """
    t = text or ""
    # Fitness + clear Garmin signal → always override regardless of LLM answer.
    if goal_type == "fitness":
        if _STEPS_RE.search(t):
            return "garmin_steps"
        if _RUNS_RE.search(t):
            return "garmin_runs"
    # Calendar signal overrides for recurring sessions.
    if _CALENDAR_RE.search(t):
        return "calendar"
    # Trust any specific non-generic LLM answer.
    clean = (existing or "").strip().lower()
    if clean in _VALID_METRICS and clean not in ("none", "manual", ""):
        return clean
    # Fall back to manual for everything else.
    return "manual"


def _minimal_goal(text: str) -> dict:
    """Deterministic minimum-viable goal dict used as the fallback baseline."""
    return {
        "title": (text or "").strip()[:80] or "untitled goal",
        "type": "other",
        "target": "",
        "plan": (text or "").strip(),
        "metric": "manual",
        "deadline": None,
    }


# ---------------------------------------------------------------------------
# System prompt for goal creation
# ---------------------------------------------------------------------------

def _create_system(today_str: str) -> str:
    """Build the goal-parsing system prompt with today's date injected.

    The date is critical: without it the LLM defaults to a stale/wrong year
    when the user gives a partial date like "2nd August".
    """
    return (
        f"Today is {today_str}.\n"
        "You convert a person's goal statement into JSON. Extract EXACTLY these fields:\n"
        '  "title": short label (<=60 chars), e.g. "Run a marathon"\n'
        '  "type": one of fitness, financial, project, habit, other\n'
        '  "target": what success concretely looks like, e.g. "finish a full marathon"\n'
        '  "plan": the person\'s OWN stated plan, verbatim-ish; "" if none given\n'
        '  "metric": one of garmin_steps, garmin_runs, calendar, manual, none\n'
        '  "deadline": ISO date (YYYY-MM-DD) or natural text like "in 10 weeks", else null\n'
        "deadline rules: resolve partial/relative dates to the NEXT FUTURE occurrence.\n"
        f"  '2nd August' with no year → the next 2nd August that is in the future from {today_str}.\n"
        "  Never produce a deadline in the past. If only month+day are given, use the\n"
        "  current year if that date is still in the future; otherwise use next year.\n"
        "  If a full explicit year is stated (e.g. 'August 2 2027'), preserve it as-is.\n"
        "metric rules: fitness+steps->garmin_steps; fitness+running/marathon/5k/10k->garmin_runs;\n"
        "  recurring sessions/classes->calendar; everything else->manual.\n"
        "Return ONLY the JSON object. Do not invent a plan the person did not state."
    )


def _resolve_deadline(deadline: str | None, today: date) -> str | None:
    """Post-parse guard: if the LLM returned a past ISO date, correct it.

    Correction logic:
    - Parse as YYYY-MM-DD. If it doesn't parse, return as-is (natural text).
    - If the date is already in the future, return as-is.
    - If the date is in the past: take its month+day. If month+day hasn't
      passed this calendar year yet, use this year. Otherwise use next year.
    """
    if not deadline or not isinstance(deadline, str):
        return deadline
    # Only apply the guard to ISO-format dates; leave natural text alone.
    try:
        parsed = date.fromisoformat(deadline.strip())
    except ValueError:
        return deadline  # natural text like "in 10 weeks" — leave as-is
    if parsed >= today:
        return deadline  # already future — no correction needed
    # Past date: try same month/day this year first, then next year.
    try:
        corrected = parsed.replace(year=today.year)
    except ValueError:
        # Feb 29 in a non-leap year — use next year
        corrected = parsed.replace(year=today.year + 1)
    if corrected < today:
        corrected = corrected.replace(year=corrected.year + 1)
    _logger.warning(
        "deadline guard: LLM returned past date %r → corrected to %s",
        deadline,
        corrected.isoformat(),
    )
    return corrected.isoformat()


def parse_create_goal(text: str, llm_complete_fn=None, *, today: date | None = None) -> dict:
    """Parse free text into goal fields.

    Returns a dict with keys: title, type, target, plan, metric, deadline.
    Uses the injected llm_complete_fn (if provided) to extract fields.
    Falls back to _minimal_goal on LLM failure or missing fn.
    Never raises.

    Args:
        today: The reference date for deadline resolution. Defaults to
               date.today(). Pass explicitly in tests for determinism.
    """
    text = (text or "").strip()
    _today = today if today is not None else date.today()
    fields = _minimal_goal(text)
    if llm_complete_fn is not None:
        try:
            raw = llm_complete_fn(
                _create_system(_today.isoformat()),
                text,
                max_tokens=300,
                prefer="groq",
                json_only=True,
                timeout=20,
            )
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                for k in ("title", "type", "target", "plan", "metric", "deadline"):
                    v = parsed.get(k)
                    if v is not None:
                        fields[k] = v
        except Exception as exc:  # noqa: BLE001
            _logger.warning("parse_create_goal LLM failed: %r", exc)
    # Post-processing: normalise type, re-infer metric, guard past deadlines.
    fields["type"] = _normalize_type(fields.get("type"))
    fields["metric"] = _infer_metric(text, fields["type"], fields.get("metric"))
    fields["deadline"] = _resolve_deadline(fields.get("deadline"), _today)
    return fields


# ---------------------------------------------------------------------------
# Query parsing — deterministic, no LLM
# ---------------------------------------------------------------------------
_PROGRESS_RE = re.compile(
    r"\b(?:how\s+am\s+i\s+(?:tracking|doing|progressing)|on\s+track|progress|tracking)\b",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(r"\bstatus\b", re.IGNORECASE)

# Boilerplate query phrases to strip when extracting a goal hint.
_QUERY_BOILERPLATE_RE = re.compile(
    r"\b(?:"
    r"how\s+am\s+i\s+(?:tracking|doing|progressing)(?:\s+on)?\b|"
    r"what\s+are\s+my\s+goals?\b|"
    r"(?:show|list|view|check)\s+(?:me\s+)?(?:my\s+)?goals?\b|"
    r"goal\s+(?:progress|status|update)\b|"
    r"am\s+i\s+on\s+track(?:\s+(?:on|with|for))?\b|"
    r"how'?s?\s+my\b|"
    r"my\s+goals?\b"
    r")",
    re.IGNORECASE,
)


def parse_query_goal(text: str) -> dict:
    """Parse a goal query request deterministically (no LLM).

    Returns {"query_type": str, "goal_hint": str}.
    query_type is one of: "progress", "status", "list".
    goal_hint is the remaining text after stripping boilerplate — used by the
    handler to fuzzy-match a specific goal when query_type is "progress".
    """
    t = (text or "").strip()
    if _PROGRESS_RE.search(t):
        query_type = "progress"
    elif _STATUS_RE.search(t):
        query_type = "status"
    else:
        query_type = "list"
    # Strip boilerplate to get a usable hint.
    hint = _QUERY_BOILERPLATE_RE.sub(" ", t)
    hint = re.sub(r"\s+", " ", hint).strip(" .,!?").lower()
    return {"query_type": query_type, "goal_hint": hint}


# ---------------------------------------------------------------------------
# Update parsing — deterministic first pass + optional LLM fallback
# ---------------------------------------------------------------------------
_DONE_RE = re.compile(r"\b(done|complete[d]?|finish(?:ed)?)\b", re.IGNORECASE)
_PAUSE_RE = re.compile(r"\b(pause[d]?|hold|snooze[d]?)\b", re.IGNORECASE)
_RESUME_RE = re.compile(r"\b(resume[d]?|unpause[d]?|restart(?:ed)?)\b", re.IGNORECASE)
_DELETE_RE = re.compile(r"\b(delete[d]?|remove[d]?)\b", re.IGNORECASE)
_NOTE_RE = re.compile(r"\b(?:note|logged?|progress|update[d]?)\s*[:—\-]?\s*(.+)", re.IGNORECASE)

_UPDATE_SYSTEM = (
    "You parse a goal update request into JSON with exactly:\n"
    '  "goal_hint": the noun phrase identifying the goal (e.g. "marathon", "savings")\n'
    '  "action": one of done, pause, resume, delete, note, unknown\n'
    '  "note": the progress note text if action is "note", else ""\n'
    "Return ONLY the JSON object."
)


def parse_update_goal(text: str, llm_complete_fn=None) -> dict:
    """Parse a goal update/mutation request.

    Returns {"goal_hint": str, "updates": dict}.
    Two-tier: deterministic fast path first, LLM fallback for ambiguous cases.
    Never raises — returns {"goal_hint": text, "updates": {}} on total failure.
    """
    t = (text or "").strip()

    # -- deterministic fast path -------------------------------------------
    # Note detection first so "logged 3 miles on marathon goal" doesn't become done.
    note_m = _NOTE_RE.search(t)
    if note_m:
        note_text = note_m.group(1).strip()
        hint = _strip_update_verbs(t)
        return {"goal_hint": hint, "updates": {"progress_note": note_text}}

    if _DONE_RE.search(t):
        return {"goal_hint": _strip_update_verbs(t), "updates": {"status": "done"}}
    if _PAUSE_RE.search(t):
        return {"goal_hint": _strip_update_verbs(t), "updates": {"status": "paused"}}
    if _RESUME_RE.search(t):
        return {"goal_hint": _strip_update_verbs(t), "updates": {"status": "active"}}
    if _DELETE_RE.search(t):
        # Soft delete: map to "done" status (the store has no hard-delete surface).
        return {"goal_hint": _strip_update_verbs(t), "updates": {"status": "done"}}

    # -- LLM fallback for ambiguous phrasings ------------------------------
    if llm_complete_fn is not None:
        try:
            raw = llm_complete_fn(
                _UPDATE_SYSTEM,
                t,
                max_tokens=150,
                prefer="groq",
                json_only=True,
                timeout=20,
            )
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                action = str(parsed.get("action", "unknown")).lower()
                hint = str(parsed.get("goal_hint") or t).strip()
                note = str(parsed.get("note") or "").strip()
                if action == "done":
                    return {"goal_hint": hint, "updates": {"status": "done"}}
                if action == "pause":
                    return {"goal_hint": hint, "updates": {"status": "paused"}}
                if action == "resume":
                    return {"goal_hint": hint, "updates": {"status": "active"}}
                if action == "delete":
                    return {"goal_hint": hint, "updates": {"status": "done"}}
                if action == "note" and note:
                    return {"goal_hint": hint, "updates": {"progress_note": note}}
        except Exception as exc:  # noqa: BLE001
            _logger.warning("parse_update_goal LLM failed: %r", exc)

    # Total fallback — return the raw text as hint with no updates so the handler
    # can ask for clarification rather than mutate the wrong goal.
    return {"goal_hint": t, "updates": {}}


# ---------------------------------------------------------------------------
# Helper: strip common update verb phrases to isolate the goal noun phrase
# ---------------------------------------------------------------------------
_UPDATE_VERBS_RE = re.compile(
    r"\b(?:"
    r"mark(?:ed)?\b|"
    r"set\s+(?:as\s+)?|"
    r"(?:done|complete[d]?|finished?|paused?|resumed?|deleted?|removed?)\b|"
    r"(?:my|the)\s+|"
    r"goal\b"
    r")",
    re.IGNORECASE,
)


def _strip_update_verbs(t: str) -> str:
    """Strip update verb boilerplate to leave the goal noun phrase."""
    hint = _UPDATE_VERBS_RE.sub(" ", t)
    hint = re.sub(r"\s+", " ", hint).strip(" .,!?").lower()
    return hint or t.lower()


# ---------------------------------------------------------------------------
# confirm_create_message — Jack's voice confirmation for goal creation
# ---------------------------------------------------------------------------
_METRIC_LABELS: dict[str, str] = {
    "garmin_steps": "Garmin steps",
    "garmin_runs":  "Garmin runs",
    "calendar":     "calendar sessions",
    "manual":       "manual check-ins",
    "none":         "no metric",
}


def confirm_create_message(goal_fields: dict) -> str:
    """Generate Jack's warm second-person confirmation for a newly created goal.

    Framing guardrail: reflect the user's own title/target/plan back — never
    invent prescriptions or add training/diet/medical advice.
    """
    title = (goal_fields.get("title") or "your goal").strip()
    target = (goal_fields.get("target") or "").strip()
    plan = (goal_fields.get("plan") or "").strip()
    metric = (goal_fields.get("metric") or "none").strip().lower()
    deadline = (goal_fields.get("deadline") or "").strip()

    metric_label = _METRIC_LABELS.get(metric, metric)
    deadline_str = f" · by {deadline}" if deadline else ""
    target_str = f" Target: {target}." if target else ""
    plan_str = " Your plan's saved." if plan else ""

    return (
        f"Got it — I'm tracking: {title}{deadline_str}.{target_str} "
        f"Metric: {metric_label}.{plan_str} I'll keep you honest."
    )
