"""Weather fetcher for the morning briefing via wttr.in (stdlib-only, no deps).

Uses wttr.in's JSON API (format=j1) to pull current conditions. Returns None
on any failure so the briefing is never blocked by a weather outage.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

_WTTR_BASE = "https://wttr.in/"
_TIMEOUT_S = 10
_USER_AGENT = "HermesAgent/1.0 (personal assistant; wttr.in weather fetch)"
_DEFAULT_LOCATION = "Ichalkaranji, India"


class WeatherFetcher:
    """Fetch current weather from wttr.in."""

    def get_weather(self, location: str | None = None) -> dict | None:
        """Return a dict with current conditions or None on any failure.

        Keys: temp_c, feels_like_c, condition, humidity, location.
        """
        loc = location or os.environ.get("JACK_WEATHER_LOCATION", _DEFAULT_LOCATION)
        url = _WTTR_BASE + urllib.parse.quote(loc, safe="") + "?format=j1"
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": _USER_AGENT},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:  # noqa: S310
                raw = resp.read()
            data = json.loads(raw)
            cc = data["current_condition"][0]
            return {
                "temp_c": int(cc["temp_C"]),
                "feels_like_c": int(cc["FeelsLikeC"]),
                "condition": cc["weatherDesc"][0]["value"],
                "humidity": int(cc["humidity"]),
                "location": loc.split(",")[0].strip(),
            }
        except Exception:  # noqa: BLE001 — outage must never block the briefing
            return None

    def summary_text(self, location: str | None = None) -> str:
        """Return a one-line weather summary or '' on failure.

        Example: "28°C and Partly cloudy in Ichalkaranji (feels like 31°C)"
        """
        w = self.get_weather(location)
        if w is None:
            return ""
        return (
            f"{w['temp_c']}°C and {w['condition']} in {w['location']}"
            f" (feels like {w['feels_like_c']}°C)"
        )
