"""Integration tests for the orchestrator's reminder wiring in handler.py:
the message-extraction / IST-formatting / cancel-matching helpers, plus a real
parse -> store -> list round-trip through the reminders package.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks" / "jack_router"))
sys.path.insert(0, str(ROOT))

import handler  # noqa: E402
from reminders import parser as rparser  # noqa: E402
from reminders.store import ReminderStore  # noqa: E402


class HelperTest(unittest.TestCase):
    def test_extracts_task_from_set_request(self):
        self.assertEqual(handler._reminder_message("remind me to call mom at 9am tomorrow"), "call mom")
        self.assertEqual(handler._reminder_message("remind me to buy milk in 2 hours"), "buy milk")
        self.assertEqual(handler._reminder_message("don't let me forget the demo tonight"), "the demo")

    def test_fmt_ist_converts_utc(self):
        # 03:30 UTC == 09:00 IST (UTC+5:30)
        out = handler._fmt_ist("2026-06-19T03:30:00Z")
        self.assertIn("9:00", out)
        self.assertIn("IST", out)

    def test_match_reminder_by_overlap(self):
        items = [{"id": "1", "message": "call mom"}, {"id": "2", "message": "buy milk"}]
        self.assertEqual(handler._match_reminder("cancel the call mom reminder", items)["id"], "1")

    def test_match_reminder_single_defaults(self):
        items = [{"id": "9", "message": "anything"}]
        self.assertEqual(handler._match_reminder("cancel my reminder", items)["id"], "9")


class RoundTripTest(unittest.TestCase):
    def test_set_then_list(self):
        path = Path(tempfile.mkdtemp()) / "reminders.json"
        store = ReminderStore(path=path)
        parsed = rparser.parse_time("remind me to call mom in 2 hours")
        store.add("u1", handler._reminder_message("remind me to call mom in 2 hours"),
                  parsed.fire_at, parsed.recurring)
        items = store.list_pending("u1")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["message"], "call mom")
        self.assertFalse(items[0]["fired"])


if __name__ == "__main__":
    unittest.main()
