"""Garmin hourly health snapshot poller for Jack.

Polls the Mac-side Garmin service once per hour, builds a JSON snapshot,
and writes it atomically to ~/.hermes/health_today.json using fcntl.flock
to guard concurrent writers.

Usage (one-shot, called by systemd timer):
    python3 ~/.hermes/integrations/garmin_poller.py --once

Design rules:
- Store timestamps in UTC; display / date boundaries use IST (UTC+5:30).
- Never fabricate data; if Mac is unreachable, write is_stale=True.
- Keep a rolling 24-entry hourly_steps array (one entry per poll).
- Reset hourly_steps at midnight IST (new date_ist vs previous snapshot).
- Use fcntl.flock on a sidecar .lock file for all file writes.
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# Ensure ~/.hermes is on sys.path so `integrations.garmin` resolves whether
# the script is run directly (systemd timer) or imported in tests.
_HERMES_ROOT = Path(__file__).resolve().parent.parent
if str(_HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(_HERMES_ROOT))

from integrations.garmin import GarminClient  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
_UTC = datetime.timezone.utc
_MAX_HOURLY = 24  # rolling window size

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [garmin_poller] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _default_snapshot_path() -> Path:
    """Return the default path for health_today.json on the VPS."""
    return Path.home() / ".hermes" / "health_today.json"


def _lock_path(snapshot_path: Path) -> Path:
    return snapshot_path.with_suffix(".lock")


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(tz=_UTC)


def _today_ist(now: datetime.datetime | None = None) -> str:
    """Return today's date in IST as YYYY-MM-DD."""
    ref = now or _now_utc()
    return ref.astimezone(_IST).date().isoformat()


# ---------------------------------------------------------------------------
# Snapshot builder
# ---------------------------------------------------------------------------

def _build_snapshot(
    summary: dict | None,
    sleep: dict | None,
    prev: dict | None,
    now: datetime.datetime,
    runs: list[dict] | None = None,
) -> dict:
    """Construct a fresh snapshot dict.

    Args:
        summary: result of GarminClient().get_daily_summary(), or None.
        sleep:   result of GarminClient().last_night_sleep(), or None.
        prev:    the previously persisted snapshot dict (may be None).
        now:     the UTC datetime of this poll.
        runs:    result of GarminClient().get_recent_runs(days=1), or None.
    """
    date_ist = _today_ist(now)
    is_stale = summary is None and sleep is None

    # --- stats from daily summary -----------------------------------------
    stats: dict | None = None
    if summary and isinstance(summary.get("stats"), dict):
        stats = summary["stats"]
    elif summary and isinstance(summary.get("steps"), int):
        # some versions return flat structure
        stats = {k: summary[k] for k in ("steps", "body_battery_high", "calories", "stress_avg") if k in summary}

    # --- sleep from dedicated call (takes precedence over summary.sleep) ---
    sleep_data: dict | None = sleep
    if sleep_data is None and summary and isinstance(summary.get("sleep"), dict):
        sleep_data = summary["sleep"]

    # --- hourly_steps: rolling 24h, reset on new IST day ------------------
    steps_now: int | None = stats.get("steps") if stats else None

    prev_date = prev.get("date_ist") if prev else None
    prev_hourly: list[dict] = (
        prev.get("hourly_steps", [])
        if (prev and prev_date == date_ist)
        else []
    )

    if steps_now is not None:
        new_entry: dict[str, Any] = {
            "timestamp_utc": now.isoformat(),
            "steps_cumulative": steps_now,
        }
        hourly = (prev_hourly + [new_entry])[-_MAX_HOURLY:]
    else:
        hourly = prev_hourly  # keep whatever we had; don't append None

    return {
        "fetched_at_utc": now.isoformat(),
        "date_ist": date_ist,
        "is_stale": is_stale,
        "stats": stats,
        "sleep": sleep_data,
        "runs_today": runs if runs is not None else (prev.get("runs_today") if prev else None),
        "runs_available": runs is not None,
        "hourly_steps": hourly,
    }


# ---------------------------------------------------------------------------
# Atomic flock-guarded write
# ---------------------------------------------------------------------------

