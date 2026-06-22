"""JackMemoryClient — thin, injectable Mem0 wrapper for Jack's semantic memory.

Design contract:
- mem0 is imported LAZILY inside _client() — importing this module does NOT
  require mem0ai at test time. Tests inject a mock mem0_client instead.
- search() is SYNCHRONOUS and bounded by a hard timeout (default 1.5s).
  It NEVER invokes the extraction LLM; it only embeds the query locally
  (VPS Ollama nomic-embed-text) and queries Qdrant. Returns [] on timeout/error.
  This is the hot-path context retrieval — it must never stall respond().
- add() calls the extraction LLM (Mac via Tailscale). It propagates exceptions
  so the caller (Mem0MemoryUpdater) can decide to queue instead.
- All env vars have safe defaults; flatfile path is never disrupted.
"""

from __future__ import annotations

import concurrent.futures
import os
from typing import Union


def build_mem0_config() -> dict:
    """Build the mem0ai config dict from environment variables.

    CRITICAL nesting requirement: embedding_model_dims MUST be inside
    vector_store.config — never at the top level. This is a documented
    mem0ai failure mode where misplacing it silently breaks the collection.
    """
    return {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": "jack_memory",
                "host": os.environ.get("JACK_QDRANT_HOST", "localhost"),
                "port": int(os.environ.get("JACK_QDRANT_PORT", "6333")),
                "embedding_model_dims": 768,  # MUST be here, INSIDE vector_store.config
            },
        },
        "embedder": {
            "provider": "ollama",
            "config": {
                "model": os.environ.get("JACK_EMBED_MODEL", "nomic-embed-text"),
                "ollama_base_url": os.environ.get(
                    "JACK_EMBED_URL", "http://localhost:11434"
                ),
            },
        },
        "llm": {
            "provider": "ollama",
            "config": {
                "model": os.environ.get("JACK_EXTRACT_MODEL", "qwen2.5:14b"),
                "temperature": 0,
                "max_tokens": 2000,
                "ollama_base_url": os.environ.get(
                    "JACK_EXTRACT_URL", "http://100.120.65.115:11434"
                ),
            },
        },
    }


class JackMemoryClient:
    """Thin injectable wrapper around mem0.Memory for Jack's semantic memory.

    For testing, inject a ``mem0_client`` mock — this avoids any real network
    calls and eliminates the mem0ai import requirement at test time.

    For production, call ``JackMemoryClient.from_env()`` or let the client
    self-initialise lazily on first use.
    """

    def __init__(
        self,
        mem0_client=None,  # injected in tests; real mem0.Memory built lazily if None
        *,
        config: Union[dict, None] = None,
        search_timeout_s: float = 1.5,
        user_id: str = "arnav",
    ) -> None:
        self._mem0 = mem0_client
        self._config = config
        self._timeout = search_timeout_s
        self._user_id = user_id

    @classmethod
    def from_env(cls) -> "JackMemoryClient":
        """Build a real client from env vars. Do not call in tests."""
        return cls(config=build_mem0_config())

    def _client(self):
        """Lazy-init real mem0.Memory(config=...) if no injected mock."""
        if self._mem0 is None:
            from mem0 import Memory  # lazy — not at module level

            self._mem0 = Memory.from_config(self._config or build_mem0_config())
            self._ensure_low_threshold()
        return self._mem0

    def _ensure_low_threshold(self) -> None:
        """Patch the Qdrant collection to index small collections immediately.

        Qdrant's default indexing_threshold is 20000 — the HNSW index is never
        built until that many vectors exist. With <1000 personal facts we would
        never get indexed, so .search() (which requires the vector index) always
        returns empty while scroll/get_all (no index needed) works fine.

        Setting threshold to 100 forces the index to build after 100 insertions,
        and triggers an index rebuild on the next optimizer cycle for an existing
        collection. Idempotent and best-effort — won't break init if Qdrant is down.
        """
        import json
        import urllib.request

        host = os.environ.get("JACK_QDRANT_HOST", "localhost")
        port = int(os.environ.get("JACK_QDRANT_PORT", "6333"))
        url = f"http://{host}:{port}/collections/jack_memory"
        payload = json.dumps({"optimizer_config": {"indexing_threshold": 100}}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            method="PATCH",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=2)
        except Exception:  # noqa: BLE001 — best-effort; never break init
            pass

    def search(
        self,
        query: str,
        user_id: Union[str, None] = None,
        limit: int = 8,
    ) -> list[dict]:
        """Fast synchronous retrieval: local embed (VPS) + Qdrant only.

        Wraps _client().search in a ThreadPoolExecutor with search_timeout_s.
        Returns [] on timeout, exception, or empty result.
        NEVER calls the extraction LLM. NEVER touches the Mac.
        """
        uid = user_id or self._user_id
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(self._client().search, query, filters={"user_id": uid}, top_k=limit)
                result = future.result(timeout=self._timeout)
            # mem0 search returns {"results": [...]} or a list depending on version
            if isinstance(result, dict):
                return result.get("results", [])
            return list(result) if result else []
        except Exception:  # noqa: BLE001 — timeout, Qdrant down, etc. → degrade silently
            return []

    def add(
        self,
        messages: Union[list[dict], str],
        user_id: Union[str, None] = None,
        metadata: Union[dict, None] = None,
    ) -> dict:
        """Add memories via extraction LLM (Mac via Tailscale).

        Propagates exceptions — caller queues on failure.
        """
        uid = user_id or self._user_id
        kwargs: dict = {"user_id": uid}
        if metadata is not None:
            kwargs["metadata"] = metadata
        return self._client().add(messages, **kwargs)

    @staticmethod
    def format_for_prompt(memories: list[dict]) -> str:
        """Render a memory block for injection into the system prompt.

        Returns empty string if no memories.
        Format:
            # What I remember about you
            - <memory text>
            - <memory text>
        """
        if not memories:
            return ""
        lines = ["# What I remember about you"]
        for m in memories:
            text = m.get("memory") or m.get("text") or str(m)
            if text:
                lines.append(f"- {text}")
        return "\n".join(lines)

    def health(self) -> dict:
        """Check reachability of embedder (VPS) and extractor (Mac).

        Returns {'embedder': bool, 'extractor': bool}.
        Probes /api/tags with a 1s timeout.
        """
        import urllib.request

        def probe(url: str) -> bool:
            try:
                urllib.request.urlopen(url, timeout=1)
                return True
            except Exception:  # noqa: BLE001
                return False

        embed_url = os.environ.get("JACK_EMBED_URL", "http://localhost:11434")
        extract_url = os.environ.get("JACK_EXTRACT_URL", "http://100.120.65.115:11434")
        return {
            "embedder": probe(f"{embed_url}/api/tags"),
            "extractor": probe(f"{extract_url}/api/tags"),
        }
