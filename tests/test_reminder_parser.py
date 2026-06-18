"""Tests for reminders.parser — natural-language IST time -> UTC.

A FIXED `now` is used so every assertion is deterministic. The anchor is
2026-06-19 10:00 IST (a Friday), which is 2026-06-19 04:30 UTC.
"""

import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from reminders.parser import IST, ParsedTime, parse_time

# Friday, 19 Jun 2026, 10:00 IST.
NOW = datetime(2026, 6, 19, 10, 0, tzinfo=IST)


def _utc(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class ParseTimeTests(unittest.TestCase):
    def test_tomorrow_9am(self):
        # 20 Jun 09:00 IST == 20 Jun 03:30 UTC.
        result = parse_time("tomorrow at 9am", now=NOW)
        self.assertEqual(result.recurring, None)
        self.assertEqual(result.fire_at.tzinfo, timezone.utc)
        self.assertEqual(result.fire_at, _utc(2026, 6, 20, 3, 30))

    def test_in_2_hours(self):
        # 10:00 + 2h = 12:00 IST == 06:30 UTC.
        result = parse_time("in 2 hours", now=NOW)
        self.assertIsNone(result.recurring)
        self.assertEqual(result.fire_at.tzinfo, timezone.utc)
        self.assertEqual(result.fire_at, _utc(2026, 6, 19, 6, 30))

    def test_in_30_minutes(self):
        # 10:00 + 30m = 10:30 IST == 05:00 UTC.
        result = parse_time("in 30 minutes", now=NOW)
        self.assertIsNone(result.recurring)
        self.assertEqual(result.fire_at, _utc(2026, 6, 19, 5, 0))

    def test_tonight_11pm(self):
        # 19 Jun 23:00 IST == 19 Jun 17:30 UTC.
        result = parse_time("tonight at 11pm", now=NOW)
        self.assertIsNone(result.recurring)
        self.assertEqual(result.fire_at.tzinfo, timezone.utc)
        self.assertEqual(result.fire_at, _utc(2026, 6, 19, 17, 30))

    def test_every_morning_8am(self):
        # Default every-morning is 08:00 IST. 08:00 already passed today (now
        # is 10:00), so next occurrence is 20 Jun 08:00 IST == 02:30 UTC.
        result = parse_time("every morning", now=NOW)
        self.assertEqual(result.recurring, "daily")
        self.assertEqual(result.fire_at, _utc(2026, 6, 20, 2, 30))
        # Confirm the IST wall-clock is 08:00.
        ist = result.fire_at.astimezone(IST)
        self.assertEqual((ist.hour, ist.minute), (8, 0))

    def test_every_weekday_recurring(self):
        # "every weekday at 9am": 09:00 today already passed -> tomorrow
        # 20 Jun is Saturday, so it must roll to Monday 22 Jun 09:00 IST
        # == 03:30 UTC.
        result = parse_time("every weekday at 9am", now=NOW)
        self.assertEqual(result.recurring, "weekday")
        self.assertEqual(result.fire_at, _utc(2026, 6, 22, 3, 30))
        self.assertLess(result.fire_at.astimezone(IST).weekday(), 5)

    def test_invalid_time_raises_friendly_error(self):
        with self.assertRaises(ValueError) as ctx:
            parse_time("sometime when pigs fly", now=NOW)
        msg = str(ctx.exception)
        self.assertIn("tomorrow at 9am", msg)
        self.assertIn("in 2 hours", msg)


if __name__ == "__main__":
    unittest.main()
