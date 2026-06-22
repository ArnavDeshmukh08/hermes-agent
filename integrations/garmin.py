"""Garmin integration for Jack — VPS-side HTTP client.

Instead of calling Garmin Connect directly (the VPS IP is blocked),
this module fetches data from the Mac-side Garmin service over Tailscale.

Mac service URL is read from JACK_GARMIN_URL (default http://100.120.65.115:8765).

Design rules (see integrations/__init__.py):
- No garminconnect import — credentials live only on the Mac.
- All public methods return None/'' on any failure; they never raise.
- JACK_GARMIN_URL is always read from env — never hardcoded here.
"""

from __future__ import annotations

import datetime
import logging
import os
from typing import Any

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _requests = None  # type: ignore[assignment]
    _HAS_REQUESTS = False

logger = logging.getLogger(__name__)

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _today_ist() -> str:
    """Return today's date in IST as an ISO-8601 string (YYYY-MM-DD)."""
    return datetime.datetime.now(tz=_IST).date().isoformat()


def _get(url: str, timeout: float) -> Any:
    """Perform a GET request and return the parsed JSON body, or raise.

    Uses `requests` if available, falls back to `urllib.request`.
    Raises on any network or HTTP error — callers must handle.
    """
    if _HAS_REQUESTS:
        resp = _requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    else:
        import json
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode())


class GarminClient:
    """HTTP client that proxies Garmin data through the Mac service."""

    def __init__(self) -> None:
        self._base_url: str = os.environ.get(
            "JACK_GARMIN_URL", "http://100.120.65.115:8765"
        ).rstrip("/")
        self._timeout: float = float(os.environ.get("JACK_GARMIN_TIMEOUT", "5"))

    # ------------------------------------------------------------------
    # Public API — signatures identical to the old garminconnect-backed impl
    # ------------------------------------------------------------------

    def last_night_sleep(self, day: str | None = None) -> dict | None:
        """Return a sleep summary dict for *day* (ISO date), or None.

        Keys: total_sleep_h (float), deep_h (float), rem_h (float),
              score (int | None).
        Returns None if the Mac service is unreachable, returns no data,
        or returns {"error": "no_data"}.  Never raises.
        """
        iso_date = day or _today_ist()
        url = f"{self._base_url}/sleep?date={iso_date}"
        try:
            data = _get(url, self._timeout)
        except Exception:
            logger.warning("Garmin: failed to fetch sleep for %s from %s", iso_date, url, exc_info=True)
            return None

        if not isinstance(data, dict):
            logger.warning("Garmin: unexpected sleep response type for %s: %r", iso_date, data)
            return None

        if data.get("error") == "no_data":
            logger.debug("Garmin: no sleep data for %s", iso_date)
            return None

        try:
            total_sleep_h = float(data["total_sleep_h"])
            deep_h = float(data["deep_h"])
            rem_h = float(data["rem_h"])
            score_raw = data.get("score")
            score: int | None = int(score_raw) if score_raw is not None else None

            if total_sleep_h <= 0:
                return None

            return {
                "total_sleep_h": total_sleep_h,
                "deep_h": deep_h,
                "rem_h": rem_h,
                "score": score,
            }
        except Exception:
            logger.warning("Garmin: failed to parse sleep response for %s: %r", iso_date, data, exc_info=True)
            return None

    def sleep_summary_text(self, day: str | None = None) -> str:
        """Return a one-line human-readable sleep summary, or '' on failure."""
        try:
            data = self.last_night_sleep(day=day)
            if data is None:
                return ""

            parts = [f"{data['total_sleep_h']}h sleep ({data['deep_h']}h deep)"]
            if data["score"] is not None:
                parts.append(f"score {data['score']}")
            return ", ".join(parts)
        except Exception:
            logger.warning("Garmin: sleep_summary_text failed", exc_info=True)
            return ""

    def get_stats(self, day: str | None = None) -> dict | None:
        """Return activity stats for *day*, or None on any failure.

        Keys (from Mac service): steps (int), body_battery_high (int),
        calories (int), stress_avg (int).  Never raises.
        """
        iso_date = day or _today_ist()
        url = f"{self._base_url}/stats?date={iso_date}"
        try:
            data = _get(url, self._timeout)
        except Exception:
            logger.warning("Garmin: failed to fetch stats for %s from %s", iso_date, url, exc_info=True)
            return None

        if not isinstance(data, dict):
            logger.warning("Garmin: unexpected stats response type for %s: %r", iso_date, data)
            return None

        if data.get("error") == "no_data":
            logger.debug("Garmin: no stats data for %s", iso_date)
            return None

        return data

    def get_daily_summary(self) -> dict | None:
        """Return today's combined summary, or None on any failure.

        Keys: date (str), sleep (dict | None), stats (dict | None).
        Never raises.
        """
        url = f"{self._base_url}/daily_summary"
        try:
            data = _get(url, self._timeout)
        except Exception:
            logger.warning("Garmin: failed to fetch daily_summary from %s", url, exc_info=True)
            return None

        if not isinstance(data, dict):
            logger.warning("Garmin: unexpected daily_summary response type: %r", data)
            return None

        return data
