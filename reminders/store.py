"""ReminderStore — durable, flock-safe JSON storage for Jack's reminders.

A standalone store (no framework dependency) so the scheduler can run as its
own service. The backing file is a JSON list of reminder dicts. We guard every
read/write with fcntl.flock (LOCK_SH for reads, LOCK_EX for writes) so a poll
loop and the gateway can touch the same file concurrently without corruption,
and we write atomically (tmp file + os.replace) so a crash never leaves a
half-written file behind.

Times are stored as ISO8601 UTC ending in 'Z'. The single source of reschedule
logic lives in mark_fired().
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_RECURRING_VALUES = ("daily", "weekday", None)


def _default_path() -> Path:
    """env JACK_REMINDERS_PATH, else ~/.hermes/reminders.json."""
    env = os.environ.get("JACK_REMINDERS_PATH")
    if env:
        return Path(env).expanduser()
    return Path("~/.hermes/reminders.json").expanduser()


def _to_z(dt: datetime) -> str:
    """Format a datetime as ISO8601 UTC ending in 'Z'."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def _from_z(value: str) -> datetime:
    """Parse an ISO8601 UTC string (with trailing 'Z') to an aware datetime."""
    text = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _next_weekday_after(dt: datetime) -> datetime:
    """Advance dt by at least one day, landing on the next Mon-Fri."""
    dt = dt + timedelta(days=1)
    while dt.weekday() >= 5:  # 5 = Sat, 6 = Sun
        dt = dt + timedelta(days=1)
    return dt


class ReminderStore:
    """JSON-backed reminder store with flock concurrency safety."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path).expanduser() if path is not None else _default_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._write_all([])

    # -- low-level IO ----------------------------------------------------
    def _read_all(self) -> list[dict]:
        """Read the full reminder list under a shared lock."""
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
        data = json.loads(text)
        return data if isinstance(data, list) else []

    def _write_all(self, reminders: list[dict]) -> None:
        """Atomically write the reminder list under an exclusive lock.

        We take LOCK_EX on a long-lived lock file so concurrent writers
        serialize, then os.replace the freshly written tmp file into place.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        with open(lock_path, "a+", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                fd, tmp = tempfile.mkstemp(
                    dir=str(self._path.parent), prefix=".tmp-reminders-", suffix=".json"
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        json.dump(reminders, fh, ensure_ascii=False, indent=2)
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
    def add(
        self,
        user_id: str,
        message: str,
        fire_at_utc: datetime,
        recurring: str | None = None,
    ) -> str:
        """Add a reminder. Returns the new uuid4 id."""
        if recurring not in _RECURRING_VALUES:
            raise ValueError(f"invalid recurring value: {recurring!r}")
        reminder = {
            "id": str(uuid.uuid4()),
            "user_id": str(user_id),
            "message": str(message),
            "fire_at": _to_z(fire_at_utc),
            "recurring": recurring,
            "fired": False,
            "created_at": _to_z(datetime.now(timezone.utc)),
        }
        reminders = self._read_all()
        reminders.append(reminder)
        self._write_all(reminders)
        return reminder["id"]

    def get_due(self, now: datetime | None = None) -> list[dict]:
        """Return un-fired reminders whose fire_at is at or before `now`."""
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)

        due = []
        for r in self._read_all():
            if r.get("fired"):
                continue
            if _from_z(r["fire_at"]) <= now:
                due.append(r)
        return due

    def mark_fired(self, reminder_id: str) -> None:
        """Mark a reminder fired.

        One-time reminders get fired=True. Recurring reminders keep
        fired=False and advance fire_at to the next occurrence (daily: +1 day,
        weekday: next Mon-Fri). This is the single source of reschedule logic.
        """
        reminders = self._read_all()
        changed = False
        for r in reminders:
            if r.get("id") != reminder_id:
                continue
            recurring = r.get("recurring")
            if recurring == "daily":
                current = _from_z(r["fire_at"])
                r["fire_at"] = _to_z(current + timedelta(days=1))
                r["fired"] = False
            elif recurring == "weekday":
                current = _from_z(r["fire_at"])
                r["fire_at"] = _to_z(_next_weekday_after(current))
                r["fired"] = False
            else:
                r["fired"] = True
            changed = True
            break
        if changed:
            self._write_all(reminders)

    def list_pending(self, user_id: str) -> list[dict]:
        """That user's not-yet-fired reminders, sorted by fire_at ascending."""
        pending = [
            r
            for r in self._read_all()
            if r.get("user_id") == str(user_id) and not r.get("fired")
        ]
        pending.sort(key=lambda r: _from_z(r["fire_at"]))
        return pending

    def cancel(self, reminder_id: str, user_id: str) -> bool:
        """Remove the reminder iff it belongs to user_id. Returns True if removed."""
        reminders = self._read_all()
        kept = [
            r
            for r in reminders
            if not (r.get("id") == reminder_id and r.get("user_id") == str(user_id))
        ]
        if len(kept) == len(reminders):
            return False
        self._write_all(kept)
        return True
