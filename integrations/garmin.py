"""Garmin Connect integration for Jack.

Fetches sleep data from Garmin Connect using the garminconnect library.

Design rules (see integrations/__init__.py):
- garminconnect is imported LAZILY inside methods — never at module top.
- All public methods return None/'' on any failure; they never raise.
- Credentials come from env vars GARMIN_EMAIL / GARMIN_PASSWORD (or
  constructor args). Missing creds → graceful None, no raise.
- The password is never logged.
"""

from __future__ import annotations

import datetime
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _today_ist() -> str:
    """Return today's date in IST as an ISO-8601 string (YYYY-MM-DD)."""
    return datetime.datetime.now(tz=_IST).date().isoformat()


class GarminClient:
    """Thin wrapper around garminconnect that degrades gracefully."""

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
    ) -> None:
        self._email: str | None = email or os.environ.get("GARMIN_EMAIL")
        self._password: str | None = password or os.environ.get("GARMIN_PASSWORD")
        self._client: Any = None  # cached after first successful login

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _login(self) -> Any:
        """Return a logged-in garminconnect.Garmin client, or None.

        Caches the client on self so subsequent calls in the same process
        reuse the session. Returns None on missing creds, ImportError,
        or auth failure — never raises.
        """
        if self._client is not None:
            return self._client

        if not self._email or not self._password:
            logger.debug("Garmin: no credentials configured — skipping login")
            return None

        try:
            from garminconnect import Garmin  # lazy import
        except ImportError:
            logger.warning("Garmin: garminconnect package not installed")
            return None

        try:
            client = Garmin(self._email, self._password)
            client.login()
            self._client = client
            logger.info("Garmin: logged in as %s", self._email)
            return self._client
        except Exception:
            logger.warning("Garmin: login failed", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def last_night_sleep(self, day: str | None = None) -> dict | None:
        """Return a sleep summary dict for *day* (ISO date), or None.

        Keys: total_sleep_h (float), deep_h (float), rem_h (float),
              score (int | None).
        All fields are parsed defensively; None is returned on any error
        or empty response.
        """
        client = self._login()
        if client is None:
            return None

        iso_date = day or _today_ist()

        try:
            raw = client.get_sleep_data(iso_date)
        except Exception:
            logger.warning("Garmin: get_sleep_data failed for %s", iso_date, exc_info=True)
            return None

        if not raw:
            return None

        try:
            dto: dict = raw.get("dailySleepDTO") or {}
            if not dto:
                return None

            def _secs_to_h(key: str) -> float:
                val = dto.get(key)
                if val is None:
                    return 0.0
                return round(int(val) / 3600, 1)

            total_sleep_h = _secs_to_h("sleepTimeSeconds")
            deep_h = _secs_to_h("deepSleepSeconds")
            rem_h = _secs_to_h("remSleepSeconds")

            # score lives at sleepScores.overall.value — all optional
            sleep_scores = raw.get("sleepScores") or {}
            overall = sleep_scores.get("overall") or {}
            score_raw = overall.get("value")
            score: int | None = int(score_raw) if score_raw is not None else None

            return {
                "total_sleep_h": total_sleep_h,
                "deep_h": deep_h,
                "rem_h": rem_h,
                "score": score,
            }
        except Exception:
            logger.warning("Garmin: failed to parse sleep response", exc_info=True)
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
