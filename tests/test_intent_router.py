"""Tests for the deterministic intent classifier (no LLM, no network)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks" / "jack_router"))

import jack_intent_router as router  # noqa: E402


class ClassifyTest(unittest.TestCase):
    def test_lead_with_count_and_location(self):
        r = router.classify("find 100 physiotherapy clinics in India")
        self.assertEqual(r.intent, "lead")
        self.assertEqual(r.params["count"], 100)
        self.assertIn("physiotherapy", r.params["target"])
        self.assertEqual(r.params["location"], "India")

    def test_lead_no_count_defaults(self):
        r = router.classify("find dentists in Delhi")
        self.assertEqual(r.intent, "lead")
        self.assertEqual(r.params["count"], router.DEFAULT_COUNT)
        self.assertEqual(r.params["target"], "dentists")
        self.assertEqual(r.params["location"], "Delhi")

    def test_lead_scrape_verb(self):
        r = router.classify("scrape physiotherapy clinics in Mumbai")
        self.assertEqual(r.intent, "lead")
        self.assertEqual(r.params["location"], "Mumbai")

    def test_count_capped_at_max(self):
        r = router.classify("find 99999 clinics in Pune")
        self.assertEqual(r.params["count"], router.MAX_COUNT)

    def test_status_variants(self):
        for s in ("status", "queue", "running tasks", "what's running", "jobs"):
            self.assertEqual(router.classify(s).intent, "status", s)

    def test_conversational_falls_through(self):
        for s in (
            "what do you think about AI agents?",
            "hello",
            "how are you",
            "find a good restaurant near me",  # 'restaurant' is not a lead noun
            "can you research that topic for me",  # 'research' verb but no target noun
        ):
            self.assertIsNone(router.classify(s), s)

    def test_empty(self):
        self.assertIsNone(router.classify(""))
        self.assertIsNone(router.classify("   "))

    def test_leads_keyword_without_location(self):
        r = router.classify("find leads")
        self.assertEqual(r.intent, "lead")
        self.assertIsNone(r.params["location"])

    def test_research_companies(self):
        r = router.classify("research companies in Bangalore")
        self.assertEqual(r.intent, "lead")
        self.assertEqual(r.params["location"], "Bangalore")

    def test_outreach_with_row(self):
        r = router.classify("generate outreach for row 12")
        self.assertEqual(r.intent, "outreach")
        self.assertEqual(r.params["row"], 12)

    def test_outreach_variants(self):
        self.assertEqual(router.classify("draft a pitch for row 3").intent, "outreach")
        self.assertEqual(router.classify("create outreach for row 5").intent, "outreach")
        self.assertEqual(router.classify("generate a cold email for row 8").intent, "outreach")

    def test_outreach_no_false_positive_on_plain_message(self):
        # ordinary conversational requests must NOT be stolen by the outreach intent
        for s in ("draft a message to my team", "write a message", "can you make an email signature"):
            self.assertIsNone(router.classify(s), s)

    def test_outreach_does_not_eat_lead(self):
        # "find" lead requests must not be misread as outreach
        self.assertEqual(router.classify("find 10 clinics in Pune").intent, "lead")


if __name__ == "__main__":
    unittest.main()
