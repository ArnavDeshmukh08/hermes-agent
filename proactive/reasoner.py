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
            except Exception:
                pass
            if len(memories) >= _MAX_MEMORIES:
                break
    except Exception as exc:
        _logger.warning("gather_context: memory collection failed: %s: %s", type(exc).__name__, exc)
        memories = []

    # --- calendar ---
    calendar_text: str = ""
    try:
        if calendar_client is not None:
            calendar_text = calendar_client.events_summary_text() or ""
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
    cal = calendar_text or "No calendar events."
    rem = reminders_text or "No pending reminders."
    already = already_sent if already_sent is not None else []
    already_lines = "\n".join(f"- {s}" for s in already) if already else "Nothing surfaced yet today."

    context_str = f"""# Current time
{now_ist.strftime("%A")} {now_ist.strftime("%Y-%m-%d")} {now_ist.hour}:00 IST

# What I remember about Arnav
{mem_lines}

# Calendar today
{cal}

# Pending reminders
{rem}

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
5. DEFAULT — If none of the above produced a concrete, timely, actionable reason, return an EMPTY list. This is the expected outcome for most cycles.

Decision rules:
- When uncertain, return []. Silence is the safe default and is never penalized.
- A nudge must be timely (relevant now), actionable (there is a clear thing to do), or miss-preventing (stops a real drop). If it is none of these, drop it.
- Do not re-surface anything already listed under 'Already surfaced today'.
- Do not pad. One strong nudge beats three weak ones; zero beats one weak one.

Priority mapping:
- P1 = urgent / crisis / imminent miss (rare).
- P2 = important and actionable right now.
- P3 = light, optional, nice-to-know.

Return ONLY a JSON object: {{"nudges": [...]}} where each nudge is {{"priority": "P1"|"P2"|"P3", "message": str, "nudge_type": str, "alone": bool, "reasoning": str}}. The "reasoning" must name which domain (1-5) triggered the nudge and why it is timely. If nothing qualifies, return {{"nudges": []}}."""

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

        return ProactiveItem(
            nudge_type=nudge_type.strip(),
            priority=priority,
            score=score,
            message=message.strip(),
            alone=alone,
            meta={"source": "reasoner", "reasoning": reasoning},
        )
