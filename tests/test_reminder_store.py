"""Tests for reminders.store.ReminderStore — durable, flock-safe JSON store.

Each test gets its own tmp file so they stay isolated.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reminders.store import ReminderStore

UTC = timezone.utc


class ReminderStoreTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "reminders.json"

    def tearDown(self):
        self._dir.cleanup()

    def _store(self):
        return ReminderStore(self.path)

    def test_add_and_retrieve(self):
        store = self._store()
        fire = datetime(2026, 6, 20, 3, 30, tzinfo=UTC)
        rid = store.add("u1", "drink water", fire)
        self.assertTrue(rid)

        pending = store.list_pending("u1")
        self.assertEqual(len(pending), 1)
        r = pending[0]
        self.assertEqual(r["id"], rid)
        self.assertEqual(r["user_id"], "u1")
        self.assertEqual(r["message"], "drink water")
        self.assertEqual(r["fire_at"], "2026-06-20T03:30:00Z")
        self.assertIsNone(r["recurring"])
        self.assertFalse(r["fired"])
        self.assertTrue(r["created_at"].endswith("Z"))

    def test_get_due_returns_correct_reminders(self):
        store = self._store()
        now = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)
        past = store.add("u1", "past", now - timedelta(hours=1))
        store.add("u1", "future", now + timedelta(hours=1))

        due = store.get_due(now=now)
        due_ids = {r["id"] for r in due}
        self.assertIn(past, due_ids)
        self.assertEqual(len(due), 1)

    def test_mark_fired_removes_from_due(self):
        store = self._store()
        now = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)
        rid = store.add("u1", "one-time", now - timedelta(minutes=5))

        self.assertEqual(len(store.get_due(now=now)), 1)
        store.mark_fired(rid)
        self.assertEqual(store.get_due(now=now), [])
        # And it's no longer pending.
        self.assertEqual(store.list_pending("u1"), [])

    def test_cancel_by_user(self):
        store = self._store()
        fire = datetime(2026, 6, 20, 3, 30, tzinfo=UTC)
        rid = store.add("owner", "secret plan", fire)

        # Wrong user cannot cancel.
        self.assertFalse(store.cancel(rid, "intruder"))
        self.assertEqual(len(store.list_pending("owner")), 1)

        # Correct user can.
        self.assertTrue(store.cancel(rid, "owner"))
        self.assertEqual(store.list_pending("owner"), [])

    def test_recurring_reminder_reschedules_after_fire(self):
        store = self._store()
        now = datetime(2026, 6, 19, 8, 0, tzinfo=UTC)
        fire = datetime(2026, 6, 19, 7, 30, tzinfo=UTC)  # already due
        rid = store.add("u1", "daily standup", fire, recurring="daily")

        self.assertEqual(len(store.get_due(now=now)), 1)
        store.mark_fired(rid)

        # Still pending (recurring), advanced by one day.
        pending = store.list_pending("u1")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["fire_at"], "2026-06-20T07:30:00Z")
        self.assertFalse(pending[0]["fired"])

        # Not due for the old time anymore.
        self.assertEqual(store.get_due(now=now), [])

    def test_persists_across_instances(self):
        store_a = self._store()
        fire = datetime(2026, 6, 20, 3, 30, tzinfo=UTC)
        rid = store_a.add("u1", "survive restart", fire)

        # A brand-new store on the same path must see the data (restart sim).
        store_b = ReminderStore(self.path)
        pending = store_b.list_pending("u1")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], rid)
        self.assertEqual(pending[0]["message"], "survive restart")


if __name__ == "__main__":
    unittest.main()
