"""ProactiveReasoner — LLM-driven reasoning over a pre-built context string.

Architecture split:
  - gather_context()    : module-level function, runs on the VPS (has Qdrant/reminders/calendar).
                          Returns a formatted string ready to embed in the reasoning prompt.
  - ProactiveReasoner   : pure reasoning class, runs on any machine (Mac or VPS).
                          Takes a pre-built context string, calls Mac Ollama, returns ProactiveItems.
                          Has ZERO data dependencies — never touches Qdrant, reminders, or calendar.

The scheduler calls gather_context() locally (VPS has Qdrant), then passes the resulting
string to ProactiveReasoner.reason(context_str) which calls the Mac LLM.
"""

from __future__ import annotations
import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from proactive.scorer import ProactiveItem, P1, P2, P3, SCORE_P1, SCORE_P2, SCORE_P3

_IST = timezone(timedelta(hours=5, minutes=30))
_MAC_OLLAMA_TIMEOUT_S = 30
_MAX_MEMORIES = 40
_MEMORY_SEARCH_QUERIES = ["Arnav", "goals", "health", "relationships", "Vytal startup", "work", "deadlines"]
_MEMORY_SEARCH_LIMIT = 12
_RAW_LOG_CHARS = 200
_VALID_PRIORITIES = (P1, P2, P3)
_logger = logging.getLogger("proactive.reasoner")
_MAX_GOALS = 12  # cap the goals block so the prompt stays bounded
# Kept outside the f-string in gather_context() to avoid double-brace escaping
# errors and make the schema independently readable.
# nudge_type is mapped by domain so the LLM understands the intent, not just
# an opaque enum — this helps small models pick the right value consistently.
_RESPONSE_SCHEMA_HINT = (
    'Return ONLY a JSON object: {"nudges": [...]} where each nudge has these fields:\n'
    '  "priority": "P1" or "P2" or "P3"\n'
    '  "message": the text to send (concise, actionable)\n'
    '  "nudge_type": pick the right value for the domain that triggered this:\n'
    '    - fitness/exercise/gym → "gym"\n'
    '    - checking in with a person, relationship → "relationship_followup"\n'
    '    - work project, goal, deadline, study → "goal_nudge"\n'
    '    - imminent calendar event (within 2h) → "calendar_prep"\n'
    '    - sleep, hydration, health habit → "health_nudge"\n'
    '  "alone": true if P1 or urgent, false otherwise\n'
    '  "reasoning": which domain (1-5) triggered this and exactly why it is timely NOW\n'
    'If nothing qualifies, return {"nudges": []}.'
)

# Keyword → canonical nudge_type. Used as a fallback when the LLM returns
# a generic type like "reminder" or "notification" — inferred from message content.
_KEYWORD_TO_CANONICAL: dict[str, str] = {
    "gym": "gym",
    "workout": "gym",
    "exercise": "gym",
    "fitness": "gym",
    "siddhi": "relationship_followup",
    "relationship": "relationship_followup",
    "vytal": "goal_nudge",
    "stocksage": "goal_nudge",
    "masters": "goal_nudge",
    "goal": "goal_nudge",
    "deadline": "goal_nudge",
    "project": "goal_nudge",
    "sleep": "health_nudge",
    "hydration": "health_nudge",
    "water": "health_nudge",
    "health": "health_nudge",
}
_GENERIC_NUDGE_TYPES = frozenset({"reminder", "notification", "alert", "nudge", "update", "general"})

_SYSTEM_PROMPT = (
    "You are Jack — Arnav's AI chief of staff. You know everything about him from his memory, "
    "calendar, and reminders. Your job each cycle: methodically check every life domain "
    "(relationships, goals, calendar, health) and decide if anything is GENUINELY worth surfacing "
    "right now. You are proactive but not annoying, and you strongly err on the side of silence. "
    "Never surface something just because it is the most visible item (e.g. a calendar event) — "
    "a nudge is worth sending only if it is timely, actionable, or prevents a real miss. "
    "Most cycles should end with no nudge at all. When in doubt, stay silent."
)


