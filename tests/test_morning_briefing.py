"""Tests for briefing.morning and the notifier.send_message refactor.

No real Discord, no real store, no real LLM: fakes drive everything and the
clock is injected, so every assertion is deterministic and offline.
"""

import os
import unittest
from datetime import datetime, timezone

from briefing.morning import MorningBriefing
from reminders import notifier


class FakeStore:
    """In-memory store stand-in: list_pending() returns a fixed list."""

    def __init__(self, pending):
        self._pending = list(pending)

    def list_pending(self, user_id):
        return list(self._pending)


def _reminder(message, fire_at):
    return {"id": "x", "user_id": "42", "message": message, "fire_at": fire_at, "fired": False}


def _silent_log(briefing):
    """Replace _log so tests don't touch ~/.hermes/logs."""
    briefing._log = lambda line: None  # type: ignore[method-assign]


class MorningBriefingTests(unittest.TestCase):
    def setUp(self):
        # 2026-06-19 06:00 UTC == 11:30 IST — squarely "today" in IST.
        self.now = datetime(2026, 6, 19, 6, 0, tzinfo=timezone.utc)

    def test_briefing_includes_todays_reminders(self):
        # Arrange: a reminder due today IST, and a fake complete that captures the prompt.
        store = FakeStore([_reminder("call the dentist", "2026-06-19T08:00:00Z")])
        captured = {}

        def fake_complete(system, user, **kwargs):
            captured["system"] = system
            captured["user"] = user
            return "morning Arnav"

        briefing = MorningBriefing(
            user_path="/nonexistent/USER.md", store=store, complete_fn=fake_complete
        )
        _silent_log(briefing)

        # Act
        briefing.compile_briefing("42", now=self.now)

        # Assert: the reminder text reached the LLM prompt.
        self.assertIn("call the dentist", captured["user"])

    def test_briefing_is_short(self):
        # Arrange: model returns 8 lines.
        store = FakeStore([])
        eight = "\n".join(f"line {i}" for i in range(1, 9))
        briefing = MorningBriefing(
            user_path="/nonexistent/USER.md",
            store=store,
            complete_fn=lambda s, u, **k: eight,
        )
        _silent_log(briefing)

        # Act
        out = briefing.compile_briefing("42", now=self.now)

        # Assert
        self.assertLessEqual(len([ln for ln in out.splitlines() if ln.strip()]), 10)

    def test_briefing_uses_casual_tone(self):
        # Arrange: model slips into bullets.
        store = FakeStore([])
        bulleted = "- do thing one\n* do thing two\n• do thing three"
        briefing = MorningBriefing(
            user_path="/nonexistent/USER.md",
            store=store,
            complete_fn=lambda s, u, **k: bulleted,
        )
        _silent_log(briefing)

        # Act
        out = briefing.compile_briefing("42", now=self.now)

        # Assert: no line starts with a bullet char.
        for line in out.splitlines():
            self.assertFalse(line.startswith(("- ", "* ", "• ")), line)

    def test_briefing_fires_at_correct_ist_time(self):
        # Arrange: default 09:00 IST == 03:30 UTC. now is 2026-06-19 00:00 UTC
        # (05:30 IST) so today's 09:00 IST is still ahead.
        os.environ.pop("JACK_BRIEFING_TIME_IST", None)
        briefing = MorningBriefing(
            user_path="/nonexistent/USER.md", store=FakeStore([])
        )
        now = datetime(2026, 6, 19, 0, 0, tzinfo=timezone.utc)

        # Act
        fire = briefing.next_fire(now=now)

        # Assert: 09:00 IST on 2026-06-19 == 03:30 UTC.
        self.assertEqual(fire, datetime(2026, 6, 19, 3, 30, tzinfo=timezone.utc))

    def test_briefing_survives_empty_reminders(self):
        # Arrange: no reminders at all.
        store = FakeStore([])
        briefing = MorningBriefing(
            user_path="/nonexistent/USER.md",
            store=store,
            complete_fn=lambda s, u, **k: "hey, quiet day ahead",
        )
        _silent_log(briefing)

        # Act / Assert: no crash, returns a string.
        out = briefing.compile_briefing("42", now=self.now)
        self.assertIsInstance(out, str)
        self.assertEqual(out, "hey, quiet day ahead")


class SendMessageTests(unittest.TestCase):
    """The generic notifier.send_message used by the briefing."""

    def setUp(self):
        self._orig_post = notifier._post

    def tearDown(self):
        notifier._post = self._orig_post

    def test_send_message_formatting(self):
        # Arrange
        os.environ["DISCORD_BOT_TOKEN"] = "secret-token"
        os.environ["DISCORD_HOME_CHANNEL"] = "999000"
        os.environ.pop("DISCORD_ALLOWED_USERS", None)
        calls = {}

        def fake_post(url, data, headers):
            import json

            calls["url"] = url
            calls["headers"] = headers
            calls["body"] = json.loads(data.decode("utf-8"))

        notifier._post = fake_post

        # Act: raw content, explicit user_id.
        ok = notifier.send_message("good morning Arnav", user_id="42")

        # Assert: posts raw content (mention-prefixed) to the channel URL with Bot auth.
        self.assertTrue(ok)
        self.assertEqual(
            calls["url"], "https://discord.com/api/v10/channels/999000/messages"
        )
        self.assertEqual(calls["headers"]["Authorization"], "Bot secret-token")
        self.assertEqual(calls["body"]["content"], "<@42> good morning Arnav")


if __name__ == "__main__":
    unittest.main()