def _write_snapshot(snapshot: dict, snapshot_path: Path) -> None:
    """Write snapshot JSON atomically with fcntl.flock protection.

    Uses a sidecar .lock file so we can hold an exclusive lock while we
    do the read-modify-write cycle without locking the snapshot itself
    (which would block readers on Linux as well).
    """
    lock_file = _lock_path(snapshot_path)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_file, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            # Atomic write: write to temp then rename so readers never see partial
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=snapshot_path.parent,
                prefix=".health_today_",
                suffix=".tmp",
            )
            try:
                with os.fdopen(tmp_fd, "w") as f:
                    json.dump(snapshot, f, indent=2, default=str)
                os.replace(tmp_path, snapshot_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _read_snapshot(snapshot_path: Path) -> dict | None:
    """Read current snapshot if it exists, else None. Thread-safe read."""
    if not snapshot_path.exists():
        return None
    try:
        return json.loads(snapshot_path.read_text())
    except Exception:
        logger.warning("garmin_poller: could not parse existing snapshot", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Health history (14-day rolling log)
# ---------------------------------------------------------------------------

def _history_path() -> Path:
    return Path.home() / ".hermes" / "health_history.json"


def _write_history_entry(snapshot: dict) -> None:
    """Append/update today's entry in health_history.json. Flock-guarded."""
    hist_path = _history_path()
    lock_file = hist_path.parent / "health_history.lock"
    hist_path.parent.mkdir(parents=True, exist_ok=True)

    date_ist = snapshot.get("date_ist", _today_ist())
    steps = (snapshot.get("stats") or {}).get("steps") or 0
    sleep_h = (snapshot.get("sleep") or {}).get("total_sleep_h")

    runs_today = snapshot.get("runs_today")
    runs_available = snapshot.get("runs_available", False)
    if runs_available:
        entries = [
            {
                "km": r.get("distance_km", 0),
                "pace": str(r.get("avg_pace_min_km", "")),
            }
            for r in (runs_today or [])
        ]
        runs_entry: dict | None = {
            "count": len(entries),
            "total_km": round(sum(e["km"] for e in entries), 2),
            "entries": entries,
        }
    else:
        runs_entry = None  # Mac was unreachable — don't write fake zeros

    sedentary = sedentary_streak_hours(snapshot) >= 2

    today_entry: dict[str, Any] = {
        "date_ist": date_ist,
        "steps": steps,
        "sleep_h": sleep_h,
        "runs": runs_entry,
        "sedentary": sedentary,
    }

    with open(lock_file, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            # Load existing history
            history: list[dict] = []
            if hist_path.exists():
                try:
                    history = json.loads(hist_path.read_text(encoding="utf-8"))
                    if not isinstance(history, list):
                        history = []
                except Exception:
                    history = []

            # Replace today's entry if it exists, else append
            history = [e for e in history if e.get("date_ist") != date_ist]
            history.append(today_entry)

            # Sort by date, keep last 14
            history.sort(key=lambda e: e.get("date_ist", ""))
            history = history[-14:]

            # Atomic write
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=hist_path.parent,
                prefix=".health_history_",
                suffix=".tmp",
            )
            try:
                with os.fdopen(tmp_fd, "w") as f:
                    json.dump(history, f, indent=2, default=str)
                os.replace(tmp_path, hist_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)

    logger.info(
        "garmin_poller: history entry written for %s (runs=%s)",
        date_ist,
        runs_entry,
    )


# ---------------------------------------------------------------------------
# Main poll function
# ---------------------------------------------------------------------------

def poll_once(snapshot_path: Path | None = None) -> dict:
    """Run one poll cycle: fetch Garmin data, update snapshot file.

    Returns the snapshot dict written (useful for testing).
    """
    if snapshot_path is None:
        snapshot_path = _default_snapshot_path()

    now = _now_utc()
    client = GarminClient()

    logger.info("garmin_poller: polling at %s UTC (IST date: %s)", now.isoformat(), _today_ist(now))

    # --- Fetch from Mac service -------------------------------------------
    try:
        summary = client.get_daily_summary()
    except Exception:
        logger.error("garmin_poller: get_daily_summary raised unexpectedly", exc_info=True)
        summary = None

    try:
        sleep = client.last_night_sleep()
    except Exception:
        logger.error("garmin_poller: last_night_sleep raised unexpectedly", exc_info=True)
        sleep = None

    try:
        runs = client.get_recent_runs(days=1)
    except Exception:
        logger.error("garmin_poller: get_recent_runs raised unexpectedly", exc_info=True)
        runs = None

    if summary is None and sleep is None:
        logger.warning(
            "garmin_poller: Mac service unreachable — writing stale snapshot"
        )

    # --- Build and persist snapshot ---------------------------------------
    prev = _read_snapshot(snapshot_path)
    snapshot = _build_snapshot(summary, sleep, prev, now, runs=runs)
    _write_snapshot(snapshot, snapshot_path)

    logger.info(
        "garmin_poller: snapshot written — is_stale=%s steps=%s runs=%s",
        snapshot["is_stale"],
        (snapshot.get("stats") or {}).get("steps"),
        len(snapshot.get("runs_today") or []),
    )

    _write_history_entry(snapshot)

    return snapshot


# ---------------------------------------------------------------------------
# Derived-metric helpers (importable by callers)
# ---------------------------------------------------------------------------

def sedentary_streak_hours(snapshot: dict) -> int:
    """Return consecutive trailing hours with step-delta < 500.

    Computes from hourly_steps cumulative values in the snapshot dict.
    Returns 0 if fewer than 2 data points exist.
    """
    hourly: list[dict] = snapshot.get("hourly_steps") or []
    if len(hourly) < 2:
        return 0

    streak = 0
    for i in range(len(hourly) - 1, 0, -1):
        delta = hourly[i]["steps_cumulative"] - hourly[i - 1]["steps_cumulative"]
        if delta < 500:
            streak += 1
        else:
            break
    return streak


def steps_trend(snapshot: dict) -> str:
    """Return 'increasing', 'flat', or 'unknown' based on hourly_steps.

    Uses the last 3 data points to decide trend:
    - 'increasing' if the final delta > the initial delta by at least 100
    - 'flat' otherwise
    """
    hourly: list[dict] = snapshot.get("hourly_steps") or []
    if len(hourly) < 3:
        return "unknown"

    d1 = hourly[-2]["steps_cumulative"] - hourly[-3]["steps_cumulative"]
    d2 = hourly[-1]["steps_cumulative"] - hourly[-2]["steps_cumulative"]
    return "increasing" if d2 - d1 >= 100 else "flat"


def days_since_last_run(history: list[dict]) -> int | None:
    """Return number of days since the last logged run, or None if no run found.

    Scans history newest-first. Returns 0 if today has a run logged.
    Returns None if no run entry exists in the history.
    """
    if not history:
        return None
    # Sort descending by date
    sorted_history = sorted(history, key=lambda e: e.get("date_ist", ""), reverse=True)

    for i, entry in enumerate(sorted_history):
        runs = entry.get("runs")
        if runs is None:
            continue  # stale/unreachable day — skip, don't count as rest day
        if isinstance(runs, dict) and runs.get("count", 0) > 0:
            return i  # 0 = today, 1 = yesterday, etc.
    return None  # no run found in history


def runs_this_week(history: list[dict]) -> int:
    """Return count of days with at least one run in the last 7 days.

    A day with runs=null (stale) is not counted as a rest day or run day.
    """
    if not history:
        return 0
    cutoff = (
        datetime.datetime.now(
            tz=datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        ).date()
        - datetime.timedelta(days=6)
    ).isoformat()
    count = 0
    for entry in history:
        if entry.get("date_ist", "") >= cutoff:
            runs = entry.get("runs")
            if isinstance(runs, dict) and runs.get("count", 0) > 0:
                count += 1
    return count


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Garmin hourly health snapshot poller")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one poll cycle and exit (used by systemd timer).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Override snapshot output path (default: ~/.hermes/health_today.json).",
    )
    args = parser.parse_args()

    poll_once(snapshot_path=args.output)


if __name__ == "__main__":
    main()
