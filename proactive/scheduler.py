"""Jack Proactive Scheduler — polls ProactiveEngine every JACK_PROACTIVE_POLL_SECONDS.

Mirrors the structure of briefing/morning.py and reminders/scheduler.py:
  - sys.path bootstrap at top (so it can run as a script from /home/hermes/.hermes/)
  - _load_env() from ~/.hermes/.env
  - _truthy() helper
  - class ProactiveScheduler with DI (engine, poll_seconds, logger, user_id)
  - SIGTERM/SIGINT → self._stop flag → clean exit
  - Sliced sleep (60s slices) so stop is honoured within ~1 min
  - main() entry point
"""

from __future__ import annotations

import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

# Bootstrap: when launched as a plain script (systemd ExecStart runs the file by
# path), the script's own dir — not the package parent — is on sys.path. Add the
# package parent (~/.hermes, for `proactive`/`reminders`) AND the worker bundle
# (~/.hermes/jack_worker, where `lib`/`lib.llm` lives on the VPS).
# Locally `lib` is at the repo root, which parents[1] already covers.
_BOOT_ROOT = Path(__file__).resolve().parents[1]
for _bp in (str(_BOOT_ROOT), str(_BOOT_ROOT / "jack_worker")):
    if _bp not in sys.path:
        sys.path.insert(0, _bp)

# IST is UTC+5:30, fixed offset (no DST) — safe as a static timezone.
_IST = timezone(timedelta(hours=5, minutes=30))

_DEFAULT_POLL_SECONDS = 900  # 15 min — engine enforces quiet hours/dedup/caps
_LOG_PATH = Path.home() / ".hermes" / "logs" / "proactive.log"
_SLEEP_SLICE_S = 60.0


def _truthy(value: str | None) -> bool:
    """Loose truthiness for env flags (default-on handled by caller)."""
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _env_poll_seconds() -> int:
    """Poll interval from JACK_PROACTIVE_POLL_SECONDS, else the default."""
    raw = os.environ.get("JACK_PROACTIVE_POLL_SECONDS")
    if raw is None or not raw.strip():
        return _DEFAULT_POLL_SECONDS
    try:
        value = int(raw.strip())
    except ValueError:
        return _DEFAULT_POLL_SECONDS
    return value if value > 0 else _DEFAULT_POLL_SECONDS


class ProactiveScheduler:
    """Polls ProactiveEngine on a fixed interval; all logic is in the engine."""

    def __init__(
        self,
        engine: Any = None,
        poll_seconds: int | None = None,
        logger: Callable[[str], None] | None = None,
        user_id: str = "",
    ) -> None:
        self._engine = engine  # None → built lazily in run_forever
        self._poll_seconds = (
            poll_seconds if poll_seconds is not None else _env_poll_seconds()
        )
        self._logger = logger if logger is not None else self._default_logger
        self._user_id = user_id
        self._stop = False

    # -- core cycle -------------------------------------------------------

    def run_once(self, now: datetime | None = None) -> int:
        """Run one proactive cycle. Returns number of messages sent.

        Respects JACK_PROACTIVE_ENABLED (default on). Swallows all exceptions
        from the engine so the poll loop never crashes on a bad cycle.
        """
        enabled = _truthy(os.environ.get("JACK_PROACTIVE_ENABLED", "1"))
        if not enabled:
            self._logger("proactive disabled — skipping cycle")
            return 0
        try:
            sent = self._engine.run_cycle(self._user_id, now=now)
            self._logger(f"proactive cycle sent={sent}")
            return sent
        except Exception as exc:  # noqa: BLE001 — never crash the poll loop
            self._logger(f"proactive cycle error: {type(exc).__name__}: {exc}")
            return 0

    # -- run loop ---------------------------------------------------------

    def run_forever(self) -> None:
        """Poll run_once() forever until SIGTERM/SIGINT requests a clean shutdown."""
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)

        # Resolve user_id from notifier default if not supplied.
        if not self._user_id:
            try:
                from reminders.notifier import _default_user_id

                self._user_id = _default_user_id() or ""
            except Exception:  # noqa: BLE001
                pass

        # Build engine lazily so tests can inject a fake without real imports.
        if self._engine is None:
            from proactive.engine import ProactiveEngine

            self._engine = ProactiveEngine()

        self._logger(f"proactive scheduler started (poll={self._poll_seconds}s)")
        while not self._stop:
            self.run_once()
            # Sliced sleep: honour stop requests within ~_SLEEP_SLICE_S seconds.
            slept = 0.0
            while not self._stop and slept < self._poll_seconds:
                slice_s = min(_SLEEP_SLICE_S, self._poll_seconds - slept)
                time.sleep(slice_s)
                slept += slice_s
        self._logger("proactive scheduler stopped")

    def _request_stop(self, signum: int, frame: Any) -> None:
        self._stop = True

    # -- logging ----------------------------------------------------------

    def _default_logger(self, line: str) -> None:
        """Stamp with UTC ISO, print to stdout, append to _LOG_PATH (best-effort)."""
        stamp = datetime.now(timezone.utc).isoformat()
        entry = f"{stamp} {line}"
        print(entry)
        try:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(entry + "\n")
        except OSError as exc:  # logging must never crash the scheduler
            print(f"[proactive.scheduler] log write failed: {type(exc).__name__}")


# -- helpers --------------------------------------------------------------


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
    """Entry point: load .env, build the scheduler, run forever."""
    _load_env(Path.home() / ".hermes" / ".env")
    ProactiveScheduler().run_forever()


if __name__ == "__main__":
    main()
