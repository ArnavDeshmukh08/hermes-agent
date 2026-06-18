"""Tests for the Phase-6 founder brain-dump capture layer (offline/deterministic)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import helpers  # noqa: E402
from lib import knowledge, store  # noqa: E402

OPINION = "I think clinics massively undervalue patient retention."
OBJECTION = "A doctor told me WhatsApp feels spammy and patients won't like it."
INSIGHT = "Noticed our no-show rate was 22% last month and we recovered 18 patients."
NOISE = "Reminder to buy groceries tomorrow."


class CaptureTest(helpers.HermesTestCase):
    def test_capture_appends_and_dedups(self):
        e = knowledge.record_braindump("m1", OPINION)
        self.assertIsNotNone(e)
        log = store.read_jsonl(knowledge.knowledge_root() / knowledge.BRAINDUMP_LOG)
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["text"], OPINION)
        # idempotent on message_id
        self.assertIsNone(knowledge.record_braindump("m1", "different text"))
        self.assertEqual(len(store.read_jsonl(knowledge.knowledge_root() / knowledge.BRAINDUMP_LOG)), 1)

    def test_blank_ignored(self):
        self.assertIsNone(knowledge.record_braindump("m2", "   "))


class ParseTest(helpers.HermesTestCase):
    def _seed(self):
        knowledge.record_braindump("o1", OPINION, ts="2026-06-17T09:00:00+05:30")
        knowledge.record_braindump("j1", OBJECTION, ts="2026-06-17T09:01:00+05:30")
        knowledge.record_braindump("i1", INSIGHT, ts="2026-06-17T09:02:00+05:30")
        knowledge.record_braindump("n1", NOISE, ts="2026-06-17T09:03:00+05:30")

    def test_classifies_and_routes(self):
        self._seed()
        summary = knowledge.parse_new()
        self.assertEqual(summary["processed_messages"], 4)
        self.assertGreaterEqual(summary["by_type"]["opinion"], 1)
        self.assertGreaterEqual(summary["by_type"]["objection"], 1)
        self.assertGreaterEqual(summary["by_type"]["insight"], 1)
        root = knowledge.knowledge_root()
        self.assertTrue(any(root.joinpath("opinions").glob("*.json")))
        self.assertTrue(any(root.joinpath("objections").glob("*.json")))
        self.assertTrue(any(root.joinpath("insights").glob("*.json")))

    def test_provenance_complete(self):
        self._seed()
        knowledge.parse_new()
        recs = list(knowledge._iter_items())
        self.assertTrue(recs)
        for r in recs:
            p = r["provenance"]
            self.assertEqual(p["kind"], "brain_dump")
            self.assertTrue(p["source"])                       # source message
            self.assertIn(p["source_message_id"], {"o1", "j1", "i1", "n1"})  # discord id
            self.assertTrue(p["timestamp"])                    # message timestamp
            self.assertIsInstance(p["confidence"], (int, float))  # extraction confidence
            self.assertTrue(0.0 <= p["confidence"] <= 1.0)
        # the insight carries the number signal
        ins = [r for r in recs if r["type"] == "insight"]
        self.assertTrue(any("22%" in r["body"] for r in ins))

    def test_noise_is_needs_review(self):
        self._seed()
        knowledge.parse_new()
        review = list((knowledge.knowledge_root() / knowledge.REVIEW_DIR).glob("*.json"))
        self.assertTrue(review)
        rec = store.read_json(review[0])
        self.assertEqual(rec["type"], "other")
        self.assertEqual(rec["status"], "needs_review")

    def test_idempotent_parse(self):
        self._seed()
        s1 = knowledge.parse_new()
        s2 = knowledge.parse_new()
        self.assertEqual(s2["processed_messages"], 0)
        self.assertEqual(s2["items_created"], 0)
        # no duplicates created on the second run
        self.assertEqual(s1["items_created"], len(list(knowledge._iter_items())))


class ReportTest(helpers.HermesTestCase):
    def test_morning_report(self):
        knowledge.record_braindump("o1", OPINION)
        knowledge.record_braindump("j1", OBJECTION)
        knowledge.record_braindump("i1", INSIGHT)
        knowledge.record_braindump("n1", NOISE)
        knowledge.parse_new()
        report = knowledge.morning_report()
        self.assertIn("New opinions", report)
        self.assertIn("New insights", report)
        self.assertIn("New objections", report)
        self.assertIn("Low-confidence", report)


class CliTest(helpers.HermesTestCase):
    def test_capture_parse_report_cli(self):
        cap = {"message_id": "c1", "text": INSIGHT, "ts": "2026-06-17T10:00:00+05:30"}
        mode, r = self.run_bin("brain", ["--capture", json.dumps(cap)])
        out = r.stdout if mode == "subprocess" else None
        # parse
        mode, r = self.run_bin("brain", ["--parse"])
        # report
        mode, r = self.run_bin("brain", ["--report"])
        # an insight file now exists with full provenance
        ins = list((knowledge.knowledge_root() / "insights").glob("*.json"))
        self.assertTrue(ins)
        rec = store.read_json(ins[0])
        self.assertEqual(rec["provenance"]["source_message_id"], "c1")
        self.assertTrue(rec["provenance"]["source"])
