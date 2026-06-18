"""Natural-language time parsing for Jack's reminders.

All input times are interpreted in IST (Asia/Kolkata) — Arnav's timezone —
and converted to timezone-aware UTC for storage. We never hardcode a +5:30
offset; ZoneInfo handles DST/offset correctness for us.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Defaults for fuzzy time-of-day words (IST hours).
_MORNING_HOUR = 9
_TONIGHT_HOUR = 21
_EVERY_MORNING_HOUR = 8
_EVERY_NIGHT_HOUR = 21

_FRIENDLY_ERROR = (
    "I couldn't work out the time — try 'tomorrow at 9am' or 'in 2 hours'."
)


@dataclass(frozen=True)
class ParsedTime:
    """A parsed reminder time.

    fire_at is timezone-aware UTC. recurring is "daily", "weekday", or None.
    """

    fire_at: datetime
    recurring: str | None


def _to_utc(dt_ist: datetime) -> datetime:
    """Attach IST tzinfo (if naive) and convert to UTC."""
    if dt_ist.tzinfo is None:
        dt_ist = dt_ist.replace(tzinfo=IST)
    return dt_ist.astimezone(timezone.utc)


def _parse_clock(text: str) -> tuple[int, int] | None:
    """Extract an explicit clock time like '9am', '5pm', '11:30pm', '14:00'.

    Returns (hour_24, minute) in IST, or None if no clock time is present.
    """
    # 12-hour with am/pm, optional minutes.
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\b", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        meridiem = m.group(3)
        if hour == 12:
            hour = 0
        if meridiem == "p":
            hour += 12
        if hour > 23 or minute > 59:
            return None
        return hour, minute

    # 24-hour "HH:MM".
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        if hour > 23 or minute > 59:
            return None
        return hour, minute

    return None


def _next_weekday(dt: datetime) -> datetime:
    """Advance dt to the next Mon-Fri day (same time-of-day) if it lands on
    a weekend. dt is returned unchanged when it already falls on a weekday."""
    while dt.weekday() >= 5:  # 5 = Sat, 6 = Sun
        dt = dt + timedelta(days=1)
    return dt


def parse_time(text: str, now: datetime | None = None) -> ParsedTime:
    """Parse natural-language time `text` (IST) into a ParsedTime in UTC.

    Raises ValueError with a chat-friendly message if it can't be parsed.
    """
    if now is None:
        now = datetime.now(IST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)

    raw = text.strip().lower()
    if not raw:
        raise ValueError(_FRIENDLY_ERROR)

    clock = _parse_clock(raw)

    # ---- Recurring -------------------------------------------------------
    # "every weekday at X" — needs an explicit time.
    if "every weekday" in raw or "every week day" in raw:
        if clock is None:
            raise ValueError(_FRIENDLY_ERROR)
        hour, minute = clock
        fire = _next_occurrence(now, hour, minute)
        fire = _next_weekday(fire)
        return ParsedTime(fire_at=_to_utc(fire), recurring="weekday")

    if "every morning" in raw:
        hour, minute = clock if clock else (_EVERY_MORNING_HOUR, 0)
        fire = _next_occurrence(now, hour, minute)
        return ParsedTime(fire_at=_to_utc(fire), recurring="daily")

    if "every night" in raw or "every evening" in raw:
        hour, minute = clock if clock else (_EVERY_NIGHT_HOUR, 0)
        fire = _next_occurrence(now, hour, minute)
        return ParsedTime(fire_at=_to_utc(fire), recurring="daily")

    if "every day" in raw or "everyday" in raw:
        if clock is None:
            raise ValueError(_FRIENDLY_ERROR)
        hour, minute = clock
        fire = _next_occurrence(now, hour, minute)
        return ParsedTime(fire_at=_to_utc(fire), recurring="daily")

    # ---- Relative: "in N <unit>" ----------------------------------------
    m = re.search(
        r"\bin\s+(\d+)\s*(hour|hours|hr|hrs|minute|minutes|min|mins)\b", raw
    )
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        if unit.startswith(("hour", "hr")):
            delta = timedelta(hours=amount)
        else:
            delta = timedelta(minutes=amount)
        return ParsedTime(fire_at=_to_utc(now + delta), recurring=None)

    # ---- Absolute day anchors -------------------------------------------
    # "tonight" — defaults to ~21:00 unless an explicit time is given.
    if "tonight" in raw:
        hour, minute = clock if clock else (_TONIGHT_HOUR, 0)
        fire = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if fire <= now:
            fire = fire + timedelta(days=1)
        return ParsedTime(fire_at=_to_utc(fire), recurring=None)

    if "tomorrow" in raw:
        if "morning" in raw and clock is None:
            hour, minute = _MORNING_HOUR, 0
        elif clock is not None:
            hour, minute = clock
        else:
            hour, minute = _MORNING_HOUR, 0
        base = now + timedelta(days=1)
        fire = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return ParsedTime(fire_at=_to_utc(fire), recurring=None)

    if "today" in raw:
        if clock is None:
            raise ValueError(_FRIENDLY_ERROR)
        hour, minute = clock
        fire = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return ParsedTime(fire_at=_to_utc(fire), recurring=None)

    # bare "morning" → tomorrow-ish 9am next occurrence
    if "morning" in raw and clock is None:
        fire = _next_occurrence(now, _MORNING_HOUR, 0)
        return ParsedTime(fire_at=_to_utc(fire), recurring=None)

    # ---- "at X" → next occurrence today/tomorrow ------------------------
    if clock is not None:
        hour, minute = clock
        fire = _next_occurrence(now, hour, minute)
        return ParsedTime(fire_at=_to_utc(fire), recurring=None)

    raise ValueError(_FRIENDLY_ERROR)


def _next_occurrence(now: datetime, hour: int, minute: int) -> datetime:
    """The next datetime at hour:minute (IST), today if still future else tomorrow."""
    fire = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if fire <= now:
        fire = fire + timedelta(days=1)
    return fire
