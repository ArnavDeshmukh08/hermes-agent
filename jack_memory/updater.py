"""MemoryUpdater — extract durable new facts from a Jack conversation and append
them to the right `USER.md` section.

Design contract (see jack_memory/schema.md):
- USER.md is `[SECTION]`-delimited plain text Arnav can hand-edit.
- This module is APPEND-ONLY: it never rewrites or deletes an existing line.
  New facts land as `- <fact>` sub-bullets at the end of their section, except
  `[THINGS JACK HAS LEARNED]` which gets a dated `YYYY-MM-DD: <fact>` line.
- Extraction calls the LLM (Groq, free tier) with a short, last-turn prompt and a
  strict JSON-list shape. Anything vague is dropped; malformed JSON yields [].
- All of this is best-effort: `run_async` swallows every exception so a flaky
  Groq call or a locked file can never break the chat path that fires it.

`lib.llm` is imported lazily (inside methods) so the pure-logic + test paths
don't require the network stack at import time, and tests inject `complete_fn`.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path

# The 8 canonical sections. A learning's section MUST be one of these; anything
# else is routed to THINGS JACK HAS LEARNED so a stray label never drops a fact.
_THINGS_LEARNED = "THINGS JACK HAS LEARNED"
_VALID_SECTIONS = (
    "IDENTITY",
    "RELATIONSHIPS",
    "WORK & PROJECTS",
    "CURRENT PRIORITIES",
    "DAILY ROUTINE — ICHALKARANJI (HOLIDAYS)",
    "PREFERENCES",
    "GOALS",
    _THINGS_LEARNED,
)

# A section header is a line that is EXACTLY an uppercase-ish label in brackets.
_SECTION_RE = re.compile(r"^\[.+\]$")
_LAST_UPDATED_RE = re.compile(r"^Last updated:.*$", re.IGNORECASE)

_DEFAULT_USER_PATH = Path.home() / ".hermes" / "USER.md"
_DEFAULT_LOG_PATH = Path.home() / ".hermes" / "logs" / "memory.log"

_EXTRACTOR_SYSTEM = (
    "You are Jack's memory extractor for Arnav Deshmukh. Read the latest exchange "
    "and extract ONLY clear, specific, NEW, durable facts about Arnav — things "
    "worth remembering long-term (a preference, a relationship fact, a project "
    "change, a goal, an identity/routine change).\n\n"
    "Return a JSON list of objects {\"section\": <SECTION>, \"fact\": <one concise "
    "sentence>}. SECTION must be EXACTLY one of:\n"
    "IDENTITY, RELATIONSHIPS, WORK & PROJECTS, CURRENT PRIORITIES, "
    "DAILY ROUTINE — ICHALKARANJI (HOLIDAYS), PREFERENCES, GOALS, "
    "THINGS JACK HAS LEARNED.\n\n"
    "Rules: ignore vague impressions ('seems busy', 'likes working', moods, "
    "one-off events, speculation). Do not invent. If there is nothing clearly new "
    "and durable, return []. Output JSON only — no prose, no code fences."
)


def _today_str(today: _dt.date | None = None) -> str:
    return (today or _dt.date.today()).isoformat()


class MemoryUpdater:
    """Extracts learnings from a conversation turn and appends them to USER.md."""

    def __init__(
        self,
        user_path: str | os.PathLike | None = None,
        complete_fn=None,
        log_path: str | os.PathLike | None = None,
    ) -> None:
        if user_path is None:
            env_path = os.environ.get("JACK_USER_PATH")
            user_path = env_path if env_path else _DEFAULT_USER_PATH
        self._user_path = Path(user_path)
        self._complete_fn = complete_fn  # None → resolved lazily to lib.llm.complete
        self._log_path = Path(log_path) if log_path is not None else _DEFAULT_LOG_PATH

    # -- LLM extraction -------------------------------------------------------
    def _resolve_complete_fn(self):
        if self._complete_fn is not None:
            return self._complete_fn
        from lib import llm  # lazy: tests don't need lib/network at import time

        return llm.complete

    def extract_learnings(
        self, history: list, user_message: str, jack_response: str
    ) -> list[dict]:
        """Call the LLM on the LAST turn (+ at most the previous exchange) and
        return validated [{"section","fact"}]. Never raises; returns [] on any
        parse/validation failure."""
        # Optional model override, applied once via setdefault so it doesn't clobber
        # an explicit GROQ_MODEL the operator already set.
        model = os.environ.get("JACK_MEMORY_MODEL", "").strip()
        if model:
            os.environ.setdefault("GROQ_MODEL", model)

        user_prompt = self._build_extract_prompt(history, user_message, jack_response)
        complete_fn = self._resolve_complete_fn()
        try:
            raw = complete_fn(
                _EXTRACTOR_SYSTEM,
                user_prompt,
                prefer="groq",
                json_only=True,
                max_tokens=300,
            )
        except Exception:  # noqa: BLE001 - extraction is best-effort
            return []
        return self._parse_learnings(raw)

    @staticmethod
    def _build_extract_prompt(
        history: list, user_message: str, jack_response: str
    ) -> str:
        """A SHORT prompt: at most the previous exchange + the current turn. Keeps
        well under ~3k tokens regardless of how long the session history is."""
        lines: list[str] = []
        prev = list(history or [])[-2:]  # at most one previous user+assistant pair
        for msg in prev:
            try:
                who = "Arnav" if msg.get("role") == "user" else "Jack"
                content = str(msg.get("content", "")).strip()
            except AttributeError:
                continue
            if content:
                lines.append(f"{who}: {content}")
        lines.append(f"Arnav: {user_message}")
        lines.append(f"Jack: {jack_response}")
        return (
            "Latest conversation (extract durable NEW facts about Arnav only):\n"
            + "\n".join(lines)
        )

    @staticmethod
    def _parse_learnings(raw) -> list[dict]:
        """Defensively parse the model output into validated learnings."""
        if not raw or not isinstance(raw, str):
            return []
        text = raw.strip()
        # Strip code fences if the model added them despite instructions.
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text).strip()
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return []
        # Accept either a bare list or {"learnings": [...]} / a single object.
        if isinstance(data, dict):
            if "learnings" in data and isinstance(data["learnings"], list):
                data = data["learnings"]
            elif "section" in data and "fact" in data:
                data = [data]
            else:
                return []
        if not isinstance(data, list):
            return []

        out: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            section = str(item.get("section", "")).strip()
            fact = str(item.get("fact", "")).strip()
            if not fact or section not in _VALID_SECTIONS:
                continue
            out.append({"section": section, "fact": fact})
        return out

    # -- USER.md write (append-only, atomic, locked) --------------------------
    def update_user_md(
        self, learnings: list[dict], today: _dt.date | None = None
    ) -> int:
        """Append each learning to its section. Returns the number appended.

        Never deletes or rewrites an existing line. Unknown sections route to
        THINGS JACK HAS LEARNED. Uses flock + atomic tmp-write + os.replace."""
        if not learnings:
            return 0
        path = self._user_path
        try:
            original = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return 0

        date_str = _today_str(today)
        lines = original.split("\n")
        appended = 0
        for learning in learnings:
            section = learning.get("section", "")
            fact = (learning.get("fact", "") or "").strip()
            if not fact:
                continue
            if section not in _VALID_SECTIONS:
                section = _THINGS_LEARNED
            if section == _THINGS_LEARNED:
                new_line = f"{date_str}: {fact}"
            else:
                new_line = f"- {fact}"
            lines = self._append_into_section(lines, section, new_line)
            appended += 1

        if appended == 0:
            return 0

        lines = self._bump_last_updated(lines, date_str)
        new_text = "\n".join(lines)
        self._atomic_write(path, new_text)
        return appended

    @staticmethod
    def _append_into_section(lines: list[str], section: str, new_line: str) -> list[str]:
        """Return a NEW list with `new_line` inserted at the END of `section`
        (just before the next [SECTION] header or EOF). If the section header is
        absent, append the line under a THINGS JACK HAS LEARNED block (creating it
        only if it too is missing). Existing lines are never modified."""
        header = f"[{section}]"
        start = None
        for i, line in enumerate(lines):
            if line.strip() == header:
                start = i
                break
        if start is None:
            # Section header not present: fall back to THINGS JACK HAS LEARNED.
            if section != _THINGS_LEARNED:
                return MemoryUpdater._append_into_section(
                    lines, _THINGS_LEARNED, new_line
                )
            # Even THINGS JACK HAS LEARNED is missing — create it at EOF.
            result = list(lines)
            if result and result[-1].strip() != "":
                result.append("")
            result.append(f"[{_THINGS_LEARNED}]")
            result.append(new_line)
            return result

        # Find the end of this section: next [SECTION] header, else EOF.
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if _SECTION_RE.match(lines[j].strip()):
                end = j
                break

        # Insert just before `end`, after the last non-blank line of the section
        # so we don't strand the new bullet beyond trailing blank separators.
        insert_at = end
        while insert_at - 1 > start and lines[insert_at - 1].strip() == "":
            insert_at -= 1

        result = list(lines)
        result.insert(insert_at, new_line)
        return result

    @staticmethod
    def _bump_last_updated(lines: list[str], date_str: str) -> list[str]:
        """Update the `Last updated: <date>` header line in place (value only).
        No-op if the header isn't present."""
        result = list(lines)
        for i, line in enumerate(result):
            if _LAST_UPDATED_RE.match(line.strip()):
                result[i] = f"Last updated: {date_str}"
                break
        return result

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """flock the target, write to a sibling tmp file, fsync, os.replace.

        The lock is held on the real file for the duration so concurrent updaters
        serialize; the replace is atomic so a reader never sees a half-written
        file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        # Acquire an exclusive advisory lock on the destination (create if needed).
        lock_fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            tmp = tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(path.parent),
                prefix=path.name + ".",
                suffix=".tmp",
                delete=False,
            )
            try:
                tmp.write(content)
                tmp.flush()
                os.fsync(tmp.fileno())
            finally:
                tmp.close()
            os.replace(tmp.name, path)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    # -- async orchestration (fire-and-forget from the chat path) -------------
    async def run_async(
        self, history: list, user_message: str, jack_response: str
    ) -> None:
        """Extract + persist off-thread, then log. Swallows ALL exceptions so a
        background memory update can never crash the caller. No-op when
        JACK_MEMORY_ENABLED is falsy."""
        if not _memory_enabled():
            return
        try:
            learnings = await asyncio.to_thread(
                self.extract_learnings, history, user_message, jack_response
            )
            count = 0
            if learnings:
                count = await asyncio.to_thread(self.update_user_md, learnings)
            self._log_learnings(learnings, count)
        except Exception:  # noqa: BLE001 - background task: never propagate
            return

    def _log_learnings(self, learnings: list[dict], count: int) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            ts = _dt.datetime.now().isoformat(timespec="seconds")
            if learnings:
                summary = "; ".join(
                    f"[{l['section']}] {l['fact']}" for l in learnings
                )
                msg = f"{ts} learned {count}: {summary}\n"
            else:
                msg = f"{ts} nothing new\n"
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(msg)
        except OSError:
            return


def _memory_enabled() -> bool:
    val = os.environ.get("JACK_MEMORY_ENABLED", "1").strip().lower()
    return val in {"1", "true", "yes", "on"}
