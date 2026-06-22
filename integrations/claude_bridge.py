"""ClaudeBridge — ingest a pasted Claude/claude.ai session transcript, summarize
it via the LLM, push durable facts to Mem0 (via JackMemoryClient), and ping Discord.

Usage (CLI):
    python -m integrations.claude_bridge --text "paste transcript here"
    echo "transcript" | python -m integrations.claude_bridge

Design rules:
- All external deps (lib.llm, jack_memory.client, reminders.notifier) are
  imported lazily, inside methods — never at module import time. Tests inject
  fakes so no network or filesystem dep is required to import this module.
- Every constructor arg defaults to None and is resolved lazily on first use.
- Graceful degradation: blank input → no-op with error='blank input';
  LLM failure → error='no output'; Mem0 failure → error captured, notified=False.
- run() NEVER reports notified:True when appended:0 — that is a false success.
  Discord notification fires only after facts are confirmed stored in Mem0.
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


class ClaudeBridge:
    """Summarise a pasted Claude session and push the summary into Mem0.

    All three external collaborators are injectable so tests can pass fakes
    without any network or filesystem access:

        bridge = ClaudeBridge(
            complete_fn=fake_llm,
            mem0_client=FakeMem0Client(),
            send_fn=fake_discord,
        )

    When a collaborator is left as None it is resolved lazily on first use.
    """

    def __init__(
        self,
        complete_fn: Callable | None = None,
        mem0_client=None,
        send_fn: Callable | None = None,
    ) -> None:
        self._complete_fn = complete_fn   # None → lib.llm.complete (lazy)
        self._mem0_client = mem0_client   # None → JackMemoryClient.from_env() (lazy)
        self._send_fn = send_fn           # None → send_message (lazy)

    # ------------------------------------------------------------------
    # Lazy resolver helpers
    # ------------------------------------------------------------------

    def _resolve_complete_fn(self) -> Callable:
        if self._complete_fn is not None:
            return self._complete_fn
        from lib import llm  # noqa: PLC0415 — intentional lazy import
        return llm.complete

    def _resolve_mem0_client(self):
        if self._mem0_client is not None:
            return self._mem0_client
        from jack_memory.client import JackMemoryClient  # noqa: PLC0415
        self._mem0_client = JackMemoryClient.from_env()
        return self._mem0_client

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

    def push_to_memory(self, summary: str) -> int:
        """Push the summary into Mem0 via JackMemoryClient.add().

        mem0's extraction LLM deduplicates and structures the bullet facts
        before storing them in Qdrant. Returns the count of ADD + UPDATE events
        from mem0 (0 on empty input or any error — never raises).
        """
        if not summary or not summary.strip():
            return 0

        client = self._resolve_mem0_client()
        try:
            result = client.add(summary, metadata={"source": "claude_bridge"})
        except Exception:  # noqa: BLE001 — Mac extractor down, Qdrant down, etc.
            return 0

        if isinstance(result, dict):
            stored = [
                r for r in result.get("results", [])
                if r.get("event") in ("ADD", "UPDATE")
            ]
            return len(stored)
        return 1 if result else 0

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

    def run(self, session_text: str) -> dict:
        """Full pipeline: summarize → push to Mem0 → notify Discord.

        Discord notification fires ONLY when facts are confirmed stored in Mem0.
        notified:True with appended:0 is a false success — this method never
        produces that combination.

        Returns:
            {
                'summary':   str,        # '' on blank input or LLM failure
                'appended':  int,        # ADD+UPDATE events written to Mem0
                'notified':  bool,       # True only if appended>0 and Discord OK
                'error':     str|None,   # set when a step produces no output
            }
        """
        summary = self.summarize(session_text)

        if not summary:
            error = (
                "blank input" if not (session_text and session_text.strip())
                else "LLM returned empty summary"
            )
            return {"summary": "", "appended": 0, "notified": False, "error": error}

        appended = self.push_to_memory(summary)

        if appended == 0:
            return {
                "summary": summary,
                "appended": 0,
                "notified": False,
                "error": "nothing stored in Mem0",
            }

        notified = self.notify(summary)
        return {"summary": summary, "appended": appended, "notified": notified, "error": None}


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
