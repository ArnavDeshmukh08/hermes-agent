"""Regression for the Cloudflare-1010 delivery outage: Discord requires a
User-Agent header, and without it every reminder/briefing POST was 403-blocked
(`notify returned False`). This pins that the notifier always sends a bot UA.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reminders import notifier  # noqa: E402


class UserAgentTest(unittest.TestCase):
    def setUp(self):
        self._orig_post = notifier._post
        self.captured = {}

        def fake_post(url, data, headers):
            self.captured = {"url": url, "headers": headers}

        notifier._post = fake_post
        import os

        os.environ["DISCORD_BOT_TOKEN"] = "test-token"
        os.environ["DISCORD_HOME_CHANNEL"] = "123"

    def tearDown(self):
        notifier._post = self._orig_post

    def test_send_message_includes_discord_user_agent(self):
        ok = notifier.send_message("hi")
        self.assertTrue(ok)
        ua = self.captured["headers"].get("User-Agent", "")
        self.assertIn("DiscordBot", ua)  # without this, Cloudflare 403s with code 1010

    def test_send_reminder_also_has_user_agent(self):
        notifier.send_reminder("call mom")
        self.assertIn("User-Agent", self.captured["headers"])


if __name__ == "__main__":
    unittest.main()
