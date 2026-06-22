"""GoalStore — durable, flock-safe JSON storage for Jack's goals.

Goals are first-class durable objects (NOT memory facts). Stored at
~/.hermes/goals.json as {"goals": [ {goal dict}, ... ]}. Every read takes
LOCK_SH, every write serializes on a sidecar .lock file under LOCK_EX and
writes atomically (tmp + os.replace) so a crash never half-writes the file.

Corrupt-file recovery: if the JSON fails to parse, we log a warning and
return an empty list — we never crash the caller (reasoner runs unattended).
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from jack_goals.models import Goal, new_goal

_logger = logging.getLogger("jack_goals.store")

VALID_STATUSES = ("active", "paused", "done")
# Fields a caller may update via update_goal(); id/created_at are immutable.
_UPDATABLE = frozenset(
    {"title", "type", "target", "plan", "metric", "deadline",
     "status", "progress_notes", "last_checked"}
)


def _default_path() -> Path:
    """env JACK_GOALS_PATH, else ~/.hermes/goals.json."""
    env = os.environ.get("JACK_GOALS_PATH")
    if env:
        return Path(env).expanduser()
    return Path("~/.hermes/goals.json").expanduser()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class GoalStore:
    """JSON-backed goal store with flock + threading.Lock concurrency safety.

    The in-process threading.Lock serializes concurrent threads sharing one
    GoalStore instance. The sidecar fcntl.LOCK_EX serializes concurrent
    processes (e.g. scheduler + gateway) on the same file. Together they
    prevent the read-modify-write race that would otherwise let a late writer
    overwrite a concurrent write.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path).expanduser() if path is not None else _default_path()
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._write_all([])

    # -- low-level IO ----------------------------------------------------
    def _read_all(self) -> list[dict]:
        """Read all goal dicts under a shared lock. Returns [] on corruption."""
        if not self._path.exists():
            return []
        with open(self._path, "r", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
            try:
                text = fh.read()
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        if not text.strip():
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            _logger.warning("goals.json parse failed (%s) — returning empty list", e)
            return []
        goals = data.get("goals") if isinstance(data, dict) else None
        return goals if isinstance(goals, list) else []

    def _write_all(self, goals: list[dict]) -> None:
        """Atomically write {"goals": [...]} under an exclusive sidecar lock."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        with open(lock_path, "a+", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                fd, tmp = tempfile.mkstemp(
                    dir=str(self._path.parent), prefix=".tmp-goals-", suffix=".json"
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        json.dump({"goals": goals}, fh, ensure_ascii=False, indent=2)
                        fh.write("\n")
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp, str(self._path))
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
            finally:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    # -- public API ------------------------------------------------------
    def create_goal(
        self,
        title: str,
        type: str,            # noqa: A002 — matches domain vocabulary
        target: str,
        plan: str,
        metric: str,
        deadline: str | None = None,
    ) -> Goal:
        """Create + persist a new active goal. Returns the Goal."""
        goal = new_goal(
            title=title, type=type, target=target,
            plan=plan, metric=metric, deadline=deadline,
        )
        with self._lock:
            goals = self._read_all()
            goals.append(goal.to_dict())
            self._write_all(goals)
        return goal

    def get_goal(self, goal_id: str) -> Goal | None:
        for g in self._read_all():
            if g.get("id") == goal_id:
                return Goal.from_dict(g)
        return None

    def list_active_goals(self) -> list[Goal]:
        return [Goal.from_dict(g) for g in self._read_all() if g.get("status") == "active"]

    def list_all_goals(self) -> list[Goal]:
        return [Goal.from_dict(g) for g in self._read_all()]

    def update_goal(self, goal_id: str, **kwargs) -> Goal | None:
        """Patch arbitrary fields. Unknown / immutable keys are ignored.
        Returns the updated Goal, or None if no goal matched."""
        with self._lock:
            goals = self._read_all()
            updated: Goal | None = None
            for g in goals:
                if g.get("id") != goal_id:
                    continue
                for k, v in kwargs.items():
                    if k in _UPDATABLE:
                        g[k] = v
                updated = Goal.from_dict(g)
                break
            if updated is not None:
                self._write_all(goals)
        return updated

    def append_progress(self, goal_id: str, note: str) -> Goal | None:
        """Append a timestamped note to progress_notes (append-only) and stamp
        last_checked. Returns the updated Goal, or None if not found."""
        note = (note or "").strip()
        if not note:
            return self.get_goal(goal_id)
        with self._lock:
            goals = self._read_all()
            updated: Goal | None = None
            for g in goals:
                if g.get("id") != goal_id:
                    continue
                notes = list(g.get("progress_notes") or [])
                notes.append(f"[{_now_iso()}] {note}")
                g["progress_notes"] = notes
                g["last_checked"] = _now_iso()
                updated = Goal.from_dict(g)
                break
            if updated is not None:
                self._write_all(goals)
        return updated

    def set_status(self, goal_id: str, status: str) -> Goal | None:
        """Set status to one of VALID_STATUSES. Raises ValueError on bad status."""
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status!r}")
        return self.update_goal(goal_id, status=status)


# -- module-level convenience layer over a default singleton ---------------
_DEFAULT: GoalStore | None = None


def _default_store() -> GoalStore:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = GoalStore()
    return _DEFAULT


def create_goal(title: str, type: str, target: str, plan: str, metric: str, deadline: str | None = None) -> Goal:  # noqa: A002
    return _default_store().create_goal(title, type, target, plan, metric, deadline)


def get_goal(goal_id: str) -> Goal | None:
    return _default_store().get_goal(goal_id)


def list_active_goals() -> list[Goal]:
    return _default_store().list_active_goals()


def update_goal(goal_id: str, **kw) -> Goal | None:
    return _default_store().update_goal(goal_id, **kw)


def append_progress(goal_id: str, note: str) -> Goal | None:
    return _default_store().append_progress(goal_id, note)


def set_status(goal_id: str, status: str) -> Goal | None:
    return _default_store().set_status(goal_id, status)
