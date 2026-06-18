"""Jack's reminder scheduler — the poll loop that fires due reminders.

Runs as its own systemd service (jack-reminders.service), independent of the
gateway. Every poll it asks the store for due reminders, delivers each via the
notifier, and marks fired ones complete. A delivery failure leaves the reminder
pending so the next poll retries it — a single failure never crashes the loop.

Dependencies are injected (store, notifier) so tests run without a real store or
real Discord.
"""

from __future__ import annotations

import os
import signal
import time
from datetime import datetime, timezone
import sys
from pathlib import Path
from typing import Any, Callable, Optional

# Bootstrap: when launched as a plain script (systemd ExecStart runs the file by
# path), the script's own dir — not the package parent — is on sys.path, so
# `import reminders` fails. Add the parent (~/.hermes) so it resolves whether run
# as a script or as `python -m reminders.scheduler`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reminders.notifier import send_reminder  # noqa: E402 - after sys.path bootstrap

_DEFAULT_POLL_SECONDS = 60
_LOG_PATH = Path.home() / ".hermes" / "logs" / "reminders.log"


def _env_poll_seconds() -> int:
    """Poll interval from JACK_REMINDER_POLL_SECONDS, else the default."""
    raw = os.environ.get("JACK_REMINDER_POLL_SECONDS")
    if raw is None or not raw.strip():
        return _DEFAULT_POLL_SECONDS
    try:
        value = int(raw.strip())
    except ValueError:
        return _DEFAULT_POLL_SECONDS
    return value if value > 0 else _DEFAULT_POLL_SECONDS


class ReminderScheduler:
    """Polls a store for due reminders and delivers them via a notifier."""

    def __init__(
        self,
        store: Any,
        notifier: Callable[..., bool] = send_reminder,
        poll_seconds: Optional[int] = None,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._store = store
        self._notifier = notifier
        self._poll_seconds = poll_seconds if poll_seconds is not None else _env_poll_seconds()
        self._logger = logger or self._default_logger
        self._stop = False

    def _default_logger(self, line: str) -> None:
        """Append a line to the reminders log (best-effort; also echo to stdout)."""
        stamp = datetime.now(timezone.utc).isoformat()
        entry = f"{stamp} {line}"
        print(entry)
        try:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(entry + "\n")
        except OSError as exc:  # logging must never crash a fire
            print(f"[reminders.scheduler] log write failed: {type(exc).__name__}")

    def run_once(self, now: Optional[datetime] = None) -> int:
        """Fire all due reminders once. Returns the number successfully fired.

        For each due reminder: deliver via notifier; on success mark it fired and
        log it; on failure (returns False or raises) leave it pending for the next
        poll. A single failure never aborts the batch.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        try:
            due = self._store.get_due(now)
        except Exception as exc:  # noqa: BLE001 - a bad store read shouldn't kill the loop
            self._logger(f"get_due failed: {type(exc).__name__}")
            return 0

        fired = 0
        for reminder in due:
            rid = self._reminder_id(reminder)
            message = self._reminder_message(reminder)
            user_id = self._reminder_user_id(reminder)
            try:
                delivered = self._notifier(message, user_id=user_id)
            except Exception as exc:  # noqa: BLE001 - delivery failure → leave pending
                self._logger(f"notify failed id={rid}: {type(exc).__name__} (left pending)")
                continue

            if not delivered:
                self._logger(f"notify returned False id={rid} (left pending)")
                continue

            try:
                self._store.mark_fired(rid)
            except Exception as exc:  # noqa: BLE001 - couldn't persist → leave pending
                self._logger(f"mark_fired failed id={rid}: {type(exc).__name__} (left pending)")
                continue

            fired += 1
            self._logger(f"fired id={rid}: {message}")

        return fired

    def run_forever(self) -> None:
        """Poll run_once() forever until SIGTERM/SIGINT requests a clean shutdown."""
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)
        self._logger(f"scheduler started (poll={self._poll_seconds}s)")
        while not self._stop:
            self.run_once()
            # Sleep in small slices so a stop request is honoured promptly.
            slept = 0.0
            while not self._stop and slept < self._poll_seconds:
                time.sleep(min(1.0, self._poll_seconds - slept))
                slept += 1.0
        self._logger("scheduler stopped")

    def _request_stop(self, _signum: int, _frame: Any) -> None:
        self._stop = True

    @staticmethod
    def _reminder_id(reminder: Any) -> Any:
        if isinstance(reminder, dict):
            return reminder.get("id")
        return getattr(reminder, "id", None)

    @staticmethod
    def _reminder_message(reminder: Any) -> str:
        if isinstance(reminder, dict):
            return str(reminder.get("message", ""))
        return str(getattr(reminder, "message", ""))

    @staticmethod
    def _reminder_user_id(reminder: Any) -> Optional[str]:
        if isinstance(reminder, dict):
            value = reminder.get("user_id")
        else:
            value = getattr(reminder, "user_id", None)
        return str(value) if value is not None else None


def _load_env(env_path: Path) -> None:
    """Load KEY=VALUE lines from a .env file into os.environ (manual parser).

    Skips blank lines and comments, strips surrounding quotes. Does NOT overwrite
    variables already set in the environment.
    """
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> None:
    """Entry point: load .env, build the store + scheduler, run forever."""
    _load_env(Path.home() / ".hermes" / ".env")

    # Imported here (not at module top) so the module stays importable in tests
    # even before store.py exists.
    from reminders.store import ReminderStore

    store = ReminderStore()
    scheduler = ReminderScheduler(store)
    scheduler.run_forever()


if __name__ == "__main__":
    main()
