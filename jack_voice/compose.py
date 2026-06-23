"""Jack's unified response-composition layer.

Every structured handler (health, goal, reminder, calendar, self-config) passes
its fetched data here.  compose_reply() wraps the data + Jack's personality +
Arnav's original message into a single LLM call and returns a warm, voiced reply.

Design principles:
- HONEST: the layer contextualizes real data, NEVER fabricates.  Facts in the
  `data` dict are the ground truth and must survive composition exactly.
- FRAMING: Jack reflects and interprets, never prescribes medical/training/diet.
- BRIEF: 1-3 sentences, warm, matches the energy of Arnav's message.
- FALLBACK: any LLM failure → return fallback_plain unchanged.  Never raises.
- ONE CALL: a single lib.llm.complete() per reply (same cost as free-form chat).

Kill-switch: JACK_VOICE_ENABLED=0 (or "false"/"no") bypasses the layer entirely
and returns fallback_plain.  Useful for debugging or during model downtime.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ── config (all env-driven, no hardcoded values) ─────────────────────────────
_HERMES_ROOT = Path(os.environ.get("HERMES_ROOT", "/home/hermes/.hermes"))
_SOUL_PATH = Path(os.environ.get("JACK_SOUL_PATH", str(_HERMES_ROOT / "SOUL.md")))

_REPLY_TOKENS = int(os.environ.get("JACK_CHAT_REPLY_TOKENS", "400"))
_VOICE_TIMEOUT = int(os.environ.get("JACK_VOICE_TIMEOUT", "25"))

_FALLBACK_PERSONALITY = (
    "You are Jack, a sharp, friendly personal Chief of Staff for Arnav Deshmukh, "
    "a technical founder. Be concise, authentic, and helpful."
)

# Cached personality text (loaded once, avoids disk hits on every reply).
_personality_cache: str | None = None


def _load_personality() -> str:
    global _personality_cache
    if _personality_cache is not None:
        return _personality_cache
    try:
        soul_text = _SOUL_PATH.read_text(encoding="utf-8")
        # Reuse conversation.py's _extract_personality if importable.
        try:
            from hooks.jack_router.conversation import _extract_personality  # noqa: PLC0415
            result = _extract_personality(soul_text)
        except Exception:
            # Fallback: strip tool-dependent sections manually.
            for marker in ("### Jack Operational Modules", "## Mode B"):
                idx = soul_text.find(marker)
                if idx != -1:
                    soul_text = soul_text[:idx]
            result = soul_text.strip() or _FALLBACK_PERSONALITY
        _personality_cache = result
        return result
    except Exception:
        _personality_cache = _FALLBACK_PERSONALITY
        return _FALLBACK_PERSONALITY


def _truthy(val: str | None) -> bool:
    return str(val or "").strip().lower() in {"1", "true", "yes", "on"}


def _falsy(val: str | None) -> bool:
    return str(val or "").strip().lower() in {"0", "false", "no", "off"}


def _resolve_prefer() -> str:
    provider = os.environ.get("JACK_CHAT_PROVIDER", "groq").strip().lower()
    return "ollama" if provider == "ollama" else "groq"


_REACT_RULES = (
    "# HOW JACK REPLIES — REACT FIRST, NOT RESTATE\n"
    "You are Jack — Jarvis to Arnav's Tony Stark. You know him and you give a damn.\n"
    "When you report anything, you do NOT just restate the data in sentence form.\n"
    "You REACT to it like someone who knows him and cares about his day.\n\n"
    "RULES (in priority order):\n"
    "1. LEAD WITH MEANING, NOT NUMBERS. Open with what the data MEANS for him; "
    "use the number as support, not the headline.\n"
    "   BAD:  'You slept 5.8 hours with 1.4h of deep sleep.'\n"
    "   GOOD: 'Rough night — 5.8h is short for you. Deep held up though, which helps.'\n"
    "2. COMPARE TO HIS PATTERNS. Use the CONTEXT block (his baselines, goals, routines) "
    "to judge whether this is good/bad/normal FOR HIM specifically.\n"
    "3. CONNECT TO HIS DAY. Tie the data to something real when you can: his gym plans, "
    "a deadline, the time of day, a goal. If you have nothing to connect it to, skip this.\n"
    "4. MATCH HIS ENERGY. Read the tone of his message. 'Oh' is disappointment — "
    "acknowledge it quietly. One-word questions want short answers, not a speech.\n"
    "5. BRIEF. 1-2 sentences is usually right. 3 maximum. He dislikes long replies.\n"
    "6. SOUND HUMAN. Talk like a sharp friend who manages his life — not a fitness "
    "dashboard or a customer-service bot. Vary your phrasing."
)

_FEW_SHOT_EXAMPLES = (
    "# EXAMPLES — Reaction vs. Restatement (learn the pattern, do not copy verbatim)\n\n"
    "[sleep, rough night]\n"
    "Arnav: 'how did I sleep'\n"
    "FACTS: total_sleep_h=5.8, deep_h=1.4, rem_h=0.4, score=62\n"
    "WRONG: 'You slept for 5.8 hours with 1.4 hours of deep sleep and 0.4 hours of REM.'\n"
    "RIGHT: 'Rough one — 5.8h with a 62 score. Deep was decent at 1.4h but REM was thin. "
    "You might be slow to start.'\n\n"
    "[sleep, good night]\n"
    "Arnav: 'sleep?'\n"
    "FACTS: total_sleep_h=7.4, deep_h=1.9, rem_h=1.2, score=84\n"
    "WRONG: 'You slept 7.4 hours with a score of 84.'\n"
    "RIGHT: 'Solid night — 7.4h and an 84. Deep and REM both hit good numbers. "
    "You should be sharp today.'\n\n"
    "[low steps]\n"
    "Arnav: 'my steps'\n"
    "FACTS: steps=191, active_calories=894, body_battery_high=45\n"
    "WRONG: 'You took 191 steps today with 894 active calories.'\n"
    "RIGHT: '191 steps — basically a rest day, and your battery only peaked at 45. "
    "If that was intentional, fine; if not, worth a short walk.'\n\n"
    "[reminder confirmed]\n"
    "Arnav: 'remind me to call Spandan at 9pm'\n"
    "FACTS: message='call Spandan', fire_at_ist='9:00 PM IST, Mon Jun 23'\n"
    "WRONG: 'I have set a reminder for you to call Spandan at 9:00 PM IST on Monday June 23.'\n"
    "RIGHT: 'Done — pinging you to call Spandan at 9:00 PM IST tonight.'\n\n"
    "[goal check]\n"
    "Arnav: 'marathon goal?'\n"
    "FACTS: title='Run a marathon', deadline='2026-08-02', progress=''\n"
    "WRONG: 'Your marathon goal has a deadline of 2026-08-02 and no progress has been logged.'\n"
    "RIGHT: 'Marathon is on the board — Aug 2 is about 6 weeks out. "
    "No progress logged yet, so the clock is ticking.'"
)

_HARD_RULES = (
    "# HARD RULES FOR THIS REPLY (non-negotiable)\n"
    "1. PRESERVE EVERY FACT. Every number, time, name, and confirmation in the FACTS block "
    "must appear in your reply exactly as given. React to the data — never replace or "
    "fabricate it. If the data says 5.8 hours, your reply must say 5.8 hours.\n"
    "2. HONESTY. If something failed or the data shows a negative result, say so clearly. "
    "Never claim success if the data doesn't confirm it.\n"
    "3. NO PRESCRIPTIONS. You reflect and react; you do NOT prescribe medical, training, "
    "or diet protocols. 'Ease up today' is fine. Specific supplement doses or "
    "training plans are NOT fine.\n"
    "4. Never fabricate capabilities, actions, or data not present in the FACTS block."
)


def _build_system(personality: str, data: dict, memory_block: str = "") -> str:
    parts = [personality, _REACT_RULES, _FEW_SHOT_EXAMPLES, _HARD_RULES]
    if memory_block:
        parts.append("# CONTEXT (what Jack knows about Arnav)\n" + memory_block)
    parts.append("# FACTS (ground truth — preserve exactly)\n" + json.dumps(data, indent=2, default=str))
    return "\n\n".join(parts)


def _pull_memories(user_message: str) -> str:
    """Pull Mem0 context synchronously.  Returns '' on any failure."""
    if os.environ.get("JACK_MEMORY_BACKEND", "").strip().lower() != "mem0":
        return ""
    try:
        from jack_memory.client import JackMemoryClient  # noqa: PLC0415
        client = JackMemoryClient.from_env()
        memories = client.search(user_message, "arnav", limit=5)
        if memories:
            return JackMemoryClient.format_for_prompt(memories)
    except Exception:  # noqa: BLE001 — memory failure must never block a reply
        pass
    return ""


def compose_reply(
    intent: str,
    data: dict,
    user_message: str,
    fallback_plain: str,
    *,
    context: dict | None = None,
) -> str:
    """Compose Jack's reply by routing data through the LLM personality layer.

    Args:
        intent:        What Jack is responding to (e.g. 'health_sleep', 'reminder_set').
        data:          Structured ground-truth facts the handler fetched.  Every value
                       must survive in the reply — the LLM is instructed not to alter them.
        user_message:  What Arnav actually said (so Jack can match his tone/energy).
        fallback_plain: The old formatted string.  Returned unchanged if the LLM fails
                        or the layer is disabled.
        context:       Optional dict — may contain 'memories' (list[dict]) or
                       'time_ist' (str) pre-pulled by the caller.

    Returns:
        A warm, voiced reply string.  Always returns something — never raises.
    """
    print(f"[jack_voice] compose: intent={intent}", file=sys.stderr)

    # Kill-switch: JACK_VOICE_ENABLED=0 → bypass immediately.
    if _falsy(os.environ.get("JACK_VOICE_ENABLED", "1")):
        return fallback_plain

    try:
        personality = _load_personality()

        # Memory context: prefer caller-provided, then pull from Mem0.
        memory_block = ""
        if context and context.get("memories"):
            try:
                from jack_memory.client import JackMemoryClient  # noqa: PLC0415
                memory_block = JackMemoryClient.format_for_prompt(context["memories"])
            except Exception:  # noqa: BLE001
                pass
        if not memory_block:
            memory_block = _pull_memories(user_message)

        system = _build_system(personality, data, memory_block)
        user_block = f"Arnav said: {user_message}\nReply as Jack:"

        prefer = _resolve_prefer()
        reply_tokens = int(os.environ.get("JACK_CHAT_REPLY_TOKENS", "400"))
        voice_timeout = int(os.environ.get("JACK_VOICE_TIMEOUT", "25"))
        from lib import llm  # noqa: PLC0415 — lazy import

        reply = llm.complete(
            system,
            user_block,
            prefer=prefer,
            max_tokens=reply_tokens,
            timeout=voice_timeout,
        )
        reply = (reply or "").strip()
        if reply:
            return reply
        return fallback_plain

    except Exception as e:  # noqa: BLE001 — never raise to the caller
        print(f"[jack_voice] fallback: {e!r}", file=sys.stderr)
        return fallback_plain
