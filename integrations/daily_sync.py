"""
Daily health data sync: fetches from Mac Garmin service → writes to Mem0.
Designed to run once per day, typically at ~8am or triggered by briefing.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

logger = logging.getLogger(__name__)

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _today_ist() -> str:
    """Return today's date in IST as YYYY-MM-DD."""
    return datetime.datetime.now(_IST).strftime("%Y-%m-%d")


class DailySyncJob:
    """Fetches Garmin health data and persists it to Mem0 semantic memory."""

    def __init__(
        self,
        garmin_client: Any = None,
        memory_client: Any = None,
        user_id: str = "arnav",
    ) -> None:
        self._garmin = garmin_client  # lazily built if None
        self._memory = memory_client  # lazily built if None
        self._user_id = user_id

    # ------------------------------------------------------------------
    # Lazy dependency wiring
    # ------------------------------------------------------------------

    def _get_garmin(self) -> Any:
        if self._garmin is None:
            from integrations.garmin import GarminClient  # lazy import

            self._garmin = GarminClient()
        return self._garmin

    def _get_memory(self) -> Any:
        if self._memory is None:
            from jack_memory.client import JackMemoryClient  # lazy import

            self._memory = JackMemoryClient.from_env()
        return self._memory

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync_sleep(self, day: str | None = None) -> bool:
        """Fetch last night's sleep → write one memory. Returns True if written."""
        garmin = self._get_garmin()
        date_str = day or _today_ist()

        data = garmin.last_night_sleep(date_str)
        if data is None:
            logger.info("daily_sync: no sleep data available for %s", date_str)
            return False

        total = data.get("total_sleep_h", 0.0)
        deep = data.get("deep_h", 0.0)
        rem = data.get("rem_h", 0.0)
        score = data.get("score")

        message = f"Arnav slept {total}h last night ({deep}h deep, {rem}h REM)."
        if score is not None:
            message = (
                f"Arnav slept {total}h last night ({deep}h deep, {rem}h REM). "
                f"Sleep score: {score}."
            )

        metadata = {"source": "garmin", "type": "sleep", "date": date_str}

        try:
            self._get_memory().add(message, user_id=self._user_id, metadata=metadata)
            logger.info("daily_sync: sleep memory written for %s", date_str)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "daily_sync: failed to write sleep memory for %s: %s",
                date_str,
                exc,
                exc_info=True,
            )
            return False

    def sync_stats(self, day: str | None = None) -> bool:
        """Fetch today's activity stats → write one memory. Returns True if written."""
        garmin = self._get_garmin()
        date_str = day or _today_ist()

        # get_stats may not exist on all GarminClient versions — degrade gracefully
        get_stats = getattr(garmin, "get_stats", None)
        data = get_stats(date_str) if get_stats is not None else None
        if data is None:
            logger.info("daily_sync: no stats data available for %s", date_str)
            return False

        steps = data.get("steps")
        batt = data.get("body_battery_high")
        cals = data.get("calories")

        parts: list[str] = []
        if steps is not None:
            parts.append(f"{steps} steps")
        if batt is not None:
            parts.append(f"body battery peaked at {batt}%")
        if cals is not None:
            parts.append(f"{cals} kcal burned")

        if not parts:
            logger.info("daily_sync: stats dict had no usable fields for %s", date_str)
            return False

        message = "Today Arnav had " + ", ".join(parts) + "."

        metadata = {"source": "garmin", "type": "stats", "date": date_str}

        try:
            self._get_memory().add(message, user_id=self._user_id, metadata=metadata)
            logger.info("daily_sync: stats memory written for %s", date_str)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "daily_sync: failed to write stats memory for %s: %s",
                date_str,
                exc,
                exc_info=True,
            )
            return False

    def sync_daily(self) -> dict:
        """Run full daily sync (sleep + stats). Returns {"sleep": bool, "stats": bool}."""
        sleep_ok = self.sync_sleep()
        stats_ok = self.sync_stats()
        return {"sleep": sleep_ok, "stats": stats_ok}

    def run(self) -> dict:
        """Alias for sync_daily(). Entry point for cron/scheduler."""
        return self.sync_daily()


# ------------------------------------------------------------------
# Module-level convenience
# ------------------------------------------------------------------


def run_daily_sync() -> dict:
    """One-liner entry point: DailySyncJob().run()"""
    return DailySyncJob().run()


if __name__ == "__main__":
    import json

    result = run_daily_sync()
    print(json.dumps(result, indent=2))
