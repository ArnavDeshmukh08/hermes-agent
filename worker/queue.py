"""Filesystem task queue with atomic state transitions (mission §4).

    tasks/
      pending/    <- enqueued, unclaimed
      running/    <- claimed by exactly one worker
      completed/  <- finished, result attached
      failed/     <- raised, error attached

Workers consume tasks **independently**: `claim()` moves a file pending->running
via os.rename, which is atomic on POSIX — two workers can race for the same file
and only one wins (the loser gets FileNotFoundError and tries the next). No locks,
no DB. State == which directory the file is in.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from lib import store

STATES = ("pending", "running", "completed", "failed")
_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9_\-]+$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Task:
    """A unit of work. `kind` selects the worker; `payload` is its input."""

    id: str
    kind: str
    payload: dict
    parent: str | None = None
    created_at: str = field(default_factory=_now_iso)
    result: dict | None = None
    error: str | None = None
    history: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "payload": self.payload,
            "parent": self.parent,
            "created_at": self.created_at,
            "result": self.result,
            "error": self.error,
            "history": self.history,
        }

    @staticmethod
    def from_dict(d: dict) -> Task:
        return Task(
            id=d["id"],
            kind=d["kind"],
            payload=d.get("payload", {}),
            parent=d.get("parent"),
            created_at=d.get("created_at", _now_iso()),
            result=d.get("result"),
            error=d.get("error"),
            history=d.get("history", []),
        )


def new_task_id(kind: str) -> str:
    return f"{kind}-{uuid.uuid4().hex[:12]}"


class TaskQueue:
    """A four-state, file-backed work queue rooted at `root` (default tasks/)."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.environ.get("HERMES_TASKS_ROOT") or "tasks")
        self._dirs = {s: self.root / s for s in STATES}
        for d in self._dirs.values():
            d.mkdir(parents=True, exist_ok=True)

    # -- atomic primitives --------------------------------------------------
    def _path(self, state: str, task_id: str) -> Path:
        return self._dirs[state] / f"{task_id}.json"

    def _atomic_write(self, path: Path, task: Task) -> None:
        # Reuse the store's atomic writer (tmp + fsync + os.replace, with
        # tmp-cleanup-on-failure) instead of a divergent copy.
        store.atomic_write_json(path, task.to_dict())

    # -- public API ---------------------------------------------------------
    def enqueue(self, task: Task) -> Task:
        task.history.append(("pending", _now_iso()))
        self._atomic_write(self._path("pending", task.id), task)
        return task

    def open_task(self, task: Task) -> Task:
        """Write a task straight into running/ (Hermes Prime's own job record,
        not something the worker pool should claim from pending/)."""
        task.history.append(("running", _now_iso()))
        self._atomic_write(self._path("running", task.id), task)
        return task

    def claim(self) -> Task | None:
        """Atomically move the oldest pending task to running and return it.

        Returns None when no pending task can be claimed. Concurrency-safe:
        os.rename is atomic, so a lost race surfaces as FileNotFoundError and
        we simply try the next candidate.
        """
        pending = sorted(self._dirs["pending"].glob("*.json"), key=lambda p: p.stat().st_mtime)
        for src in pending:
            task_id = src.stem
            if not _SAFE_TASK_ID.match(task_id):
                continue  # ignore malformed/hostile filenames in the queue dir
            dst = self._path("running", task_id)
            try:
                os.rename(str(src), str(dst))  # atomic claim
            except (FileNotFoundError, OSError):
                continue  # another worker grabbed it; keep looking
            task = Task.from_dict(json.loads(dst.read_text(encoding="utf-8")))
            task.history.append(("running", _now_iso()))
            self._atomic_write(dst, task)
            return task
        return None

    def complete(self, task: Task, result: dict) -> None:
        task.result = result
        task.history.append(("completed", _now_iso()))
        self._atomic_write(self._path("completed", task.id), task)
        self._remove("running", task.id)

    def fail(self, task: Task, error: str) -> None:
        task.error = error
        task.history.append(("failed", _now_iso()))
        self._atomic_write(self._path("failed", task.id), task)
        self._remove("running", task.id)

    def _remove(self, state: str, task_id: str) -> None:
        try:
            self._path(state, task_id).unlink()
        except OSError:
            pass

    # -- introspection ------------------------------------------------------
    def counts(self) -> dict:
        return {s: len(list(self._dirs[s].glob("*.json"))) for s in STATES}

    def list_tasks(self, state: str, parent: str | None = None) -> list:
        out = []
        for p in sorted(self._dirs[state].glob("*.json")):
            try:
                t = Task.from_dict(json.loads(p.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
            if parent is None or t.parent == parent:
                out.append(t)
        return out

    def drain(self) -> None:
        """Remove all task files (used between runs/tests)."""
        for d in self._dirs.values():
            for p in d.glob("*.json"):
                try:
                    p.unlink()
                except OSError:
                    pass