def gather_context(
    memory_client: Any,
    store: Any,
    calendar_client: Any,
    queue: Any,
    user_id: str,
    now: datetime,
    *,
    already_sent: list[str] | None = None,
) -> str:
    """Collect memory/calendar/reminders/time into a formatted string.

    Run this on the VPS where Qdrant and reminders are local.
    Returns a string ready to embed in the reasoning prompt.
    """
    # --- memories ---
    memories: list[str] = []
    try:
        seen: set[str] = set()
        for query in _MEMORY_SEARCH_QUERIES:
            try:
                results = memory_client.search(query, user_id=user_id, limit=_MEMORY_SEARCH_LIMIT)
                for r in results:
                    text = r.get("memory") if isinstance(r, dict) else None
                    if text and text not in seen:
                        seen.add(text)
                        memories.append(text)
                        if len(memories) >= _MAX_MEMORIES:
                            break
            except Exception as exc:
                _logger.warning("gather_context: memory query %r failed: %s: %s", query, type(exc).__name__, exc)
            if len(memories) >= _MAX_MEMORIES:
                break
    except Exception as exc:
        _logger.warning("gather_context: memory collection failed: %s: %s", type(exc).__name__, exc)
        memories = []

    # --- calendar (hard-filtered to 2-hour window) ---
    # Only events starting within 2 hours are shown. Events further away are
    # completely omitted — the model cannot surface what it cannot see.
    calendar_text: str = ""
    try:
        if calendar_client is not None:
            events = calendar_client.list_events(day=now) or []
            within_2h_lines: list[str] = []
            for ev in events:
                start_info = ev.get("start", {})
                start_str = start_info.get("dateTime") or start_info.get("date") or ""
                title = ev.get("summary", "(no title)")
                if not start_str:
                    continue
                try:
                    if "T" in start_str:
                        start_dt = datetime.fromisoformat(start_str)
                        if start_dt.tzinfo is None:
                            start_dt = start_dt.replace(tzinfo=timezone.utc)
                        hours_until = (start_dt - now).total_seconds() / 3600
                        if 0 <= hours_until <= 2:
                            time_str = start_str.split("T")[1][:5]
                            within_2h_lines.append(
                                f"{time_str} {title} (in {hours_until:.1f}h — prep if needed)"
                            )
                except Exception:
                    pass
            calendar_text = "\n".join(within_2h_lines) if within_2h_lines else ""
    except Exception as exc:
        _logger.warning("gather_context: calendar fetch failed: %s: %s", type(exc).__name__, exc)
        calendar_text = ""

    # --- reminders ---
    reminders_text: str = ""
    try:
        if store is not None:
            pending = store.list_pending(user_id)
            if pending:
                lines = [f"- {r['message']} (fires {r['fire_at']})" for r in pending]
                reminders_text = "\n".join(lines)
    except Exception as exc:
        _logger.warning("gather_context: reminders fetch failed: %s: %s", type(exc).__name__, exc)
        reminders_text = ""

    # --- active goals ---
    goals_text: str = ""
    try:
        from jack_goals.store import list_active_goals  # lazy — jack_goals may not exist yet on VPS
        active_goals = list_active_goals()
        if active_goals:
            active_goals = active_goals[:_MAX_GOALS]
            goal_lines: list[str] = []
            for g in active_goals:
                metric_data = ""
                if g.metric == "garmin_steps":
                    try:
                        from integrations.garmin import GarminClient
                        stats = GarminClient().get_stats()
                        if stats and stats.get("steps"):
                            metric_data = f" | today's steps: {stats['steps']}"
                    except Exception:
                        pass
                elif g.metric == "garmin_runs":
                    try:
                        from integrations.garmin import GarminClient
                        stats = GarminClient().get_stats()
                        if stats and stats.get("distance_km"):
                            metric_data = f" | today's distance: {stats['distance_km']:.1f}km"
                    except Exception:
                        pass
                deadline_str = f" | deadline: {g.deadline}" if g.deadline else ""
                plan_str = f" | plan: {g.plan[:100]}" if g.plan else ""
                notes_str = ""
                if g.progress_notes:
                    notes_str = f" | last note: {g.progress_notes[-1][:80]}"
                goal_lines.append(
                    f"- [{g.type}] {g.title} | target: {g.target}{deadline_str}{plan_str}{metric_data}{notes_str}"
                )
            goals_text = "\n".join(goal_lines)
    except ImportError:
        pass  # jack_goals not available on this VPS yet
    except Exception as exc:
        _logger.warning("gather_context: goals fetch failed: %s: %s", type(exc).__name__, exc)
        goals_text = ""

    # --- time context ---
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_ist = now.astimezone(_IST)

    # --- queue depth ---
    queue_depth: int = 0
    try:
        queue_depth = len(queue) if queue is not None else 0
    except Exception:
        queue_depth = 0

    # --- format the context string ---
    mem_lines = "\n".join(f"- {m}" for m in memories) if memories else "No memories available."
    cal = calendar_text or "No calendar events within the next 2 hours."
    rem = reminders_text or "No pending reminders."
    already = already_sent if already_sent is not None else []
    already_lines = "\n".join(f"- {s}" for s in already) if already else "Nothing surfaced yet today."
    goals_section = goals_text or "No active goals."

    context_str = f"""# Current time
{now_ist.strftime("%A")} {now_ist.strftime("%Y-%m-%d")} {now_ist.hour}:00 IST

# What I remember about Arnav
{mem_lines}

# Calendar — events starting within the next 2 hours ONLY
# (events further away have been removed; do NOT surface anything from memory that is not listed here)
{cal}

# Pending reminders
{rem}

# Active Goals
# (These are goals ARNAV set himself. Each line: his target, HIS stated plan, and
#  actual data where available. Reflect his plan back — never invent prescriptions.)
{goals_section}

# Already surfaced today
{already_lines}

# Memory queue
{queue_depth} memories still pending write (context may be incomplete if >0).

---
Before you answer, silently evaluate EACH domain below in order. Do not skip any. Most cycles should end in silence — surfacing the calendar event just because it is the easiest thing to see is a failure.

1. RELATIONSHIPS — Is there a person (e.g. Siddhi) I should reach out to, follow up with, or who has something happening today? Only surface if there is a concrete, timely reason — not a generic 'you should text someone'.
2. GOALS — Is there a goal or project (e.g. Vytal, the Masters applications) where a small action right now meaningfully moves it forward or prevents slippage? Only if it is genuinely actionable this hour.
3. CALENDAR — Is there an event starting in the NEXT 2 HOURS that needs prep, travel, or a heads-up? Events more than 2 hours away MUST NOT be surfaced. Do not surface routine/recurring events the user clearly already knows about unless prep is actually required.
4. HEALTH — Is there a health-relevant action (sleep, hydration, a missed habit) that is timely right now and not nagging?
5. GOALS_TRACKING — For each active goal listed above, compare Arnav's stated plan to actual data (if available). Surface ONLY if: (a) there is a real measurable deviation from HIS OWN stated plan, (b) a scheduled check-in is genuinely due today, or (c) there is honest, specific encouragement to give (not generic). CRITICAL FRAMING RULE: You are a tracking mirror, not a coach. Reflect his plan back to him — NEVER invent training schedules, dietary advice, medical prescriptions, pacing targets, or recovery protocols. If no plan was stated, do NOT surface this goal unless Arnav explicitly asks. Bias to silence: a goal on track needs no nudge.
6. DEFAULT — If none of the above produced a concrete, timely, actionable reason, return an EMPTY list. This is the expected outcome for most cycles.

Decision rules:
- When uncertain, return []. Silence is the safe default and is never penalized.
- A nudge must be timely (relevant now), actionable (there is a clear thing to do), or miss-preventing (stops a real drop). If it is none of these, drop it.
- Do not re-surface anything already listed under 'Already surfaced today'.
- Do not pad. One strong nudge beats three weak ones; zero beats one weak one.

Priority mapping:
- P1 = urgent / crisis / imminent miss (rare).
- P2 = important and actionable right now.
- P3 = light, optional, nice-to-know.

{_RESPONSE_SCHEMA_HINT}"""

    return context_str


