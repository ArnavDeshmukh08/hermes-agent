"""Intent-router tests for the new email (Gmail) and crm (Orsa) routes.

Focus on the ordering guards that keep these from colliding with the existing
reminder / lead-scrape / outreach intents.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "hooks" / "jack_router"))

import jack_intent_router as router  # noqa: E402


class TestEmailIntent:
    def test_list_unread(self):
        r = router.classify("any unread email?")
        assert r is not None and r.intent == "email" and r.params["action"] == "list"

    def test_check_inbox(self):
        r = router.classify("check my inbox")
        assert r.intent == "email" and r.params["action"] == "list"

    def test_new_business_email(self):
        r = router.classify("any new business email")
        assert r.intent == "email" and r.params["action"] == "list"

    def test_draft_reply(self):
        r = router.classify("draft a reply to the email from Sarah")
        assert r.intent == "email" and r.params["action"] == "draft"

    def test_write_a_response(self):
        r = router.classify("write a response to that message")
        assert r.intent == "email" and r.params["action"] == "draft"

    def test_reminder_to_reply_stays_reminder(self):
        # "remind me to ..." must win over the email-draft pattern.
        r = router.classify("remind me to reply to the email from Sarah tomorrow")
        assert r.intent == "reminder"


class TestCrmIntent:
    def test_new_leads_this_week(self):
        r = router.classify("any new leads in Orsa this week")
        assert r is not None and r.intent == "crm"

    def test_how_many_leads(self):
        r = router.classify("how many leads do I have")
        assert r.intent == "crm"

    def test_orsa_keyword(self):
        assert router.classify("what's in Orsa").intent == "crm"

    def test_pipeline(self):
        assert router.classify("show me the pipeline").intent == "crm"

    def test_read_orsa_does_not_scrape(self):
        # "get new leads in orsa this week" has a scrape verb ("get") but names Orsa —
        # must READ the CRM, not kick off a scrape job.
        r = router.classify("get new leads in orsa this week")
        assert r.intent == "crm", f"expected crm read, got {r.intent}"

    def test_scrape_still_scrapes(self):
        # No Orsa/CRM context + scrape verb + noun → still the lead-scrape intent.
        r = router.classify("find 100 psychiatry clinics in India")
        assert r.intent == "lead"

    def test_generate_leads_in_orsa_is_readonly_notice(self):
        # Jack is read-only on Orsa — a generate/create request must be honestly refused,
        # not scraped and not answered by the chat brain.
        r = router.classify("can you generate new leads in orsa?")
        assert r.intent == "crm" and r.params["action"] == "readonly_notice"

    def test_add_leads_to_orsa_is_readonly_notice(self):
        r = router.classify("add these leads to Orsa")
        assert r.intent == "crm" and r.params["action"] == "readonly_notice"

    def test_scrape_into_orsa_is_readonly_notice(self):
        # "scrape leads into orsa" would otherwise hit the lead-scrape worker (which does
        # NOT write Orsa) — the guard must catch it first.
        r = router.classify("scrape leads into orsa")
        assert r.intent == "crm" and r.params["action"] == "readonly_notice"

    def test_read_query_not_flagged_as_write(self):
        # Read phrasing must stay a query, not trip the write guard.
        r = router.classify("any new leads in orsa this week")
        assert r.intent == "crm" and r.params["action"] == "query"


class TestOutreachUnaffected:
    def test_outreach_row_still_outreach(self):
        r = router.classify("generate outreach for row 12")
        assert r.intent == "outreach"
