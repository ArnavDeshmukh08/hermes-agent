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
_CREATE_SYSTEM = (
    "You convert a person's goal statement into JSON. Extract EXACTLY these fields:\n"
    '  "title": short label (<=60 chars), e.g. "Run a marathon"\n'
    '  "type": one of fitness, financial, project, habit, other\n'
    '  "target": what success concretely looks like, e.g. "finish a full marathon"\n'
    '  "plan": the person\'s OWN stated plan, verbatim-ish; "" if none given\n'
    '  "metric": one of garmin_steps, garmin_runs, calendar, manual, none\n'
    '  "deadline": ISO date (YYYY-MM-DD) or natural text like "in 10 weeks", else null\n'
    "metric rules: fitness+steps->garmin_steps; fitness+running/marathon/5k/10k->garmin_runs;\n"
    "  recurring sessions/classes->calendar; everything else->manual.\n"
    "Return ONLY the JSON object. Do not invent a plan the person did not state."
)


def parse_create_goal(text: str, llm_complete_fn=None) -> dict:
    """Parse free text into goal fields.

    Returns a dict with keys: title, type, target, plan, metric, deadline.
    Uses the injected llm_complete_fn (if provided) to extract fields.
    Falls back to _minimal_goal on LLM failure or missing fn.
    Never raises.
    """
    text = (text or "").strip()
    fields = _minimal_goal(text)
    if llm_complete_fn is not None:
        try:
            raw = llm_complete_fn(
                _CREATE_SYSTEM,
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
    # Post-processing: normalise type, then re-infer metric as safety net.
    fields["type"] = _normalize_type(fields.get("type"))
    fields["metric"] = _infer_metric(text, fields["type"], fields.get("metric"))
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
