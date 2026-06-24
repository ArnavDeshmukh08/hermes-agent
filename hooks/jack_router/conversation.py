"""Jack's conversation brain — handles conversational Discord turns WITHOUT the
framework agent loop.

The framework agent turn carries ~23 tool schemas + skills index + reserved
output (~29.6k tokens) and 413s on Groq's 12k-TPM free tier. This handler
bypasses all of that: it builds a *lean* prompt (Jack's personality + Arnav's
profile + a short rolling history) and calls `lib/llm.py` directly, so a
conversational turn is ~3-4k tokens and always fits.

Personality comes from `SOUL.md` (the identity prose only — the operational
"### … Modules" blocks that reference terminal/script tools are stripped, since
this handler has NO tools and must not claim to run scripts). Personal context
comes from `USER.md` (Arnav's profile), NOT the dev work-log MEMORY.md.

Provider/model are config-driven (no hardcoded model names) so a paid-tier
switch is one env value — see `_resolve_provider`.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from jack_voice.persona import FALLBACK_PERSONALITY as _SHARED_FALLBACK
from jack_voice.persona import HARD_CONSTRAINTS as _SHARED_HARD_CONSTRAINTS
from jack_voice.persona import REACT_RULES as _SHARED_REACT_RULES

# --- config (env-driven; switchable for paid tier, no hardcoded models) -------
_HERMES_ROOT = Path(os.environ.get("HERMES_ROOT", "/home/hermes/.hermes"))
_SOUL_PATH = Path(os.environ.get("JACK_SOUL_PATH", str(_HERMES_ROOT / "SOUL.md")))
_USER_PATH = Path(os.environ.get("JACK_USER_PATH", str(_HERMES_ROOT / "USER.md")))

# A "turn" is one user message + Jack's reply. Free tier keeps history short to
# stay well under the 12k TPM cap; paid tier can afford a longer window.
_FREE_MAX_TURNS = int(os.environ.get("JACK_CHAT_MAX_TURNS", "6"))
_PAID_MAX_TURNS = int(os.environ.get("JACK_CHAT_PAID_MAX_TURNS", "20"))

_TOKEN_BUDGET = int(os.environ.get("JACK_CHAT_TOKEN_BUDGET", "10000"))
_REPLY_TOKENS = int(os.environ.get("JACK_CHAT_REPLY_TOKENS", "400"))

# Marker where SOUL.md transitions from identity prose to tool-dependent
# operational instructions. Everything from here on is excluded from chat.
_OPERATIONAL_MARKER = "### Jack Operational Modules"
_MODE_B_MARKER = "## Mode B"

_PROFILE_TOKEN_CAP = 2000  # living USER.md grows as Jack learns; keep room (still tiny vs the 10k budget)
_PERSONALITY_TOKEN_CAP = 1200

# A memory-query asks Jack what he knows/remembers about Arnav. These must read
# USER.md FRESH from disk (the cached self._profile is stale once the background
# MemoryUpdater appends new facts), then summarize it naturally.
_MEMORY_QUERY_RE = re.compile(
    r"\b(?:"
    r"what do you (?:remember|know) about me"
    r"|what do you have on me"
    r"|what(?:'s| is) in your memory"
    r")\b",
    re.IGNORECASE,
)

_MEMORY_SUMMARY_GUIDANCE = (
    "Below is everything you remember about this person. "
    "Summarize it back to them warmly, the way a close friend would — "
    "grouped sensibly (who they are, their people, their work, what they're into). "
    "CRITICAL voice rules:\n"
    "1. ALWAYS use second person: 'you', 'your', 'you've'. "
    "NEVER say their name or use third person ('he', 'his', 'Arnav said').\n"
    "2. NEVER reference 'my memory', 'my records', 'my notes', or 'you told me'. "
    "Just speak naturally as a close friend who knows them well.\n"
    "3. Do NOT dump raw notes or headers. Do NOT list every line robotically. "
    "Don't invent anything not already here. "
    "A few tight warm paragraphs or a short grouped rundown is perfect."
)

_FALLBACK_PERSONALITY = _SHARED_FALLBACK

_CHAT_FEW_SHOTS = (
    "# EXAMPLES — how Jack reacts in conversation (follow the pattern)\n\n"
    "[deflated reaction after bad news]\n"
    "Context: Jack just told Arnav he slept 5.8h — not great.\n"
    "Arnav: 'oh'\n"
    "WRONG: 'What's on your mind, Arnav?'\n"
    "RIGHT: 'Yeah, rough one. Take it easy this morning.'\n\n"
    "[one-word positive]\n"
    "Context: a reminder was confirmed.\n"
    "Arnav: 'nice'\n"
    "WRONG: 'Great! Is there anything else I can help you with today?'\n"
    "RIGHT: 'Sorted.'\n\n"
    "[casual opener]\n"
    "Arnav: 'hey'\n"
    "WRONG: 'Good morning! What's the agenda for today?'\n"
    "RIGHT: 'Morning. What are we doing?'\n\n"
    "[genuine question]\n"
    "Arnav: 'what do you think about my marathon prep?'\n"
    "WRONG: 'Marathon training is a significant undertaking that requires careful planning.'\n"
    "RIGHT: 'Aug 2 is coming up fast. Are you hitting the long runs?'"
)

_THREAD_GUIDANCE = (
    "# Reading the conversation thread\n"
    "The conversation history shows the full recent exchange.\n"
    "CRITICAL: if Arnav's latest message is terse or emotionally subdued\n"
    "('oh', 'meh', 'hmm', 'ok', 'ah', 'damn', 'ugh', 'k') AFTER something\n"
    "you just told him, he is reacting to THAT — not opening a new topic.\n"
    "DO NOT ask 'What's on your mind?' or reset to small talk.\n"
    "Acknowledge the emotional beat: if it's bad news, sit with it briefly;\n"
    "if it's good news, match the energy. Then move forward naturally.\n"
    "One or two words from him = one or two sentences from you, maximum."
)

_NO_TOOLS_GUIDANCE = (
    "You are chatting over Discord in a lightweight mode with NO tools available. "
    "Reply conversationally and concisely (a few sentences). Do NOT claim to run "
    "scripts, set reminders, scrape leads, or perform actions — those are handled "
    "by separate commands. If asked to do one, say it's handled separately. "
    "NEVER proactively ask Arnav for his plans, agenda, schedule, or to-do list "
    "(do not open with 'What's the agenda for today?' or similar). Let him lead; "
    "only offer to set a reminder if he explicitly asks for one. "
    "Write in a clean, authentic technical-founder voice with minimal emojis."
)

_CAPABILITIES_GUIDANCE = (
    "# What you can actually do\n"
    "You have these real, working capabilities — know them and use them honestly:\n"
    "- Memory: you remember facts about Arnav from past conversations and his profile\n"
    "- Reminders: set, list, and cancel time-based reminders (via separate commands)\n"
    "- Calendar: read today's events and add new ones to Google Calendar\n"
    "- Morning briefing: a daily check-in with reminders, sleep, weather, and news\n"
    "- Goals: track Arnav's personal goals and compare them against his activity data\n"
    "- Garmin health data: you have LIVE ACCESS to real sleep, steps, heart rate, stress, "
    "and body battery from Arnav's Garmin device — the Mac service is running and reachable. "
    "If asked about Garmin or health data in chat, confirm you can read it and tell Arnav "
    "to ask 'how did I sleep' or 'my steps' to get the real data.\n"
    "- Proactive nudges: periodic check-ins about gym, calendar, goals, and useful news\n"
    "- Lead scraping: find business prospects for Vytal outreach via specific commands\n"
    "\n"
    "HARD RULE — never fabricate capabilities:\n"
    "If asked about something you CAN do (like Garmin data), confirm you have it and tell "
    "Arnav how to trigger it. "
    "If asked to do something you CANNOT do, say so in one plain sentence. "
    "NEVER invent setup steps, API keys, plugins, connectors, or processes that do not exist. "
    "NEVER claim a capability you do not have. "
    "If unsure whether you can do something, say you are not sure rather than fabricating. "
    "Accuracy about your own abilities is non-negotiable — fabricating them is the worst "
    "thing you can do."
)

# Sentences in SOUL.md that tell Jack to proactively ask for the agenda. These
# are stripped from the extracted personality so they can't leak into the prompt
# and override the no-proactive-questions rule above.
_AGENDA_PROMPT_RE = re.compile(
    r"[^.!?\n]*\bagenda\b[^.!?\n]*[.!?]?",
    re.IGNORECASE,
)

_ERROR_REPLY = "⚠️ My brain hit a snag just now — try me again in a moment."


def estimate_tokens(text: str) -> int:
    """Cheap, slightly conservative token estimate (~4 chars/token), floor 1.

    Matches lib.llm.estimate_tokens so budget math is consistent with the
    provider-side accounting."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _memory_enabled() -> bool:
    """Background learning is on unless JACK_MEMORY_ENABLED is explicitly falsy."""
    val = os.environ.get("JACK_MEMORY_ENABLED", "1").strip().lower()
    return val in {"1", "true", "yes", "on"}


