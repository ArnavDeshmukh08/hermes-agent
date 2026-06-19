"""Jack's morning briefing — a casual daily check-in posted to Discord.

Runs as its own systemd service (jack-briefing.service). Once a day at
JACK_BRIEFING_TIME_IST (default 09:00 Asia/Kolkata) it reads Arnav's USER.md
(priorities / routine / relationships) and the reminders due today, asks the LLM
to write a short friend-like message, and posts it via reminders.notifier.

Everything is dependency-injected (store, complete_fn, send_fn) so tests run
with no network and a deterministic clock. The send path swallows errors and
never raises, mirroring the reminder scheduler: a bad day never crashes the loop.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# Bootstrap: when launched as a plain script (systemd ExecStart runs the file by
# path), the script's own dir — not the package parent — is on sys.path. Add the
# package parent (~/.hermes, for `reminders`/`briefing`) AND the worker bundle
# (~/.hermes/jack_worker, where `lib`/`lib.llm` lives on the VPS — without it,
# compile_briefing's `from lib import llm` ModuleNotFErrors at fire time).
# Locally `lib` is at the repo root, which parents[1] already covers.
_BOOT_ROOT = Path(__file__).resolve().parents[1]
for _bp in (str(_BOOT_ROOT), str(_BOOT_ROOT / "jack_worker")):
    if _bp not in sys.path:
        sys.path.insert(0, _bp)

# IST is UTC+5:30, fixed offset (no DST) — safe as a static timezone.
_IST = timezone(timedelta(hours=5, minutes=30))
_DEFAULT_BRIEFING_TIME = "09:00"
_MAX_LINES = 5
_SLEEP_SLICE_S = 60.0
_BULLET_PREFIXES = ("- ", "* ", "• ")

_DEFAULT_USER_PATH = Path.home() / ".hermes" / "USER.md"
_DEFAULT_LOG_PATH = Path.home() / ".hermes" / "logs" / "briefing.log"

_SYSTEM_PROMPT = (
    "You are Jack, Arnav's personal assistant and friend. Write his morning "
    "check-in. Be casual and warm, like a friend texting — not a corporate "
    "briefing. Weave in today's reminders, his current priorities, and one "
    "relevant touch about his life. Keep it SHORT: at most 5 lines. Do NOT use "
    "bullet points or lists — talk like a real person, in plain sentences."
)


def _truthy(value: str | None) -> bool:
    """Loose truthiness for env flags (default-on handled by caller)."""
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class MorningBriefing:
    """Compiles and sends Arnav's casual daily morning briefing."""

    def __init__(
        self,
        user_path: str | Path | None = None,
        store: Any = None,
        complete_fn: Optional[Callable[..., str]] = None,
        send_fn: Optional[Callable[..., bool]] = None,
        log_path: str | Path | None = None,
    ) -> None:
        env_user = os.environ.get("JACK_USER_PATH")
        if user_path is not None:
            self._user_path = Path(user_path).expanduser()
        elif env_user:
            self._user_path = Path(env_user).expanduser()
        else:
            self._user_path = _DEFAULT_USER_PATH

        self._store = store  # lazily built in _get_store() if None
        self._complete_fn = complete_fn  # lazily resolved in _complete()
        self._send_fn = send_fn  # lazily resolved in _send()
        self._log_path = (
            Path(log_path).expanduser() if log_path is not None else _DEFAULT_LOG_PATH
        )
        self._stop = False

    # -- lazy dependency wiring ------------------------------------------
    def _get_store(self) -> Any:
        if self._store is None:
            from reminders.store import ReminderStore

            self._store = ReminderStore()
        return self._store

    def _complete(self, system: str, user: str) -> str:
        fn = self._complete_fn
        if fn is None:
            from lib.llm import complete as fn  # type: ignore[no-redef]
        return fn(system, user, prefer="groq", max_tokens=200)

    def _send(self, content: str, user_id: str | None) -> bool:
        fn = self._send_fn
        if fn is None:
            from reminders.notifier import send_message as fn  # type: ignore[no-redef]
        return fn(content, user_id=user_id)

    # -- data gathering --------------------------------------------------
    def _read_user_md(self) -> str:
        """Read USER.md fresh each call; empty string if missing/unreadable."""
        try:
            return self._user_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def todays_reminders(self, user_id: str, now: Optional[datetime] = None) -> list[dict]:
        """Pending reminders whose fire_at falls on TODAY in IST.

        fire_at is stored UTC ('Z'); we compare calendar dates in IST so a
        reminder at 23:00 IST counts as today, not tomorrow-UTC.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        today_ist = now.astimezone(_IST).date()

        out: list[dict] = []
        for r in self._get_store().list_pending(user_id):
            fire_at = r.get("fire_at")
            if not fire_at:
                continue
            try:
                fire_dt = datetime.fromisoformat(str(fire_at).replace("Z", "+00:00"))
            except ValueError:
                continue
            if fire_dt.tzinfo is None:
                fire_dt = fire_dt.replace(tzinfo=timezone.utc)
            if fire_dt.astimezone(_IST).date() == today_ist:
                out.append(r)
        return out

    # -- briefing text ---------------------------------------------------
    def compile_briefing(self, user_id: str, now: Optional[datetime] = None) -> str:
        """Build the briefing string from USER.md + today's reminders via the LLM."""
        user_md = self._read_user_md()
        reminders = self.todays_reminders(user_id, now=now)
        if reminders:
            lines = [f"- {r.get('message', '')}" for r in reminders]
            reminder_block = "\n".join(lines)
        else:
            reminder_block = "(none today)"

        user_prompt = (
            "Here is what you know about Arnav (USER.md):\n"
            f"{user_md}\n\n"
            "Today's reminders:\n"
            f"{reminder_block}\n\n"
            "Write his morning check-in now."
        )

        text = self._complete(_SYSTEM_PROMPT, user_prompt)
        return self._postprocess(text)

    @staticmethod
    def _postprocess(text: str) -> str:
        """Enforce <=5 non-empty lines and strip any leading bullet chars."""
        cleaned: list[str] = []
        for raw in (text or "").splitlines():
            line = raw.rstrip()
            if not line.strip():
                continue
            stripped = line.lstrip()
            for prefix in _BULLET_PREFIXES:
                if stripped.startswith(prefix):
                    stripped = stripped[len(prefix):].lstrip()
                    break
            cleaned.append(stripped)
            if len(cleaned) >= _MAX_LINES:
                break
        return "\n".join(cleaned)

    # -- delivery --------------------------------------------------------
    def send_briefing(self, user_id: str, now: Optional[datetime] = None) -> bool:
        """Compile, send, and log the briefing. Swallows errors; never raises."""
        try:
            text = self.compile_briefing(user_id, now=now)
            ok = self._send(text, user_id)
            self._log(f"briefing sent={ok} user={user_id}")
            return bool(ok)
        except Exception as exc:  # noqa: BLE001 - a bad day must not crash the loop
            self._log(f"briefing failed: {type(exc).__name__}")
            return False

    def _log(self, line: str) -> None:
        """Append a line to briefing.log (best-effort; also echo to stdout)."""
        stamp = datetime.now(timezone.utc).isoformat()
        entry = f"{stamp} {line}"
        print(entry)
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(entry + "\n")
        except OSError as exc:  # logging must never crash a send
            print(f"[briefing.morning] log write failed: {type(exc).__name__}")

    # -- scheduling ------------------------------------------------------
    def next_fire(self, now: Optional[datetime] = None) -> datetime:
        """Next occurrence of JACK_BRIEFING_TIME_IST in IST, returned as UTC.

        If today's time has already passed (or equals now), rolls to tomorrow.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        hour, minute = self._briefing_time()
        now_ist = now.astimezone(_IST)
        target = now_ist.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if target <= now_ist:
            target = target + timedelta(days=1)
        return target.astimezone(timezone.utc)

    @staticmethod
    def _briefing_time() -> tuple[int, int]:
        """Parse JACK_BRIEFING_TIME_IST 'HH:MM' (default 09:00)."""
        raw = os.environ.get("JACK_BRIEFING_TIME_IST", _DEFAULT_BRIEFING_TIME)
        try:
            parsed = dtime.fromisoformat(raw.strip())
            return parsed.hour, parsed.minute
        except (ValueError, AttributeError):
            return 9, 0

    def run_forever(self, user_id: str = "") -> None:
        """Sleep until next_fire, send the briefing, repeat until SIGTERM/SIGINT.

        Respects JACK_BRIEFING_ENABLED (default on). When disabled, idles without
        sending so the service can stay installed but dormant.
        """
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)

        enabled = _truthy(os.environ.get("JACK_BRIEFING_ENABLED", "1"))
        if not user_id:
            from reminders.notifier import _default_user_id

            user_id = _default_user_id() or ""

        self._log(f"briefing scheduler started (enabled={enabled})")
        while not self._stop:
            target = self.next_fire()
            while not self._stop:
                remaining = (target - datetime.now(timezone.utc)).total_seconds()
                if remaining <= 0:
                    break
                time.sleep(min(_SLEEP_SLICE_S, remaining))
            if self._stop:
                break
            if enabled:
                self.send_briefing(user_id)
            else:
                self._log("briefing disabled — skipping send")
        self._log("briefing scheduler stopped")

    def _request_stop(self, _signum: int, _frame: Any) -> None:
        self._stop = True


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
    """Entry point: load .env, build the briefing, run forever."""
    _load_env(Path.home() / ".hermes" / ".env")
    briefing = MorningBriefing()
    briefing.run_forever()


if __name__ == "__main__":
    main()
