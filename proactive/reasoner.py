"""ProactiveReasoner — LLM-driven reasoning over memory, calendar, reminders, and time.

Replaces hardcoded check_* rules in ProactiveEngine with a single reasoning call to the
Mac Ollama (qwen2.5:14b via JACK_EXTRACT_URL). Falls back to [] on any failure — never
raises, never blocks more than MAC_OLLAMA_TIMEOUT_S seconds.

All dependencies (memory_client, store, calendar_client, queue) are INJECTED via
constructor. This module has zero real network calls at import time and is testable
with mocks only.
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


class ProactiveReasoner:
    """LLM-driven proactive reasoning over memory, calendar, reminders, and time."""

    def __init__(
        self,
        *,
        memory_client: Any,
        store: Any,
        calendar_client: Any,
        queue: Any = None,
        user_id: str = "",
    ) -> None:
        self._memory_client = memory_client
        self._store = store
        self._calendar_client = calendar_client
        self._queue = queue
        self._user_id = user_id
        self.last_raw_response: Optional[str] = None

    def gather_context(
        self,
        user_id: str,
        now: datetime,
        *,
        already_sent: Optional[list[str]] = None,
    ) -> dict:
        """Collect all context needed for the reasoning prompt.

        Returns a dict with keys: memories, calendar, reminders, time_context,
        already_sent, queue_depth.
        """
        # --- memories ---
        memories: list[str] = []
        try:
            seen: set[str] = set()
            for query in _MEMORY_SEARCH_QUERIES:
                try:
                    results = self._memory_client.search(query, user_id=user_id, limit=_MEMORY_SEARCH_LIMIT)
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
            if self._calendar_client is not None:
                calendar_text = self._calendar_client.events_summary_text() or ""
        except Exception as exc:
            _logger.warning("gather_context: calendar fetch failed: %s: %s", type(exc).__name__, exc)
            calendar_text = ""

        # --- reminders ---
        reminders_text: str = ""
        try:
            if self._store is not None:
                pending = self._store.list_pending(user_id)
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
        time_context = {
            "iso": now_ist.isoformat(),
            "day_of_week": now_ist.strftime("%A"),
            "hour": now_ist.hour,
            "date": now_ist.strftime("%Y-%m-%d"),
        }

        # --- queue depth ---
        queue_depth: int = 0
        try:
            queue_depth = len(self._queue) if self._queue is not None else 0
        except Exception:
            queue_depth = 0

        return {
            "memories": memories,
            "calendar": calendar_text,
            "reminders": reminders_text,
            "time_context": time_context,
            "already_sent": already_sent if already_sent is not None else [],
            "queue_depth": queue_depth,
        }

    def build_reasoning_prompt(self, context: dict) -> tuple[str, str]:
        """Build (system_prompt, user_prompt) from gathered context."""
        tc = context["time_context"]
        memories = context.get("memories", [])
        mem_lines = "\n".join(f"- {m}" for m in memories) if memories else "No memories available."
        cal = context.get("calendar") or "No calendar events."
        rem = context.get("reminders") or "No pending reminders."
        already = context.get("already_sent", [])
        already_lines = "\n".join(f"- {s}" for s in already) if already else "Nothing surfaced yet today."
        qdepth = context.get("queue_depth", 0)

        user_prompt = f"""# Current time
{tc['day_of_week']} {tc['date']} {tc['hour']}:00 IST

# What I remember about Arnav
{mem_lines}

# Calendar today
{cal}

# Pending reminders
{rem}

# Already surfaced today
{already_lines}

# Memory queue
{qdepth} memories still pending write (context may be incomplete if >0).

---
Given all of this, what (if anything) should I proactively surface to Arnav right now?

Return a JSON object: {{"nudges": [...]}} where each nudge has: {{"priority": "P1"|"P2"|"P3", "message": str, "nudge_type": str, "alone": bool, "reasoning": str}}. If nothing is worth surfacing, return {{"nudges": []}}. Be conservative — silence is fine."""

        return (_SYSTEM_PROMPT, user_prompt)

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

    def reason(
        self,
        user_id: str,
        now: datetime,
        already_sent_today: list[str],
        store: Any = None,
        calendar_client: Any = None,
        memory_client: Any = None,
        queue: Any = None,
    ) -> list[ProactiveItem]:
        """Run one reasoning cycle. Never raises — returns [] on any failure."""
        try:
            self.last_raw_response = None

            if memory_client is not None:
                self._memory_client = memory_client
            if store is not None:
                self._store = store
            if calendar_client is not None:
                self._calendar_client = calendar_client
            if queue is not None:
                self._queue = queue

            ctx = self.gather_context(user_id, now, already_sent=already_sent_today)
            system, user = self.build_reasoning_prompt(ctx)
            raw = self._mac_ollama_complete(system, user)
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
