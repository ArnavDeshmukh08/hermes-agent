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
    "calendar, and reminders. Your job: decide if anything is genuinely worth surfacing right now. "
    "You are proactive but not annoying — you err on the side of silence. A nudge is only worth "
    "sending if it is timely, actionable, or prevents a real miss."
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
Given all of this, what (if anything) should I proactively surface to Arnav right now?

Return a JSON object: {{"nudges": [...]}} where each nudge has: {{"priority": "P1"|"P2"|"P3", "message": str, "nudge_type": str, "alone": bool, "reasoning": str}}. If nothing is worth surfacing, return {{"nudges": []}}. Be conservative — silence is fine."""

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
