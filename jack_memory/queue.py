"""MemoryQueue — durable crash-safe queue for memory extraction backlog.

When the Mac (extraction LLM) is unreachable, Mem0MemoryUpdater enqueues
the raw conversation here instead of dropping it. The proactive scheduler
drains the queue when the Mac comes back.

Storage: ~/.hermes/memory_queue.json — JSON array, fcntl.flock-protected.
Guarantee: an item is removed ONLY after a successful client.add() call.
           A crash between add and remove at worst re-adds one item, which
           Mem0's dedup handles.
Locking: LOCK_EX for all mutations (read-modify-write); tmp-write + os.replace
         (same pattern as jack_memory.updater._atomic_write).
"""

from __future__ import annotations

import datetime
import fcntl
import json
import os
import tempfile
import uuid
from pathlib import Path

_DEFAULT_QUEUE_PATH = Path.home() / ".hermes" / "memory_queue.json"


class MemoryQueue:
    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else _DEFAULT_QUEUE_PATH

    def enqueue(self, item: dict) -> str:
        """Append item to queue. Returns item_id.

        item should contain at minimum:
          {user_id: str, messages: list[dict]}
        A uuid4 id and ISO timestamp are added automatically.
        Atomic: LOCK_EX + read-modify-write + tmp-replace.
        """
        item_id = item.get("id") or str(uuid.uuid4())
        record = {
            "id": item_id,
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            **item,
        }
        self._update(lambda items: items + [record])
        return item_id

    def peek_all(self) -> list[dict]:
        """Return a snapshot of all queued items (LOCK_SH read)."""
        return self._read()

    def remove(self, item_id: str) -> None:
        """Remove a single item by id. LOCK_EX + atomic write.

        No-op if item_id not found (idempotent removal is safe).
        """
        self._update(lambda items: [i for i in items if i.get("id") != item_id])

    def drain(self, add_fn) -> int:
        """Drain the queue by calling add_fn(item) for each queued item.

        - Processes FIFO.
        - Removes an item ONLY after add_fn returns without raising.
        - Stops on first exception (Mac went down mid-drain) — remaining items stay queued.
        - Returns count successfully drained.
        - Never raises.
        """
        drained = 0
        try:
            items = self.peek_all()
            for item in items:
                try:
                    add_fn(item)
                    self.remove(item["id"])
                    drained += 1
                except Exception:  # noqa: BLE001 — Mac down, stop and leave remainder
                    break
        except Exception:  # noqa: BLE001 — never raise out of drain
            pass
        return drained

    def __len__(self) -> int:
        return len(self.peek_all())

    # -- internal atomic read-modify-write ------------------------------------

    @property
    def _lock_path(self) -> Path:
        """Sibling lock file that is never replaced (unlike the data file).

        os.replace atomically swaps inodes on POSIX, which means a lock_fd
        opened on the data file before a replace would reference a stale inode
        after the replace — so subsequent threads acquire their lock on a ghost
        file, bypassing serialisation. Using a dedicated .lock file that is
        never replaced avoids this race entirely.
        """
        return self._path.with_suffix(".lock")

    def _read(self) -> list[dict]:
        """Read queue file with LOCK_SH. Returns [] if missing or corrupt."""
        if not self._path.exists():
            return []
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
                try:
                    text = fh.read()
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            data = json.loads(text)
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001 — corrupt file → treat as empty
            return []

    def _update(self, transform) -> None:
        """LOCK_EX read-modify-write + atomic tmp-replace.

        `transform` is a function (list[dict]) -> list[dict].
        The lock is held on _lock_path (a stable sibling file that is never
        replaced) for the duration, ensuring cross-thread and cross-process
        serialisation even though os.replace swaps the data file's inode.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(str(self._lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    text = fh.read().strip()
                existing = json.loads(text) if text else []
            except Exception:  # noqa: BLE001 — start fresh if corrupt
                existing = []
            updated = transform(existing)
            tmp = tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self._path.parent),
                prefix=self._path.name + ".",
                suffix=".tmp",
                delete=False,
            )
            try:
                json.dump(updated, tmp, ensure_ascii=False, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
            finally:
                tmp.close()
            os.replace(tmp.name, str(self._path))
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
