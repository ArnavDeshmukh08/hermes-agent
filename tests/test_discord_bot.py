"""Tests for bin.discord_bot — the Discord transport layer (offline/dry-run).

Covers: fail-closed auth (user allowlist AND channel lock), custom_id parsing,
dry-run posting + enqueue, and the interaction approve/reject/unauthorized/
wrong-channel/idempotency paths — i.e. every guardrail that moved from Telegram.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import helpers  # noqa: E402
from bin import discord_bot as bot  # noqa: E402
from lib import store  # noqa: E402


class DiscordAuthTest(helpers.HermesTestCase):
    def test_fail_closed_empty_allowlist(self):
        os.environ["DISCORD_ALLOWED_USERS"] = ""
        self.assertFalse(bot.is_authorized(42, helpers.APPROVAL_CHANNEL_ID))

    def test_wrong_user_denied(self):
        self.assertFalse(bot.is_authorized(999, helpers.APPROVAL_CHANNEL_ID))

    def test_wrong_channel_denied(self):
        self.assertFalse(bot.is_authorized(42, helpers.WRONG_CHANNEL_ID))

    def test_unset_channel_denied(self):
        os.environ["DISCORD_APPROVAL_CHANNEL_ID"] = ""
        self.assertFalse(bot.is_authorized(42, helpers.APPROVAL_CHANNEL_ID))

    def test_allowed_user_in_channel_ok(self):
        self.assertTrue(bot.is_authorized(42, helpers.APPROVAL_CHANNEL_ID))

    def test_parse_custom_id(self):
        self.assertEqual(bot.parse_custom_id("apr:xyz:0:a"), ("xyz", 0, "approve"))
        self.assertEqual(bot.parse_custom_id("apr:xyz:1:r"), ("xyz", 1, "reject"))
        self.assertIsNone(bot.parse_custom_id("nope:xyz:0:a"))
        self.assertIsNone(bot.parse_custom_id("apr:xyz:0:z"))
        self.assertIsNone(bot.parse_custom_id("apr:xyz:notint:a"))

    def test_channel_is_private_helper(self):
        self.assertTrue(bot.channel_is_private(["1", "2"], allowed_ids=["1", "2", "3"]))
        self.assertFalse(bot.channel_is_private(["1", "9"], allowed_ids=["1", "2"]))
        self.assertFalse(bot.channel_is_private(["1"], allowed_ids=[]))  # fail-closed


class DiscordPostTest(helpers.HermesTestCase):
    def _seed_draft(self, content_id="20260617-1001-noshow"):
        helpers.write_draft_fixture(self.memory_root,
                                    helpers.make_pending_draft(content_id=content_id))
        return content_id

    def test_post_pending_once_enqueues(self):
        cid = self._seed_draft()
        posted = bot.post_pending_once(dry_run=True)
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["content_id"], cid)
        self.assertTrue(posted[0]["nonce"])
        item = store.find_pending(cid)
        self.assertIsNotNone(item)
        self.assertEqual(str(item["channel_id"]), str(helpers.APPROVAL_CHANNEL_ID))
        self.assertIn("discord_message_id", item)
        draft = store.load_draft(cid)
        self.assertEqual(draft["approval"]["transport"], "discord")
        self.assertIsNotNone(draft["approval"]["dispatched_at"])

    def test_post_skips_already_dispatched(self):
        self._seed_draft()
        bot.post_pending_once(dry_run=True)
        again = bot.post_pending_once(dry_run=True)
        self.assertEqual(again, [])


class DiscordInteractionTest(helpers.HermesTestCase):
    def _post(self, content_id="20260617-1002-noshow"):
        helpers.write_draft_fixture(self.memory_root,
                                    helpers.make_pending_draft(content_id=content_id))
        posted = bot.post_pending_once(dry_run=True)
        return content_id, posted[0]["nonce"]

    def test_approve_end_to_end(self):
        cid, nonce = self._post()
        res = bot.handle_interaction(helpers.interaction(nonce, 0, "a"))
        self.assertTrue(res["ok"])
        appr = self.approved_dir() / f"{cid}.md"
        self.assertTrue(appr.exists())
        decs = self.read_decisions()
        self.assertEqual([d["action"] for d in decs], ["approve"])
        self.assertEqual(decs[0]["decided_by"], "discord:42")
        self.assertEqual(store.load_draft(cid)["status"], "approved")

    def test_reject(self):
        cid, nonce = self._post()
        res = bot.handle_interaction(helpers.interaction(nonce, 0, "r"))
        self.assertTrue(res["ok"])
        self.assertFalse((self.approved_dir() / f"{cid}.md").exists())
        self.assertEqual(store.load_draft(cid)["status"], "rejected")

    def test_unauthorized_user_writes_nothing(self):
        cid, nonce = self._post()
        res = bot.handle_interaction(
            helpers.interaction(nonce, 0, "a", user_id=helpers.UNAUTHORIZED_USER_ID))
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "unauthorized")
        self.assertFalse((self.approved_dir() / f"{cid}.md").exists())
        self.assertEqual(self.read_decisions(), [])
        self.assertEqual(store.load_draft(cid)["status"], "pending")

    def test_wrong_channel_writes_nothing(self):
        cid, nonce = self._post()
        res = bot.handle_interaction(
            helpers.interaction(nonce, 0, "a", channel_id=helpers.WRONG_CHANNEL_ID))
        self.assertFalse(res["ok"])
        self.assertFalse((self.approved_dir() / f"{cid}.md").exists())
        self.assertEqual(self.read_decisions(), [])

    def test_double_click_idempotent(self):
        cid, nonce = self._post()
        bot.handle_interaction(helpers.interaction(nonce, 0, "a"))
        res2 = bot.handle_interaction(helpers.interaction(nonce, 0, "a"))
        self.assertEqual(res2.get("reason"), "already_resolved")
        self.assertEqual(len(self.read_decisions()), 1)
