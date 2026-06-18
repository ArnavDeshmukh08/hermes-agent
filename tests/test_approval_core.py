"""Transport-free tests for lib.approval_core (the shared decision core).

Proves the guardrails independent of any transport: write-ahead decision, the
single human gate (only approve writes approved/), idempotency, and the
revise two-stage flow with status preservation.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import helpers  # noqa: E402
from lib import store, approval_core  # noqa: E402

NONCE = "test-nonce-abc"
DECIDER = "discord:42"


class ApprovalCoreTest(helpers.HermesTestCase):
    def _seed_pending(self, content_id="20260617-1000-noshow"):
        draft = helpers.make_pending_draft(content_id=content_id)
        helpers.write_draft_fixture(self.memory_root, draft)
        store.enqueue({
            "content_id": content_id, "variant_idx": 0, "nonce": NONCE,
            "state": "sent", "channel_id": "777", "discord_message_id": 123,
            "sent_at": "2026-06-17T10:00:00+05:30",
        })
        return content_id

    def test_approve_writes_approved_and_ledger(self):
        cid = self._seed_pending()
        res = approval_core.decide(NONCE, 0, "approve", decided_by=DECIDER)
        self.assertTrue(res["ok"])
        self.assertEqual(res["action"], "approve")
        # single human gate: approved/<id>.md exists with the chosen variant body
        appr = self.approved_dir() / f"{cid}.md"
        self.assertTrue(appr.exists())
        self.assertIn("Variant 0", appr.read_text(encoding="utf-8"))
        # write-ahead ledger line, status, dequeued
        decs = self.read_decisions()
        self.assertEqual([d["action"] for d in decs], ["approve"])
        self.assertEqual(decs[0]["decided_by"], DECIDER)
        self.assertEqual(decs[0]["note"], "")  # HIGH-fix: note present as ""
        self.assertEqual(store.load_draft(cid)["status"], "approved")
        self.assertEqual(self.read_queue()["pending"], [])

    def test_reject_writes_nothing_to_approved(self):
        cid = self._seed_pending()
        res = approval_core.decide(NONCE, 0, "reject", decided_by=DECIDER)
        self.assertTrue(res["ok"])
        self.assertFalse((self.approved_dir() / f"{cid}.md").exists())
        self.assertEqual(store.load_draft(cid)["status"], "rejected")
        self.assertEqual([d["action"] for d in self.read_decisions()], ["reject"])

    def test_idempotent_double_approve(self):
        cid = self._seed_pending()
        approval_core.decide(NONCE, 0, "approve", decided_by=DECIDER)
        res2 = approval_core.decide(NONCE, 0, "approve", decided_by=DECIDER)
        self.assertEqual(res2.get("reason"), "already_resolved")
        self.assertEqual(len([d for d in self.read_decisions() if d["action"] == "approve"]), 1)

    def test_unknown_nonce_is_noop(self):
        self._seed_pending()
        res = approval_core.decide("not-a-real-nonce", 0, "approve", decided_by=DECIDER)
        self.assertEqual(res.get("reason"), "already_resolved")
        self.assertEqual(self.read_decisions(), [])

    def test_revise_two_stage(self):
        cid = self._seed_pending()
        r1 = approval_core.decide(NONCE, 0, "revise", decided_by=DECIDER)
        self.assertTrue(r1["ok"])
        # parked: status revise, still queued, awaiting note, no decision line yet
        self.assertEqual(store.load_draft(cid)["status"], "revise")
        self.assertEqual(self.read_decisions(), [])
        item = store.find_pending(cid)
        self.assertEqual(item["state"], "awaiting_revise_note")
        # the note arrives (modal)
        r2 = approval_core.record_revise(cid, "make the hook punchier", decided_by=DECIDER)
        self.assertTrue(r2["ok"])
        decs = self.read_decisions()
        self.assertEqual([d["action"] for d in decs], ["revise"])
        self.assertEqual(decs[0]["note"], "make the hook punchier")
        draft = store.load_draft(cid)
        self.assertEqual(draft["status"], "revise")              # HIGH-fix held
        self.assertEqual(draft["approval"]["revise_count"], 1)
        self.assertIsNone(store.find_pending(cid))               # dequeued
