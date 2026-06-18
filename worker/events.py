"""Tiny async event bus (mission §5).

Workers/orchestrator publish events; Hermes Prime subscribes. The one event that
matters here is `task_complete`: when all workers finish, Prime emits it and the
subscribed handler sends the Discord summary automatically.

Deliberately minimal — an in-process pub/sub, not a broker. Handlers may be sync
or async; both are awaited correctly.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Optional

TASK_COMPLETE = "task_complete"

Handler = Callable[[dict], Optional[Awaitable]]


class EventBus:
    def __init__(self):
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event: str, handler: Handler) -> None:
        self._subs[event].append(handler)

    async def emit(self, event: str, payload: dict) -> None:
        """Fire all handlers for `event`. Coroutine handlers are awaited; sync
        handlers run in a thread so a blocking call (e.g. a webhook POST) can't
        stall the event loop. Handler errors are isolated so one bad subscriber
        can't sink the others."""
        for handler in list(self._subs.get(event, [])):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(payload)
                else:
                    await asyncio.to_thread(handler, payload)  # don't block the loop
            except Exception as e:  # noqa: BLE001 - isolate subscriber failures
                print(f"[events] handler for {event!r} raised: {e}")
