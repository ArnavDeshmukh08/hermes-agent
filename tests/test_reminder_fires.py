"""Regression tests for the reminder FIRE path (the '9am reminder never fired'
incident). The forensic root cause was a crashed scheduler service (import
bootstrap), not a timezone bug — these tests pin the time math + fire path so a
real tz-aware/naive regression (the classic culprit) can't slip in unnoticed.
"""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reminders import parser as rparser  # noqa: E402
from reminders.scheduler import ReminderScheduler  # noqa: E402
from reminders.store import ReminderStore  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


def _store():
    return ReminderStore(path=Path(tempfile.mkdtemp()) / "r.json")


class TimeConversionTest(unittest.TestCase):
    def test_tomorrow_9am_ist_converts_to_correct_utc(self):
        # 9:00 IST == 03:30 UTC; result must be timezone-aware
        now = datetime(2026, 6, 19, 18, 0, tzinfo=IST)  # evening IST
        parsed = rparser.parse_time("call mom at 9am tomorrow", now=now)
        self.assertIsNotNone(parsed.fire_at.tzinfo)
        self.assertEqual(parsed.fire_at.utcoffset(), timedelta(0))  # stored in UTC
        ist = parsed.fire_at.astimezone(IST)
        self.assertEqual((ist.hour, ist.minute), (9, 0))
        self.assertEqual(ist.date(), now.date() + timedelta(days=1))


class FirePathTest(unittest.TestCase):
    def test_due_reminder_detected_when_time_arrives(self):
        s = _store()
        fire = datetime.now(timezone.utc) + timedelta(minutes=10)
        s.add("u", "call mom", fire, None)
        # before the time: not due
        self.assertEqual(s.get_due(now=fire - timedelta(seconds=1)), [])
        # at/after the time: due (this is the comparison that the incident proved works)
        due = s.get_due(now=fire + timedelta(seconds=1))
        self.assertEqual([r["message"] for r in due], ["call mom"])

    def test_notifier_called_when_reminder_due(self):
        s = _store()
        s.add("u", "ping", datetime.now(timezone.utc) - timedelta(minutes=1), None)
        calls = []
        sched = ReminderScheduler(store=s, notifier=lambda msg, user_id=None: calls.append(msg) or True)
        fired = sched.run_once()
        self.assertEqual(fired, 1)
        self.assertEqual(len(calls), 1)
        self.assertIn("ping", calls[0])
        # and it's marked fired so it won't double-fire
        self.assertEqual(s.get_due(), [])

    def test_future_reminder_not_fired(self):
        s = _store()
        s.add("u", "later", datetime.now(timezone.utc) + timedelta(hours=2), None)
        calls = []
        sched = ReminderScheduler(store=s, notifier=lambda msg, user_id=None: calls.append(msg) or True)
        self.assertEqual(sched.run_once(), 0)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
