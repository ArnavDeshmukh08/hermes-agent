"""Tests for reminders.scheduler and reminders.notifier.

No real Discord, no real store: a FakeStore + fake notifier drive the scheduler,
and notifier._post is monkeypatched to capture the payload instead of hitting the
network.
"""

import os
import unittest
from datetime import datetime, timezone

from reminders import notifier
from reminders.scheduler import ReminderScheduler


class FakeStore:
    """In-memory store stand-in: get_due() / mark_fired() over a list."""

    def __init__(self, due):
        self._due = list(due)
        self.fired_ids = []

    def get_due(self, now):
        return list(self._due)

    def mark_fired(self, reminder_id):
        self.fired_ids.append(reminder_id)


def _silent_logger(_line):
    """Swallow log output so tests don't touch ~/.hermes/logs."""


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)

    def test_due_reminder_gets_fired(self):
        # Arrange
        captured = []

        def fake_notifier(message, *, user_id=None):
            captured.append((message, user_id))
            return True

        store = FakeStore([{"id": "r1", "message": "drink water", "user_id": "42"}])
        scheduler = ReminderScheduler(store, notifier=fake_notifier, logger=_silent_logger)

        # Act
        fired = scheduler.run_once(now=self.now)

        # Assert
        self.assertEqual(fired, 1)
        self.assertEqual(captured, [("drink water", "42")])

    def test_fired_reminder_marked_complete(self):
        # Arrange
        store = FakeStore([{"id": "r1", "message": "standup", "user_id": "42"}])
        scheduler = ReminderScheduler(
            store, notifier=lambda m, *, user_id=None: True, logger=_silent_logger
        )

        # Act
        scheduler.run_once(now=self.now)

        # Assert
        self.assertEqual(store.fired_ids, ["r1"])

    def test_recurring_reminder_reschedules(self):
        # A recurring reminder still goes through mark_fired (the store owns the
        # reschedule math); assert the fake recorded the mark_fired call.
        # Arrange
        store = FakeStore(
            [{"id": "daily-1", "message": "take meds", "user_id": "42", "recurring": "daily"}]
        )
        scheduler = ReminderScheduler(
            store, notifier=lambda m, *, user_id=None: True, logger=_silent_logger
        )

        # Act
        fired = scheduler.run_once(now=self.now)

        # Assert
        self.assertEqual(fired, 1)
        self.assertIn("daily-1", store.fired_ids)

    def test_scheduler_survives_notifier_failure(self):
        # Arrange: one notifier raises, one returns False — neither aborts the batch
        # and neither is marked fired; a third succeeds.
        def flaky_notifier(message, *, user_id=None):
            if message == "boom":
                raise RuntimeError("network down")
            if message == "soft-fail":
                return False
            return True

        store = FakeStore(
            [
                {"id": "bad", "message": "boom", "user_id": "42"},
                {"id": "soft", "message": "soft-fail", "user_id": "42"},
                {"id": "good", "message": "ok", "user_id": "42"},
            ]
        )
        scheduler = ReminderScheduler(store, notifier=flaky_notifier, logger=_silent_logger)

        # Act
        fired = scheduler.run_once(now=self.now)  # must not raise

        # Assert: only the successful one fired; failed ones left pending.
        self.assertEqual(fired, 1)
        self.assertEqual(store.fired_ids, ["good"])

    def test_poll_seconds_from_env(self):
        # Arrange
        prev = os.environ.get("JACK_REMINDER_POLL_SECONDS")
        os.environ["JACK_REMINDER_POLL_SECONDS"] = "5"
        try:
            scheduler = ReminderScheduler(FakeStore([]), logger=_silent_logger)
            # Assert
            self.assertEqual(scheduler._poll_seconds, 5)
        finally:
            if prev is None:
                os.environ.pop("JACK_REMINDER_POLL_SECONDS", None)
            else:
                os.environ["JACK_REMINDER_POLL_SECONDS"] = prev


class NotifierTests(unittest.TestCase):
    def setUp(self):
        self._saved = {
            k: os.environ.get(k)
            for k in ("DISCORD_BOT_TOKEN", "DISCORD_HOME_CHANNEL", "DISCORD_ALLOWED_USERS")
        }
        self._orig_post = notifier._post

    def tearDown(self):
        notifier._post = self._orig_post
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_message_formatting_and_request_shape(self):
        # Arrange
        os.environ["DISCORD_BOT_TOKEN"] = "secret-token"
        os.environ["DISCORD_HOME_CHANNEL"] = "999000"
        os.environ["DISCORD_ALLOWED_USERS"] = "42, 7"
        calls = {}

        def fake_post(url, data, headers):
            import json

            calls["url"] = url
            calls["headers"] = headers
            calls["body"] = json.loads(data.decode("utf-8"))

        notifier._post = fake_post

        # Act
        ok = notifier.send_reminder("call mom")

        # Assert
        self.assertTrue(ok)
        self.assertEqual(
            calls["url"], "https://discord.com/api/v10/channels/999000/messages"
        )
        self.assertEqual(calls["headers"]["Authorization"], "Bot secret-token")
        self.assertEqual(calls["headers"]["Content-Type"], "application/json")
        # First allowed user is @mentioned.
        self.assertEqual(
            calls["body"]["content"], "<@42> ⏰ Hey Arnav — reminder: call mom"
        )

    def test_explicit_user_id_overrides_default(self):
        os.environ["DISCORD_BOT_TOKEN"] = "tok"
        os.environ["DISCORD_HOME_CHANNEL"] = "1"
        os.environ["DISCORD_ALLOWED_USERS"] = "42"
        calls = {}

        def fake_post(url, data, headers):
            import json

            calls["body"] = json.loads(data.decode("utf-8"))

        notifier._post = fake_post
        notifier.send_reminder("ping", user_id="99")
        self.assertTrue(calls["body"]["content"].startswith("<@99>"))

    def test_missing_creds_is_dry_run(self):
        # Arrange
        os.environ.pop("DISCORD_BOT_TOKEN", None)
        os.environ.pop("DISCORD_HOME_CHANNEL", None)

        def boom(*_a, **_k):
            raise AssertionError("_post must not be called without creds")

        notifier._post = boom

        # Act / Assert — returns False, does not raise.
        self.assertFalse(notifier.send_reminder("anything"))

    def test_retries_once_then_returns_false(self):
        os.environ["DISCORD_BOT_TOKEN"] = "tok"
        os.environ["DISCORD_HOME_CHANNEL"] = "1"
        os.environ.pop("DISCORD_ALLOWED_USERS", None)
        attempts = {"n": 0}

        def always_fail(url, data, headers):
            attempts["n"] += 1
            raise OSError("nope")

        notifier._post = always_fail

        ok = notifier.send_reminder("x")
        self.assertFalse(ok)
        self.assertEqual(attempts["n"], 2)  # initial + one retry

    def test_succeeds_on_retry(self):
        os.environ["DISCORD_BOT_TOKEN"] = "tok"
        os.environ["DISCORD_HOME_CHANNEL"] = "1"
        attempts = {"n": 0}

        def fail_then_ok(url, data, headers):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise OSError("transient")

        notifier._post = fail_then_ok
        self.assertTrue(notifier.send_reminder("x"))
        self.assertEqual(attempts["n"], 2)


if __name__ == "__main__":
    unittest.main()
