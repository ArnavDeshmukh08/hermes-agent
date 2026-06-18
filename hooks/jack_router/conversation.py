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

_PROFILE_TOKEN_CAP = 600
_PERSONALITY_TOKEN_CAP = 1200

_FALLBACK_PERSONALITY = (
    "You are Jack, a sharp, friendly personal Chief of Staff for Arnav Deshmukh, "
    "a technical founder. Be concise, authentic, and helpful."
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
        self._profile = self._load_profile(user_path)
        self._sessions: dict[str, list[dict[str, str]]] = {}

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
    def _system_prompt(self) -> str:
        parts = [self._personality]
        if self._profile:
            parts.append("# About the person you're talking to\n" + self._profile)
        parts.append("# How to behave right now\n" + _NO_TOOLS_GUIDANCE)
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

    def build_prompt(self, user_message: str, user_id: str) -> tuple[str, str]:
        """Assemble (system, user) within the token budget, trimming the oldest
        exchanges first if needed. Guarantees the current message survives."""
        system = self._system_prompt()
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

    # -- response -------------------------------------------------------------
    async def respond(self, user_message: str, user_id: str) -> str:
        """Build a lean prompt, call lib.llm (off-thread — it's blocking), record
        the exchange, and return Jack's reply. Never raises to the caller."""
        system, user_block = self.build_prompt(user_message, user_id)
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
        except Exception:  # noqa: BLE001 - degrade gracefully, never crash the gateway
            return _ERROR_REPLY
        if not reply:
            return _ERROR_REPLY
        self.add_turn(user_id, "user", user_message)
        self.add_turn(user_id, "assistant", reply)
        return reply