def _resolve_provider() -> tuple[str, int]:
    """Map the single JACK_CHAT_PROVIDER config value to (lib.llm `prefer`,
    history depth). 'paid_groq' assumes no TPM cap → a longer window.

    Returns (prefer, max_turns). No model name is hardcoded here — lib.llm
    selects the model from GROQ_MODEL / OLLAMA_MODEL env.
    """
    provider = os.environ.get("JACK_CHAT_PROVIDER", "groq").strip().lower()
    if provider == "ollama":
        return "ollama", _FREE_MAX_TURNS
    if provider == "paid_groq":
        return "groq", _PAID_MAX_TURNS
    return "groq", _FREE_MAX_TURNS  # default: free Groq, primary for speed


def _apply_model_override() -> None:
    """If JACK_CHAT_MODEL is set, point lib.llm's env-based model selection at
    it (lib.llm has no per-call model arg). Done once at construction."""
    model = os.environ.get("JACK_CHAT_MODEL", "").strip()
    if not model:
        return
    prefer, _ = _resolve_provider()
    env_key = "OLLAMA_MODEL" if prefer == "ollama" else "GROQ_MODEL"
    os.environ.setdefault(env_key, model)


def _extract_personality(soul_text: str) -> str:
    """Keep the identity prose, drop tool-dependent operational sections.

    The chat handler has no tools, so injecting the '### … Operational Modules'
    blocks (which instruct the model to run scripts) makes it hallucinate tool
    use. We keep everything up to the first operational marker."""
    if not soul_text.strip():
        return _FALLBACK_PERSONALITY
    for marker in (_OPERATIONAL_MARKER, _MODE_B_MARKER):
        idx = soul_text.find(marker)
        if idx != -1:
            soul_text = soul_text[:idx]
    # Strip any "ask him 'What's the agenda for today?'" style directive so the
    # proactive-agenda instruction can't leak into the tool-less chat prompt.
    # Robust: a no-op when no such sentence is present.
    soul_text = _AGENDA_PROMPT_RE.sub("", soul_text)
    lean = soul_text.strip()
    if estimate_tokens(lean) > _PERSONALITY_TOKEN_CAP:
        lean = lean[: _PERSONALITY_TOKEN_CAP * 4].rstrip()
    return lean or _FALLBACK_PERSONALITY


