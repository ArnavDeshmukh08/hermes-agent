"""Mem0MemoryUpdater — drop-in replacement for jack_memory.updater.MemoryUpdater.

Exposes the SAME async surface (run_async) so conversation.py needs no shape
change. Instead of appending to USER.md, it:
  1. Tries to add the conversation to Mem0 via JackMemoryClient.add().
  2. If the Mac (extraction LLM) is unreachable, enqueues to MemoryQueue.
  3. Never raises to the caller (same contract as MemoryUpdater.run_async).

JackMemoryClient and MemoryQueue are constructor-injected for testability.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


class Mem0MemoryUpdater:
    """Background memory updater backed by Mem0 + graceful-degradation queue."""

    def __init__(
        self,
        *,
        client: Any = None,
        queue: Any = None,
        user_id: str = "arnav",
        log_path: Path | None = None,
    ) -> None:
        self._client = client   # None → built lazily from env
        self._queue = queue     # None → built lazily with default path
        self._user_id = user_id
        self._log_path = log_path or (Path.home() / ".hermes" / "logs" / "memory.log")

    def _get_client(self) -> Any:
        if self._client is None:
            from jack_memory.client import JackMemoryClient
            self._client = JackMemoryClient.from_env()
        return self._client

    def _get_queue(self) -> Any:
        if self._queue is None:
            from jack_memory.queue import MemoryQueue
            self._queue = MemoryQueue()
        return self._queue

    @staticmethod
    def _build_messages(
        history: list[dict], user_message: str, jack_response: str
    ) -> list[dict]:
        """Build messages list: last prior exchange + current turn."""
        msgs: list[dict] = []
        prior = list(history or [])[-2:]  # at most one prior exchange
        for msg in prior:
            try:
                role = "user" if msg.get("role") == "user" else "assistant"
                content = str(msg.get("content", "")).strip()
                if content:
                    msgs.append({"role": role, "content": content})
            except (AttributeError, TypeError):
                continue
        if user_message:
            msgs.append({"role": "user", "content": user_message})
        if jack_response:
            msgs.append({"role": "assistant", "content": jack_response})
        return msgs

    async def run_async(
        self, history: list, user_message: str, jack_response: str
    ) -> None:
        """Extract and store memories. Never raises. Background fire-and-forget.

        1. Builds messages from the conversation turn.
        2. Tries client.add() (may require Mac via Tailscale).
        3. If Mac unreachable (any exception from add()): enqueues to queue.
        4. Logs result to memory.log.
        """
        try:
            messages = self._build_messages(history, user_message, jack_response)
            if not messages:
                return
            try:
                await asyncio.to_thread(
                    self._get_client().add,
                    messages,
                    self._user_id,
                )
                self._log("extracted and stored via Mem0")
            except Exception as add_err:  # noqa: BLE001 — Mac down → queue
                item = {
                    "user_id": self._user_id,
                    "messages": messages,
                    "metadata": {"queued_reason": type(add_err).__name__},
                }
                self._get_queue().enqueue(item)
                self._log(f"queued (Mac unreachable: {type(add_err).__name__})")
        except Exception:  # noqa: BLE001 — never propagate to caller
            pass

    def _log(self, msg: str) -> None:
        try:
            import datetime
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().isoformat(timespec="seconds")
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(f"{ts} [mem0] {msg}\n")
        except OSError:
            pass
