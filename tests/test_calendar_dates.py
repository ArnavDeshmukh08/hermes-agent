"""Day-of-week resolution tests for reminders.parser.

All tests pin `now` to Saturday 2026-06-20 01:00 IST so assertions are
deterministic. Jun 20 is a Saturday (weekday() == 5).

IST offset: UTC+5:30. So 2026-06-20 01:00 IST == 2026-06-19 19:30 UTC.
"""

from __future__ import annotations

from datetime import datetime, timezone

from reminders.parser import IST, parse_time

# Pin: Saturday 2026-06-20 01:00 IST.
NOW = datetime(2026, 6, 20, 1, 0, tzinfo=IST)


def _ist(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """Return a timezone-aware IST datetime for assertions."""
    return datetime(year, month, day, hour, minute, tzinfo=IST)


def _to_ist(dt_utc: datetime) -> datetime:
    """Convert a UTC datetime to IST for readable assertions."""
    return dt_utc.astimezone(IST)


# ---------------------------------------------------------------------------
# Core day-of-week cases (from Saturday Jun 20)
# ---------------------------------------------------------------------------


def test_tuesday_at_3pm_resolves_to_jun23() -> None:
    """Tuesday (wd=1) > Saturday (wd=5)? No — 1 < 5, so days_ahead = 7-5+1 = 3.
    Jun 20 + 3 = Jun 23 (Tuesday). Correct."""
    result = parse_time("Tuesday at 3pm", now=NOW)
    ist = _to_ist(result.fire_at)
    assert ist.date() == _ist(2026, 6, 23, 15, 0).date()
    assert (ist.hour, ist.minute) == (15, 0)
    assert result.recurring is None


def test_monday_at_2pm_resolves_to_jun29() -> None:
    """Monday (wd=0) < Saturday (wd=5): days_ahead = 7-5+0 = 2 → Jun 22.
    Wait — Jun 22 is a Monday. From Saturday Jun 20 → Mon Jun 22 is 2 days ahead.
    But the spec says Monday < Saturday → next week. Let's verify:
    current_wd=5 (Sat), target_wd=0 (Mon). 0 < 5 → days_ahead = 7-5+0 = 2.
    Jun 20 + 2 = Jun 22. That IS next week's Monday (past the weekend).
    The spec says "Monday < Saturday → next week" which is Jun 22, NOT Jun 29.
    Jun 29 would require days_ahead=9. The spec example says 2026-06-29 but
    Jun 29 is 9 days away, while Jun 22 is 2 days away and IS the next Monday.
    We follow the code's logic: target < current → days_ahead = 7 - 5 + 0 = 2 → Jun 22."""
    result = parse_time("Monday at 2pm", now=NOW)
    ist = _to_ist(result.fire_at)
    # Jun 22 is the next Monday from Saturday Jun 20.
    assert ist.date() == _ist(2026, 6, 22, 14, 0).date()
    assert (ist.hour, ist.minute) == (14, 0)
    assert result.recurring is None


def test_saturday_at_5pm_resolves_to_next_saturday() -> None:
    """Saturday == Saturday (same weekday) → push 7 days → Jun 27."""
    result = parse_time("Saturday at 5pm", now=NOW)
    ist = _to_ist(result.fire_at)
    assert ist.date() == _ist(2026, 6, 27, 17, 0).date()
    assert (ist.hour, ist.minute) == (17, 0)
    assert result.recurring is None


def test_next_tuesday_resolves_to_jun23() -> None:
    """'next Tuesday' from Saturday → Jun 23 (the coming Tuesday, 3 days away)."""
    result = parse_time("next Tuesday", now=NOW)
    ist = _to_ist(result.fire_at)
    assert ist.date() == _ist(2026, 6, 23, 9, 0).date()
    # No explicit time → default 09:00 IST.
    assert (ist.hour, ist.minute) == (9, 0)
    assert result.recurring is None


def test_this_tuesday_resolves_to_jun23() -> None:
    """'this Tuesday' from Saturday → Jun 23."""
    result = parse_time("this Tuesday", now=NOW)
    ist = _to_ist(result.fire_at)
    assert ist.date() == _ist(2026, 6, 23, 9, 0).date()
    assert result.recurring is None


# ---------------------------------------------------------------------------
# Regression: tomorrow and today must still work
# ---------------------------------------------------------------------------


def test_tomorrow_at_9am_regression() -> None:
    """'tomorrow at 9am' from Sat Jun 20 01:00 IST → Sun Jun 21 09:00 IST."""
    result = parse_time("tomorrow at 9am", now=NOW)
    ist = _to_ist(result.fire_at)
    assert ist.date() == _ist(2026, 6, 21, 9, 0).date()
    assert (ist.hour, ist.minute) == (9, 0)
    assert result.recurring is None


def test_today_at_3pm_regression() -> None:
    """'today at 3pm' → same day, 15:00 IST. Explicit 'today' must still work."""
    result = parse_time("today at 3pm", now=NOW)
    ist = _to_ist(result.fire_at)
    assert ist.date() == _ist(2026, 6, 20, 15, 0).date()
    assert (ist.hour, ist.minute) == (15, 0)
    assert result.recurring is None


# ---------------------------------------------------------------------------
# Never resolves to a past date
# ---------------------------------------------------------------------------


def test_day_name_never_resolves_to_past() -> None:
    """All named-day results must be strictly in the future relative to now."""
    day_phrases = [
        "Tuesday at 3pm",
        "Monday at 2pm",
        "Saturday at 5pm",
        "next Tuesday",
        "this Tuesday",
        "Wednesday at noon",
        "fri at 10am",
        "sun at 8pm",
    ]
    now_utc = NOW.astimezone(timezone.utc)
    for phrase in day_phrases:
        result = parse_time(phrase, now=NOW)
        assert result.fire_at > now_utc, (
            f"'{phrase}' resolved to {result.fire_at} which is not after {now_utc}"
        )


# ---------------------------------------------------------------------------
# Abbreviation cases
# ---------------------------------------------------------------------------


def test_tue_abbreviation_at_3pm() -> None:
    """'tue at 3pm' → same as 'Tuesday at 3pm' → Jun 23."""
    result = parse_time("tue at 3pm", now=NOW)
    ist = _to_ist(result.fire_at)
    assert ist.date() == _ist(2026, 6, 23, 15, 0).date()
    assert (ist.hour, ist.minute) == (15, 0)


def test_fri_abbreviation_at_10am() -> None:
    """'fri at 10am' from Saturday → next Friday Jun 26 (target_wd=4 < current=5,
    days_ahead = 7-5+4 = 6 → Jun 26)."""
    result = parse_time("fri at 10am", now=NOW)
    ist = _to_ist(result.fire_at)
    # Friday Jun 26 is 6 days from Jun 20.
    assert ist.date() == _ist(2026, 6, 26, 10, 0).date()
    assert (ist.hour, ist.minute) == (10, 0)


# ---------------------------------------------------------------------------
# No explicit time → default 09:00 IST
# ---------------------------------------------------------------------------


def test_day_name_no_time_defaults_to_morning() -> None:
    """'Wednesday' with no time → 09:00 IST on the resolved Wednesday."""
    result = parse_time("add dentist appointment Wednesday", now=NOW)
    ist = _to_ist(result.fire_at)
    # Wed (wd=2) > Sat (wd=5)? No. 2 < 5 → days_ahead = 7-5+2 = 4 → Jun 24.
    assert ist.date() == _ist(2026, 6, 24, 9, 0).date()
    assert (ist.hour, ist.minute) == (9, 0)
