"""Backend factory — reads JACK_MEMORY_BACKEND (mem0|flatfile, default flatfile).

Single chokepoint for the flag. Keeps the flag logic OUT of conversation.py
and OUT of every other consumer. Default 'flatfile' keeps all existing tests
green and the legacy path byte-identical on the VPS until the flag is flipped.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def memory_backend() -> str:
    """Return 'mem0' or 'flatfile'. Default: 'flatfile'."""
    return os.environ.get("JACK_MEMORY_BACKEND", "flatfile").strip().lower()


def build_updater(
    user_path: Path,
    *,
    client: Any = None,
    queue: Any = None,
) -> Any:
    """Return the appropriate memory updater for the current backend.

    flatfile → MemoryUpdater (existing, unchanged)
    mem0     → Mem0MemoryUpdater (new)
    """
    if memory_backend() == "mem0":
        from jack_memory.mem0_adapter import Mem0MemoryUpdater
        return Mem0MemoryUpdater(client=client, queue=queue)
    else:
        from jack_memory.updater import MemoryUpdater
        return MemoryUpdater(user_path=user_path)


def build_retriever(*, client: Any = None) -> Any:
    """Return JackMemoryClient for mem0 backend, None for flatfile.

    None return means: use cached USER.md profile (legacy path).
    """
    if memory_backend() == "mem0":
        if client is not None:
            return client
        from jack_memory.client import JackMemoryClient
        return JackMemoryClient.from_env()
    return None
