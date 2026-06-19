"""Regression tests for the complaint-vs-reminder bug.

A feedback sentence like "You didn't remind me this morning about calling my mom"
contains the literal "remind me" trigger, so the reminder classifier used to fire
on it and set a bogus reminder from the complaint text. These tests lock in the
fix: complaints are classified BEFORE reminders, and genuine reminder commands
still work.
"""

import sys
import unittest
from pathlib import Path

_HOOK_DIR = Path(__file__).resolve().parents[1] / "hooks" / "jack_router"
sys.path.insert(0, str(_HOOK_DIR))

import jack_intent_router as router  # noqa: E402
import handler  # noqa: E402


class ComplaintGuardTest(unittest.TestCase):
    def test_complaint_not_treated_as_reminder(self):
        # The bug: "didn't remind me" matches _REMINDER_SET_RE. Must be a complaint.
        r = router.classify("You didn't remind me this morning about calling my mom")
        self.assertEqual(r.intent, "complaint")
        self.assertNotEqual(r.intent, "reminder")

    def test_reminder_command_triggers_correctly(self):
        r = router.classify("remind me to call mom at 9am tomorrow")
        self.assertEqual(r.intent, "reminder")
        self.assertEqual(r.params["action"], "set")

    def test_why_question_not_reminder(self):
        r = router.classify("why did you ask for the agenda")
        self.assertEqual(r.intent, "conversational")

    def test_you_forgot_triggers_complaint(self):
        r = router.classify("you forgot to remind me")
        self.assertEqual(r.intent, "complaint")

    def test_reminder_text_extracted_cleanly(self):
        self.assertEqual(handler._reminder_message("remind me to call mom at 9am"), "call mom")

    def test_genuine_reminder_still_works_after_complaint_guard(self):
        # The complaint guard must not over-eat real set commands.
        r = router.classify("remind me to buy milk in 2 hours")
        self.assertEqual(r.intent, "reminder")
        self.assertEqual(r.params["action"], "set")


if __name__ == "__main__":
    unittest.main()
