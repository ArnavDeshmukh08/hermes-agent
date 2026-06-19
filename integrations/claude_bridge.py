"""ClaudeBridge — ingest a pasted Claude/claude.ai session transcript, summarize
it via the LLM, append durable facts to USER.md, and ping Discord.

Usage (CLI):
    python -m integrations.claude_bridge --text "paste transcript here"
    echo "transcript" | python -m integrations.claude_bridge

Design rules (mirrors jack_memory/updater.py):
- All external deps (lib.llm, jack_memory.updater, reminders.notifier) are
  imported lazily, inside methods — never at module import time. Tests inject
  fakes so no network or filesystem dep is required to import this module.
- Every constructor arg defaults to None and is resolved lazily on first use.
- Graceful degradation: blank input → no-op; LLM failure → ''; notify failure
  → False. run() never raises into the caller; each step is independently
  guarded so a notify failure cannot zero out an already-successful append.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable

# ---------------------------------------------------------------------------
# Prompt used when summarising a pasted session into durable facts.
# ---------------------------------------------------------------------------
_SUMMARISE_SYSTEM = (
    "You are Jack. Summarize this Claude session into durable, concrete facts "
    "and decisions worth remembering about Arnav's work. "
    "Concise bullet facts, no fluff."
)

# Groq free-tier context window is ~8 192 tokens (~32 k chars for typical text).
# We cap the pasted transcript at 8 000 chars (taking the LAST portion — the
# most recent context) so we stay safely under the limit even after the system
# prompt is prepended.
_MAX_TRANSCRIPT_CHARS = 8_000

_WORK_SECTION = "WORK & PROJECTS"


class ClaudeBridge:
    """Summarise a pasted Claude session and push the summary into Jack's memory.

    All three external collaborators are injectable so tests can pass fakes
    without any network or filesystem access:

        bridge = ClaudeBridge(
            complete_fn=fake_llm,
            updater=FakeUpdater(),
            send_fn=fake_discord,
        )

    When a collaborator is left as None it is resolved lazily on first use.
    """

    def __init__(
        self,
        complete_fn: Callable | None = None,
        updater=None,
        send_fn: Callable | None = None,
    ) -> None:
        self._complete_fn = complete_fn   # None → lib.llm.complete (lazy)
        self._updater = updater           # None → MemoryUpdater()   (lazy)
        self._send_fn = send_fn           # None → send_message      (lazy)

    # ------------------------------------------------------------------
    # Lazy resolver helpers
    # ------------------------------------------------------------------

    def _resolve_complete_fn(self) -> Callable:
        if self._complete_fn is not None:
            return self._complete_fn
        from lib import llm  # noqa: PLC0415 — intentional lazy import
        return llm.complete

    def _resolve_updater(self):
        if self._updater is not None:
            return self._updater
        from jack_memory.updater import MemoryUpdater  # noqa: PLC0415
        self._updater = MemoryUpdater()
        return self._updater

    def _resolve_send_fn(self) -> Callable:
        if self._send_fn is not None:
            return self._send_fn
        from reminders.notifier import send_message  # noqa: PLC0415
        return send_message

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def summarize(self, session_text: str) -> str:
        """Return a bullet-point summary of the pasted session.

        Returns '' (without calling the LLM) for blank input.
        Returns '' on any LLM failure — never raises.
        """
        if not session_text or not session_text.strip():
            return ""

        # Truncate to the LAST _MAX_TRANSCRIPT_CHARS characters so Groq's free
        # tier context window isn't exceeded regardless of how long the paste is.
        text = session_text
        if len(text) > _MAX_TRANSCRIPT_CHARS:
            text = text[-_MAX_TRANSCRIPT_CHARS:]

        complete_fn = self._resolve_complete_fn()
        try:
            result = complete_fn(
                _SUMMARISE_SYSTEM,
                text,
                prefer="groq",
                max_tokens=400,
            )
            return result or ""
        except Exception:  # noqa: BLE001 — best-effort; never propagate
            return ""

    def push_to_memory(self, summary: str, today=None) -> int:
        """Append each non-empty line of `summary` as a WORK & PROJECTS fact.

        Returns the number of facts appended (0 for empty summary).
        Unknown section labels are safely routed to THINGS JACK HAS LEARNED by
        MemoryUpdater, so the hard-coded section here is just the best default.
        """
        if not summary or not summary.strip():
            return 0

        learnings = []
        for line in summary.splitlines():
            # Strip leading bullet characters (-, *, •) and whitespace.
            fact = line.strip().lstrip("-*•").strip()
            if fact:
                learnings.append({"section": _WORK_SECTION, "fact": fact})

        if not learnings:
            return 0

        updater = self._resolve_updater()
        return updater.update_user_md(learnings, today)

    def notify(self, summary: str) -> bool:
        """Post the summary to Discord.

        Returns True on success, False on dry-run or any failure. Never raises.
        """
        send_fn = self._resolve_send_fn()
        content = f"🧠 Logged a Claude session summary:\n{summary}"
        try:
            return bool(send_fn(content))
        except Exception:  # noqa: BLE001 — delivery failure must not propagate
            return False

    def run(self, session_text: str, today=None) -> dict:
        """Full pipeline: summarize → push to memory → notify Discord.

        Each step is independently guarded. A notify failure cannot zero out
        'appended'. Never raises.

        Returns:
            {
                'summary':   str,   # '' on blank input or LLM failure
                'appended':  int,   # facts written to USER.md
                'notified':  bool,  # True only if Discord delivery succeeded
            }
        """
        summary = ""
        appended = 0
        notified = False

        try:
            summary = self.summarize(session_text)
        except Exception:  # noqa: BLE001
            summary = ""

        try:
            appended = self.push_to_memory(summary, today)
        except Exception:  # noqa: BLE001
            appended = 0

        try:
            notified = self.notify(summary)
        except Exception:  # noqa: BLE001
            notified = False

        return {"summary": summary, "appended": appended, "notified": notified}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest a pasted Claude session transcript into Jack's memory.",
    )
    parser.add_argument(
        "--text",
        type=str,
        default=None,
        help="Session transcript text. Reads from stdin when omitted.",
    )
    args = parser.parse_args()

    session_text = args.text if args.text is not None else sys.stdin.read()

    bridge = ClaudeBridge()
    result = bridge.run(session_text)
    print(result)


if __name__ == "__main__":
    main()