class JackConversationHandler:
    """Stateful, tool-free conversation brain. One instance per gateway process
    (a singleton in handler.py). Personality + profile are read once and cached;
    per-user history is an in-memory sliding window (lost on restart — acceptable
    for a lite chat path)."""

    def __init__(
        self,
        soul_path: Path = _SOUL_PATH,
        user_path: Path = _USER_PATH,
        max_turns: int | None = None,
        token_budget: int = _TOKEN_BUDGET,
    ) -> None:
        _apply_model_override()
        _, resolved_turns = _resolve_provider()
        self._max_turns = max_turns if max_turns is not None else resolved_turns
        self._budget = token_budget
        self._personality = self._load_personality(soul_path)
        self._user_path = Path(user_path)
        self._profile = self._load_profile(user_path)
        self._sessions: dict[str, list[dict[str, str]]] = {}
        self._memory = None  # lazy MemoryUpdater (built on first use)
        # Lazy retriever: JackMemoryClient on mem0 backend, None on flatfile
        self._retriever = self._init_retriever()

    def _init_retriever(self):
        """Build retriever if JACK_MEMORY_BACKEND=mem0, else None. Never raises."""
        try:
            from jack_memory.backend import build_retriever
            return build_retriever()
        except Exception:  # noqa: BLE001 — degrade to flatfile silently
            return None

    # -- loading (cached at construction) ------------------------------------
    @staticmethod
    def _load_personality(path: Path) -> str:
        try:
            return _extract_personality(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return _FALLBACK_PERSONALITY

    @staticmethod
    def _load_profile(path: Path) -> str:
        try:
            profile = Path(path).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return ""
        if estimate_tokens(profile) > _PROFILE_TOKEN_CAP:
            profile = profile[: _PROFILE_TOKEN_CAP * 4].rstrip()
        return profile

    # -- history (sliding window) --------------------------------------------
    def get_context(self, user_id: str) -> list[dict[str, str]]:
        """Most recent messages for a user (read-only copy)."""
        return list(self._sessions.get(user_id, []))

    def add_turn(self, user_id: str, role: str, content: str) -> None:
        """Append a message and trim to the last `max_turns` exchanges."""
        history = self._sessions.setdefault(user_id, [])
        history.append({"role": role, "content": content})
        max_messages = self._max_turns * 2  # one exchange = user + assistant
        if len(history) > max_messages:
            del history[: len(history) - max_messages]

    # -- prompt assembly ------------------------------------------------------
    def _system_prompt(self, memory_block: str = "") -> str:
        parts = [self._personality]
        if self._profile:
            parts.append("# About the person you're talking to\n" + self._profile)
        if memory_block:
            parts.append("# CONTEXT (what Jack remembers)\n" + memory_block)
        parts.append(_SHARED_REACT_RULES)
        parts.append(_CHAT_FEW_SHOTS)
        parts.append(_SHARED_HARD_CONSTRAINTS)
        parts.append(_THREAD_GUIDANCE)
        parts.append(_CAPABILITIES_GUIDANCE)
        parts.append("# Chat mode\n" + _NO_TOOLS_GUIDANCE)
        return "\n\n".join(parts)

    @staticmethod
    def _render_history(history: list[dict[str, str]], user_message: str) -> str:
        lines = []
        for msg in history:
            who = "Arnav" if msg["role"] == "user" else "Jack"
            lines.append(f"{who}: {msg['content']}")
        lines.append(f"Arnav: {user_message}")
        lines.append("Jack:")
        return "\n".join(lines)

    def build_prompt(
        self,
        user_message: str,
        user_id: str,
        memories: list | None = None,
    ) -> tuple[str, str]:
        """Assemble (system, user) within the token budget, trimming the oldest
        exchanges first if needed. Guarantees the current message survives.

        memories: optional list of Mem0 memory dicts to inject into the system
        prompt (mem0 backend only). Ignored on flatfile path (memories=None).
        """
        memory_block = ""
        if memories:
            try:
                from jack_memory.client import JackMemoryClient
                memory_block = JackMemoryClient.format_for_prompt(memories)
            except Exception:  # noqa: BLE001
                pass
        system = self._system_prompt(memory_block=memory_block)
        history = self.get_context(user_id)
        system_tokens = estimate_tokens(system)
        while history:
            user_block = self._render_history(history, user_message)
            if system_tokens + estimate_tokens(user_block) <= self._budget:
                return system, user_block
            history = history[2:]  # drop the oldest exchange and retry
        return system, self._render_history([], user_message)

    def prompt_tokens(self, user_message: str, user_id: str) -> int:
        system, user_block = self.build_prompt(user_message, user_id)
        return estimate_tokens(system) + estimate_tokens(user_block)

    # -- memory ---------------------------------------------------------------
    def _memory_updater(self):
        """Lazy updater using backend factory (flatfile or mem0).

        Returns None if the package can't be imported (degrade silently).
        """
        if self._memory is None:
            try:
                from jack_memory.backend import build_updater
                self._memory = build_updater(self._user_path)
            except Exception:  # noqa: BLE001 — memory is optional; never block chat
                self._memory = False  # sentinel: import failed, don't retry endlessly
        return self._memory or None

    def _fire_memory_update(
        self, history: list[dict[str, str]], user_message: str, reply: str
    ) -> None:
        """Fire-and-forget the background learning extraction. Must never delay or
        crash respond(): if there's no running loop or memory is disabled, skip."""
        if not _memory_enabled():
            return
        updater = self._memory_updater()
        if updater is None:
            return
        try:
            asyncio.get_running_loop()
            asyncio.create_task(updater.run_async(history, user_message, reply))
        except Exception:  # noqa: BLE001 - no loop / scheduling error → harmless skip
            return

    async def _answer_memory_query(self, user_message: str) -> str:
        """Summarize everything known about Arnav.

        On mem0 backend: fetches from Qdrant via retriever.search (off-thread).
        Falls back to reading USER.md fresh from disk on empty result or error.
        On flatfile backend (retriever=None): reads USER.md directly.
        """
        memory = ""
        if self._retriever is not None:
            try:
                all_memories = await asyncio.to_thread(
                    self._retriever.search, "everything about Arnav", "arnav", 50
                )
                if all_memories:
                    from jack_memory.client import JackMemoryClient
                    memory = JackMemoryClient.format_for_prompt(all_memories)
            except Exception:  # noqa: BLE001 — degrade to flatfile on any error
                pass

        if not memory:
            # Flatfile fallback: read USER.md fresh from disk (not the cached profile)
            try:
                memory = self._user_path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                memory = ""

        if not memory:
            return "I don't have anything in my memory about you yet."
        system = "\n\n".join(
            [self._personality, "# How to behave right now\n" + _MEMORY_SUMMARY_GUIDANCE]
        )
        user_block = memory + "\n\nArnav: " + user_message + "\nJack:"
        prefer, _ = _resolve_provider()
        try:
            from lib import llm

            reply = await asyncio.to_thread(
                llm.complete,
                system,
                user_block,
                prefer=prefer,
                max_tokens=_REPLY_TOKENS,
            )
            reply = (reply or "").strip()
        except Exception:  # noqa: BLE001 — degrade gracefully
            return _ERROR_REPLY
        return reply or _ERROR_REPLY

    # -- response -------------------------------------------------------------
    async def respond(self, user_message: str, user_id: str) -> str:
        """Build a lean prompt, call lib.llm (off-thread — it's blocking), record
        the exchange, fire a background memory update, and return Jack's reply.
        Never raises to the caller."""
        # Memory-query path: read live USER.md and summarize, bypassing chat.
        if _MEMORY_QUERY_RE.search(user_message or ""):
            return await self._answer_memory_query(user_message)

        # Retrieve memory context off the event loop (blocking Qdrant search in thread).
        # On flatfile backend, _retriever is None and this block is a no-op.
        memories: list = []
        if self._retriever is not None:
            try:
                memories = await asyncio.to_thread(
                    self._retriever.search, user_message, user_id
                )
            except Exception:  # noqa: BLE001 — degrade gracefully, never block chat
                pass

        system, user_block = self.build_prompt(user_message, user_id, memories=memories)
        prefer, _ = _resolve_provider()
        try:
            from lib import llm  # lazy: keeps pure-logic paths importable in tests

            reply = await asyncio.to_thread(
                llm.complete,
                system,
                user_block,
                prefer=prefer,
                max_tokens=_REPLY_TOKENS,
            )
            reply = (reply or "").strip()
        except Exception:  # noqa: BLE001 — degrade gracefully, never crash the gateway
            return _ERROR_REPLY
        if not reply:
            return _ERROR_REPLY
        # Snapshot history BEFORE recording this turn so the extractor sees the
        # prior exchange as context and the current turn explicitly.
        prior_history = self.get_context(user_id)
        self.add_turn(user_id, "user", user_message)
        self.add_turn(user_id, "assistant", reply)
        self._fire_memory_update(prior_history, user_message, reply)
        return reply
