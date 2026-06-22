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
        """Run one proactive cycle. Returns number of messages sent (or would-send in shadow).

        Respects JACK_PROACTIVE_ENABLED (default on). Swallows all exceptions
        from the engine so the poll loop never crashes on a bad cycle.

        Dispatches to legacy engine (JACK_PROACTIVE_ENGINE=legacy) or the new
        ProactiveReasoner path (default). In shadow mode (JACK_PROACTIVE_MODE=shadow,
        the default) no Discord messages are ever sent.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        enabled = _truthy(os.environ.get("JACK_PROACTIVE_ENABLED", "1"))
        if not enabled:
            self._logger("proactive disabled — skipping cycle")
            return 0

        engine_mode = os.environ.get("JACK_PROACTIVE_ENGINE", "reasoner")
        if engine_mode == "legacy":
            return self._run_once_legacy(now)
        return self._run_once_reasoner(now)

    def _run_once_legacy(self, now: datetime) -> int:
        """Legacy path: delegate to ProactiveEngine.run_cycle."""
        sent = 0
        try:
            sent = self._engine.run_cycle(self._user_id, now=now)
            self._logger(f"proactive cycle (legacy) sent={sent}")
        except Exception as exc:  # noqa: BLE001 — never crash the poll loop
            self._logger(f"proactive cycle (legacy) error: {type(exc).__name__}: {exc}")

        _drain_memory_queue_if_needed()
        return sent

    def _run_once_reasoner(self, now: datetime) -> int:  # noqa: C901 — complex but sequential
        """New reasoner path: run ProactiveReasoner, score, dedup, shadow-log or send."""
        mode = os.environ.get("JACK_PROACTIVE_MODE", "shadow")

        # Build deps — each individually guarded so partial failure still works.
        try:
            from jack_memory.client import JackMemoryClient  # noqa: PLC0415
            memory_client = JackMemoryClient.from_env()
        except Exception as exc:
            self._logger(f"memory_client init failed: {type(exc).__name__}: {exc}")
            memory_client = None

        try:
            from reminders.store import ReminderStore  # noqa: PLC0415
            store = ReminderStore()
        except Exception as exc:
            self._logger(f"store init failed: {type(exc).__name__}: {exc}")
            store = None

        try:
            from integrations.calendar import CalendarClient  # noqa: PLC0415
            calendar_client = CalendarClient()
        except Exception as exc:
            self._logger(f"calendar_client init failed: {type(exc).__name__}: {exc}")
            calendar_client = None

        try:
            from jack_memory.queue import MemoryQueue  # noqa: PLC0415
            queue = MemoryQueue()
        except Exception as exc:
            self._logger(f"queue init failed: {type(exc).__name__}: {exc}")
            queue = None

        # Resolve engine (for dedup, scoring, quiet hours).
        if self._engine is None:
            try:
                from proactive.engine import ProactiveEngine  # noqa: PLC0415
                self._engine = ProactiveEngine()
            except Exception as exc:
                self._logger(f"engine init failed: {type(exc).__name__}: {exc}")
                return 0

        # Collect already-sent nudge descriptions for today (best-effort).
        already_sent_today: list[str] = []
        try:
            log = self._engine._read_log()
            now_ist_date = now.astimezone(_IST).date()
            for entry in log:
                try:
                    from proactive.engine import _from_z  # noqa: PLC0415
                    sent_at = _from_z(entry["sent_at"]).astimezone(_IST)
                    if sent_at.date() == now_ist_date:
                        already_sent_today.append(
                            f"[{entry.get('priority', '')}] {entry.get('nudge_type', '')}"
                        )
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

        # Gather context on the VPS (has Qdrant/reminders/calendar), then reason on Mac.
        from proactive.reasoner import ProactiveReasoner, gather_context  # noqa: PLC0415
        context_str = ""
        try:
            context_str = gather_context(
                memory_client, store, calendar_client, queue,
                self._user_id, now,
                already_sent=already_sent_today,
            )
        except Exception as exc:
            self._logger(f"gather_context failed: {type(exc).__name__}: {exc}")

        reasoner = ProactiveReasoner()  # no data deps
        # Run reasoning — never raises; returns [] on any failure.
        candidates = reasoner.reason(context_str)
        raw: str | None = getattr(reasoner, "last_raw_response", None)
        mac_reachable = raw is not None

        # Score candidates.
        from proactive.scorer import PriorityScorer  # noqa: PLC0415
        scorer = PriorityScorer()
        # Shadow mode: bypass daily cap so all reasoning decisions are visible
        # (nothing is actually sent, so the cap has no meaning in shadow mode).
        sent_today = 0
        if mode != "shadow":
            try:
                sent_today = self._engine.sent_today_count(now=now)
            except Exception:  # noqa: BLE001
                pass
        selected = scorer.select(candidates, sent_today=sent_today)

        # Dedup: drop nudges sent recently.
        dedup_dropped: list[str] = []
        kept: list = []
        for it in selected:
            try:
                if self._engine.already_sent_recently(it.nudge_type, now=now):
                    dedup_dropped.append(it.nudge_type)
                else:
                    kept.append(it)
            except Exception:  # noqa: BLE001
                kept.append(it)

        # Quiet hours check.
        quiet = False
        try:
            from proactive.engine import ProactiveEngine  # noqa: PLC0415
            quiet = ProactiveEngine.in_quiet_hours(now)
        except Exception:  # noqa: BLE001
            pass
        kept_final = [] if quiet else kept

        now_ist = now.astimezone(_IST)

        if mode == "shadow":
            try:
                from proactive.shadow import log_shadow_cycle  # noqa: PLC0415
                log_shadow_cycle(
                    now_ist,
                    kept_final,
                    raw,
                    mac_reachable=mac_reachable,
                    dedup_dropped=dedup_dropped,
                    quiet_hours=quiet,
                )
            except Exception as exc:
                self._logger(f"shadow log failed: {type(exc).__name__}: {exc}")
            self._logger(f"proactive cycle (reasoner/shadow) would_send={len(kept_final)}")
            _drain_memory_queue_if_needed()
            return len(kept_final)

        else:  # live
            if quiet:
                self._logger("proactive cycle (reasoner/live) quiet hours — skip")
                return 0
            count = 0
            for it in kept_final:
                try:
                    rendered = PriorityScorer.render(it)
                    ok = self._engine._send(rendered, self._user_id)
                    if ok:
                        self._engine.log_sent(it, now=now)
                        count += 1
                except Exception as exc:
                    self._logger(f"send failed: {type(exc).__name__}: {exc}")
            self._logger(f"proactive cycle (reasoner/live) sent={count}")
            _drain_memory_queue_if_needed()
            return count

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


def _drain_memory_queue_if_needed() -> None:
    """Drain the memory extraction queue if backend=mem0 and Mac is reachable.

    Called once per proactive poll cycle. Never raises — any failure is silent.
    On flatfile backend this is a fast early-return with no imports.
    """
    try:
        from jack_memory.backend import memory_backend
        if memory_backend() != "mem0":
            return
        from jack_memory.client import JackMemoryClient
        from jack_memory.queue import MemoryQueue
        client = JackMemoryClient.from_env()
        h = client.health()
        if not h.get("extractor"):
            return  # Mac still down; leave items queued
        queue = MemoryQueue()
        if len(queue) == 0:
            return
        drained = queue.drain(lambda item: client.add(
            item.get("messages", []),
            user_id=item.get("user_id", "arnav"),
            metadata=item.get("metadata"),
        ))
        if drained:
            print(f"[memory_queue] drained {drained} item(s)", flush=True)
    except Exception:  # noqa: BLE001 — must never crash the poll loop
        pass


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
