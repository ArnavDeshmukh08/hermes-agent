"""Tests for reminder task-text extraction and fire-date formatting in jack_router.handler.

Covers FIX 1 (_reminder_message cleanup: filler + time-embedded command prefixes + time tail)
and FIX 2 (_fmt_ist renders the reminder's fire date from fire_at, never today's date).
"""

import unittest
from datetime import datetime, timedelta, timezone

from hooks.jack_router import handler


class TaskExtractionTests(unittest.TestCase):
    def test_strips_yeah_remind_me_at_time_that(self):
        got = handler._reminder_message(
            "yeah remind me at 11am that do subject registration for 3rd year"
        )
        self.assertEqual(got, "do subject registration for 3rd year")

    def test_strips_remind_me_to(self):
        self.assertEqual(handler._reminder_message("remind me to call mom at 9am"), "call mom")

    def test_strips_leading_filler(self):
        self.assertEqual(
            handler._reminder_message("ok so remind me to call Spandan at 12pm"),
            "call Spandan",
        )

    def test_extracts_clean_task_only(self):
        cases = {
            "remind me at 9am to take meds": "take meds",
            "please remind me to pay rent tomorrow": "pay rent",
            "set a reminder to water the plants tonight": "water the plants",
            "don't let me forget to email the professor at 5pm": "email the professor",
            "alert me when the build finishes": "the build finishes",
        }
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(handler._reminder_message(phrase), expected)

    def test_non_empty_fallback(self):
        # If a phrase is nothing but a stripped prefix, fall back to the original text.
        self.assertEqual(handler._reminder_message("remind me"), "remind me")


class FireDateFormattingTests(unittest.TestCase):
    def test_date_shows_fire_date_not_today(self):
        # Build a UTC fire time for "tomorrow 03:30 UTC" relative to a fixed today.
        # 03:30 UTC == 09:00 IST (UTC+5:30), and the IST date is the same calendar day
        # as the UTC date here, so the rendered date must be tomorrow's IST date.
        today_utc = datetime(2026, 6, 19, 0, 0, tzinfo=timezone.utc)
        tomorrow = today_utc + timedelta(days=1)
        fire_at = tomorrow.replace(hour=3, minute=30)

        rendered = handler._fmt_ist(fire_at.isoformat().replace("+00:00", "Z"))

        expected_day = tomorrow.strftime("%a %b %-d")  # 'Sat Jun 20'
        today_day = today_utc.strftime("%a %b %-d")  # 'Fri Jun 19'

        self.assertIn(expected_day, rendered)
        self.assertNotIn(today_day, rendered)
        self.assertIn("9:00 AM IST", rendered)


if __name__ == "__main__":
    unittest.main()