class ProactiveReasoner:
    """Pure reasoning: pre-built context string → Mac LLM → ProactiveItems.

    Never fetches data. Never touches Qdrant, reminders, or calendar.
    Call gather_context() on the VPS first, then pass the result here.
    """

    def __init__(self) -> None:
        self.last_raw_response: Optional[str] = None

    def reason(self, context_str: str) -> list[ProactiveItem]:
        """Run one reasoning cycle. Never raises — returns [] on any failure."""
        try:
            self.last_raw_response = None
            system = _SYSTEM_PROMPT
            raw = self._mac_ollama_complete(system, context_str)
            self.last_raw_response = raw
            if raw is None:
                _logger.info("Mac unreachable — reasoning skipped")
                return []
            items_raw = self._parse_items(raw)
            result = [self._to_proactive_item(it) for it in items_raw]
            return [it for it in result if it is not None]
        except Exception:
            _logger.exception("reason() failed unexpectedly")
            return []

    def _mac_ollama_complete(self, prompt_system: str, prompt_user: str) -> Optional[str]:
        """Call Mac Ollama and return the content string, or None on any failure."""
        try:
            base = os.environ.get("JACK_EXTRACT_URL", "http://100.120.65.115:11434")
            model = os.environ.get("JACK_EXTRACT_MODEL", "qwen2.5:14b")
            url = base.rstrip("/") + "/api/chat"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": prompt_system},
                    {"role": "user", "content": prompt_user},
                ],
                "stream": False,
                "format": "json",
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=_MAC_OLLAMA_TIMEOUT_S) as resp:
                body = resp.read().decode("utf-8")
            obj = json.loads(body)
            return obj["message"]["content"]
        except Exception as exc:
            _logger.warning("Mac Ollama call failed: %s: %s", type(exc).__name__, exc)
            return None

    def _parse_items(self, raw: str) -> list[dict]:
        """Parse the LLM response into a list of raw item dicts."""
        stripped = raw.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r'^```(?:json)?\s*', '', stripped)
            stripped = re.sub(r'\s*```$', '', stripped).strip()
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                for v in parsed.values():
                    if isinstance(v, list):
                        return v
            return []
        except json.JSONDecodeError as e:
            _logger.error(
                "reasoner JSON parse failed: %s | raw[:%d]=%r",
                e,
                _RAW_LOG_CHARS,
                raw[:_RAW_LOG_CHARS],
            )
            return []

    def _to_proactive_item(self, item: dict) -> Optional[ProactiveItem]:
        """Convert a raw dict from the LLM into a ProactiveItem, or None if invalid."""
        priority = item.get("priority")
        if priority not in _VALID_PRIORITIES:
            _logger.debug("dropping item: bad priority %r", priority)
            return None

        message = item.get("message")
        if not isinstance(message, str) or not message.strip():
            _logger.debug("dropping item: bad message")
            return None

        nudge_type = item.get("nudge_type")
        if not isinstance(nudge_type, str) or not nudge_type.strip():
            _logger.debug("dropping item: bad nudge_type")
            return None

        score = {P1: SCORE_P1, P2: SCORE_P2, P3: SCORE_P3}[priority]
        alone = bool(item.get("alone", priority == P1))

        reasoning = item.get("reasoning", "")
        if not isinstance(reasoning, str):
            reasoning = ""

        # When the LLM returns a generic type ("reminder", "notification", etc.),
        # infer the canonical domain type from message + reasoning keywords.
        resolved_type = nudge_type.strip().lower()
        if resolved_type in _GENERIC_NUDGE_TYPES:
            search_text = (message + " " + reasoning).lower()
            for keyword, canonical in _KEYWORD_TO_CANONICAL.items():
                if keyword in search_text:
                    resolved_type = canonical
                    _logger.debug("nudge_type %r inferred as %r from message", nudge_type, resolved_type)
                    break

        return ProactiveItem(
            nudge_type=resolved_type,
            priority=priority,
            score=score,
            message=message.strip(),
            alone=alone,
            meta={"source": "reasoner", "reasoning": reasoning},
        )
