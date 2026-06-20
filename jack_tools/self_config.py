"""Jack self-config engine.

Allows reading and writing JACK_* settings in ~/.hermes/.env without
exposing arbitrary file paths or permitting command injection into
systemctl. Every write is atomic (temp-file + os.replace), mode-0600,
and logged to ~/.hermes/logs/self_config.log.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Settle wait — tests monkeypatch this to 0 so they finish instantly.
# ---------------------------------------------------------------------------
_SETTLE_SECONDS: float = 3.0

# ---------------------------------------------------------------------------
# IST timezone
# ---------------------------------------------------------------------------
_IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------
ENV_PATH = Path.home() / ".hermes" / ".env"
LOG_PATH = Path.home() / ".hermes" / "logs" / "self_config.log"

# ---------------------------------------------------------------------------
# Allowed keys — single source of truth
# ---------------------------------------------------------------------------
ALLOWED_KEYS: dict[str, dict[str, Any]] = {
    "JACK_BRIEFING_TIME_IST": {
        "type": "time",
        "format": "HH:MM (24h)",
        "service": "jack-briefing.service",
        "description": "Time (IST) at which the morning briefing fires",
    },
    "JACK_BRIEFING_ENABLED": {
        "type": "bool",
        "service": "jack-briefing.service",
        "description": "Enable / disable the morning briefing",
    },
    "JACK_WEATHER_ENABLED": {
        "type": "bool",
        "service": "jack-briefing.service",
        "description": "Include weather in the morning briefing",
    },
    "JACK_NEWS_ENABLED": {
        "type": "bool",
        "service": "jack-briefing.service",
        "description": "Include news headlines in the morning briefing",
    },
    "JACK_REMINDER_POLL_SECONDS": {
        "type": "int",
        "min": 30,
        "max": 300,
        "service": "jack-reminders.service",
        "description": "How often (seconds) the reminder poller checks for due reminders",
    },
    "JACK_MEMORY_ENABLED": {
        "type": "bool",
        "service": "hermes-gateway.service",
        "description": "Enable / disable the long-term memory layer",
    },
}

# Friendly name -> JACK key.  Module-level so other modules can import it.
FRIENDLY_TO_KEY: dict[str, str] = {
    "briefing_time": "JACK_BRIEFING_TIME_IST",
    "briefing_enabled": "JACK_BRIEFING_ENABLED",
    "weather_enabled": "JACK_WEATHER_ENABLED",
    "news_enabled": "JACK_NEWS_ENABLED",
    "memory_enabled": "JACK_MEMORY_ENABLED",
    "reminder_poll_seconds": "JACK_REMINDER_POLL_SECONDS",
}

# Reverse mapping used by list_configurable()
_KEY_TO_FRIENDLY: dict[str, str] = {v: k for k, v in FRIENDLY_TO_KEY.items()}

# All service names that are valid systemctl targets (prevent injection)
_KNOWN_SERVICES: frozenset[str] = frozenset(m["service"] for m in ALLOWED_KEYS.values())

# Bool-accepting strings
_BOOL_TRUE = {"true", "1", "yes", "on"}
_BOOL_FALSE = {"false", "0", "no", "off"}

# Time pattern: 8:00  08:00  23:59
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_env(path: Path) -> list[str]:
    """Return the raw lines of *path*, or [] if it does not exist."""
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except FileNotFoundError:
        return []


def _extract_value(lines: list[str], key: str) -> str | None:
    """Return the value for *key* from parsed .env lines, or None."""
    prefix = f"{key}="
    for line in lines:
        stripped = line.rstrip("\n")
        if stripped.startswith(prefix):
            return stripped[len(prefix):]
    return None


def _upsert_lines(lines: list[str], key: str, value: str) -> list[str]:
    """Return a new list where KEY=value is updated in-place or appended."""
    prefix = f"{key}="
    updated = False
    result: list[str] = []
    for line in lines:
        if line.rstrip("\n").startswith(prefix):
            result.append(f"{key}={value}\n")
            updated = True
        else:
            result.append(line)
    if not updated:
        # Ensure the file ends with a newline before appending
        if result and not result[-1].endswith("\n"):
            result[-1] += "\n"
        result.append(f"{key}={value}\n")
    return result


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class JackSelfConfig:
    """Read / write JACK_* settings in ~/.hermes/.env safely and atomically."""

    def __init__(
        self,
        env_path: Path | None = None,
        log_path: Path | None = None,
        restart_fn: Callable[[str], bool] | None = None,
    ) -> None:
        self._env_path = env_path if env_path is not None else ENV_PATH
        self._log_path = log_path if log_path is not None else LOG_PATH
        self._restart_fn = restart_fn if restart_fn is not None else self.restart_service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> str | None:
        """Return the current value of *key* from .env, or None if absent/unknown."""
        if key not in ALLOWED_KEYS:
            return None
        try:
            lines = _parse_env(self._env_path)
            return _extract_value(lines, key)
        except Exception:  # noqa: BLE001
            return None

    def set(self, key: str, value: str) -> dict[str, Any]:  # noqa: A003
        """Validate, write, and restart the owning service for *key*."""
        # --- security: only known keys ---
        if key not in ALLOWED_KEYS:
            known = ", ".join(sorted(ALLOWED_KEYS))
            return {"success": False, "message": f"Unknown setting '{key}'. Configurable: {known}"}

        meta = ALLOWED_KEYS[key]

        # --- type validation + normalisation ---
        normalised, err = self._validate(key, value, meta)
        if err:
            return {"success": False, "message": err}

        old = self.get(key)

        # --- atomic write ---
        try:
            self._write_env(key, normalised)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "message": f"Failed to update {key}: {exc}"}

        # --- audit log ---
        try:
            self._append_log(key, normalised, old)
        except Exception:  # noqa: BLE001
            pass  # log failure should not block the caller

        # --- restart owning service ---
        service = meta["service"]
        ok = self._restart_fn(service)
        if not ok:
            return {
                "success": False,
                "message": (
                    f"Updated {key} in .env but the {service} restart could not be confirmed. "
                    "It may need a manual restart."
                ),
            }

        return {
            "success": True,
            "message": f"Updated {key} to {normalised}. {service} restarted and confirmed running.",
        }

    def restart_service(self, service_name: str) -> bool:
        """Restart *service_name* via systemctl --user and verify it is active.

        Returns False (never raises) on macOS / any error.
        """
        # Guard against injection: only fixed service names are accepted.
        if service_name not in _KNOWN_SERVICES:
            logger.warning("restart_service: unknown service %r — rejecting", service_name)
            return False

        try:
            subprocess.run(
                ["systemctl", "--user", "restart", service_name],
                check=True,
            )
        except FileNotFoundError:
            logger.info("restart_service: systemctl not found (macOS/local dev) — skipping")
            return False
        except subprocess.CalledProcessError as exc:
            logger.warning("restart_service: restart failed for %s: %s", service_name, exc)
            return False

        # Brief settle so systemd has time to mark the unit active.
        time.sleep(_SETTLE_SECONDS)

        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", service_name],
                capture_output=True,
                text=True,
            )
            return result.stdout.strip() == "active"
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False

    def list_configurable(self) -> list[dict[str, Any]]:
        """Return one entry per ALLOWED_KEY with current value and metadata."""
        out = []
        for key, meta in ALLOWED_KEYS.items():
            out.append(
                {
                    "key": key,
                    "friendly": _KEY_TO_FRIENDLY.get(key, ""),
                    "value": self.get(key),
                    "type": meta["type"],
                    "description": meta["description"],
                }
            )
        return out

    def get_status(self) -> dict[str, str]:
        """Return {service_name: "active"|"inactive"|"unknown"} for each service."""
        services = {m["service"] for m in ALLOWED_KEYS.values()}
        status: dict[str, str] = {}
        for svc in services:
            try:
                result = subprocess.run(
                    ["systemctl", "--user", "is-active", svc],
                    capture_output=True,
                    text=True,
                )
                raw = result.stdout.strip()
                status[svc] = "active" if raw == "active" else "inactive"
            except FileNotFoundError:
                status[svc] = "unknown"
            except subprocess.CalledProcessError:
                status[svc] = "unknown"
        return status

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _validate(self, key: str, value: str, meta: dict[str, Any]) -> tuple[str | None, str | None]:
        """Return (normalised_value, error_message).  One of them is None."""
        kind = meta["type"]

        if kind == "time":
            m = _TIME_RE.match(value.strip())
            if not m:
                return None, (
                    f"'{value}' is not a valid time for {key}. "
                    "Use HH:MM in 24-hour format, e.g. '09:00' or '14:30'."
                )
            hh = int(m.group(1))
            mm = int(m.group(2))
            return f"{hh:02d}:{mm:02d}", None

        if kind == "bool":
            lower = value.strip().lower()
            if lower in _BOOL_TRUE:
                return "1", None
            if lower in _BOOL_FALSE:
                return "0", None
            return None, (
                f"'{value}' is not a valid boolean for {key}. "
                "Use true/false, 1/0, yes/no, or on/off."
            )

        if kind == "int":
            try:
                int_val = int(value.strip())
            except ValueError:
                return None, f"'{value}' is not an integer for {key}."
            lo = meta.get("min")
            hi = meta.get("max")
            if lo is not None and int_val < lo:
                return None, f"{key} must be at least {lo} (got {int_val})."
            if hi is not None and int_val > hi:
                return None, f"{key} must be at most {hi} (got {int_val})."
            return str(int_val), None

        # Unknown type — should never happen if ALLOWED_KEYS is correct.
        return value, None

    def _write_env(self, key: str, value: str) -> None:
        """Atomically update KEY=value in self._env_path (mode 0600)."""
        env_path = self._env_path
        env_dir = env_path.parent
        env_dir.mkdir(parents=True, exist_ok=True)

        lines = _parse_env(env_path)
        new_lines = _upsert_lines(lines, key, value)
        content = "".join(new_lines)

        # Write to a temp file in the SAME directory so os.replace is atomic.
        fd, tmp_name = tempfile.mkstemp(dir=env_dir, prefix=".env.tmp.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
        except Exception:
            # Clean up the temp file on error before re-raising.
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

        os.replace(tmp_name, env_path)
        os.chmod(env_path, 0o600)

    def _append_log(self, key: str, value: str, old: str | None) -> None:
        """Append a single audit line to self._log_path."""
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        now_ist = datetime.now(_IST).isoformat()
        line = f"[{now_ist}] SET {key}={value} (was {old})\n"
        with self._log_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
