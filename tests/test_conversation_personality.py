"""Tests for the updated conversation.py personality and thread-tracking.

All tests are offline — no LLM calls, no network. JackConversationHandler is
instantiated with nonexistent soul/user paths to force fallback personality,
making assertions deterministic regardless of VPS file contents.

Tests verify:
- System prompt contains react-first rules + energy-matching (shared persona)
- System prompt contains chat-specific few-shot examples (incl. the 'oh' case)
- System prompt contains thread-reading guidance
- System prompt forbids fabrication and prescriptions
- Recent exchange (prior Jack reply) appears in the user block
- A terse 'oh' after a prior bad-news reply is contextualised (not orphaned)
- History is bounded (does not grow unboundedly)
- Graceful when no history exists (first message)
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO), str(_REPO / "hooks" / "jack_router")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["HERMES_LLM_MOCK"] = "1"
os.environ.setdefault("HERMES_ROOT", str(_REPO))

_NO_PATH = Path("/nonexistent")


def _handler(**kw):
    from conversation import JackConversationHandler
    return JackConversationHandler(soul_path=_NO_PATH, user_path=_NO_PATH, **kw)


# ---------------------------------------------------------------------------
# System prompt content — shared persona present
# ---------------------------------------------------------------------------

class TestConvSystemPromptPersona(unittest.TestCase):
    def setUp(self):
        self._h = _handler()
        self._sys, _ = self._h.build_prompt("hey", "u1")

    def test_react_first_rules_present(self):
        self.assertIn("REACT FIRST", self._sys)

    def test_lead_with_meaning_present(self):
        self.assertIn("LEAD WITH MEANING", self._sys)

    def test_energy_matching_present(self):
        lower = self._sys.lower()
        self.assertIn("energy", lower)

    def test_brief_rule_present(self):
        lower = self._sys.lower()
        self.assertIn("brief", lower)

    def test_sound_human_present(self):
        lower = self._sys.lower()
        self.assertIn("human", lower)

    def test_hard_rules_header_present(self):
        self.assertIn("HARD RULES", self._sys)

    def test_no_fabrication_rule_present(self):
        self.assertIn("fabricat", self._sys.lower())

    def test_no_prescription_rule_present(self):
        lower = self._sys.lower()
        has_rule = "prescri" in lower or "medical" in lower or "diet" in lower
        self.assertTrue(has_rule)

    def test_honesty_rule_present(self):
        self.assertIn("HONESTY", self._sys)


# ---------------------------------------------------------------------------
# Chat-specific few-shot examples
# ---------------------------------------------------------------------------

class TestConvChatFewShots(unittest.TestCase):
    def setUp(self):
        self._h = _handler()
        self._sys, _ = self._h.build_prompt("hey", "u1")

    def test_examples_section_present(self):
        self.assertIn("EXAMPLES", self._sys)

    def test_deflated_oh_example_present(self):
        # The critical case: 'oh' after bad news must have a WRONG/RIGHT contrast
        self.assertIn("'oh'", self._sys)
        self.assertIn("WRONG", self._sys)
        self.assertIn("RIGHT", self._sys)

    def test_wrong_answer_for_oh_shown(self):
        # Model must see the bad pattern explicitly
        self.assertIn("What's on your mind", self._sys)

    def test_right_answer_for_oh_shown(self):
        # The correct react-to-the-beat pattern
        lower = self._sys.lower()
        self.assertIn("rough", lower)

    def test_casual_opener_example_present(self):
        # 'hey' should not get 'What's the agenda for today?'
        self.assertIn("agenda", self._sys)

    def test_one_word_positive_example_present(self):
        # 'nice' → 'Sorted.' not a customer-service reply
        lower = self._sys.lower()
        self.assertIn("nice", lower)


# ---------------------------------------------------------------------------
# Thread-reading guidance
# ---------------------------------------------------------------------------

class TestConvThreadGuidance(unittest.TestCase):
    def setUp(self):
        self._h = _handler()
        self._sys, _ = self._h.build_prompt("hey", "u1")

    def test_thread_guidance_section_present(self):
        self.assertIn("conversation thread", self._sys.lower())

    def test_terse_followup_instruction_present(self):
        lower = self._sys.lower()
        has_terse = "terse" in lower or "subdued" in lower or "emotionally" in lower
        self.assertTrue(has_terse)

    def test_no_topic_reset_instruction(self):
        # Must explicitly ban "What's on your mind?"
        self.assertIn("What's on your mind", self._sys)  # appears as the banned phrase
        lower = self._sys.lower()
        # And must say NOT to do it
        self.assertIn("do not", lower)

    def test_emotional_beat_instruction(self):
        lower = self._sys.lower()
        self.assertIn("emotional beat", lower)


# ---------------------------------------------------------------------------
# History threading — prior turn appears in user block
# ---------------------------------------------------------------------------

class TestConvHistoryThread(unittest.TestCase):
    def test_first_message_no_history(self):
        """First message: no crash, no prior history in user block."""
        h = _handler()
        _, user_block = h.build_prompt("hey", "u1")
        self.assertIn("Arnav: hey", user_block)
        self.assertIn("Jack:", user_block)

    def test_prior_jack_reply_in_user_block(self):
        """After one exchange, Jack's prior reply appears in the next user block."""
        h = _handler()
        h.add_turn("u1", "user", "how did I sleep")
        h.add_turn("u1", "assistant", "Rough night — 5.8h, not your best rest.")
        _, user_block = h.build_prompt("oh", "u1")
        self.assertIn("Rough night", user_block)
        self.assertIn("oh", user_block)

    def test_terse_oh_contextualised_by_prior_reply(self):
        """'oh' appears in context of Jack's bad-news prior reply — not orphaned."""
        h = _handler()
        h.add_turn("u1", "user", "how did I sleep")
        h.add_turn("u1", "assistant", "Rough night — 5.8h, not your best rest.")
        _, user_block = h.build_prompt("oh", "u1")
        # Both the prior context and the terse reaction must be in the user block
        self.assertIn("5.8h", user_block)
        self.assertIn("Arnav: oh", user_block)

    def test_history_bounded(self):
        """History is capped — adding many turns does not grow the session unboundedly."""
        h = _handler(max_turns=3)
        for i in range(20):
            h.add_turn("u1", "user", f"msg {i}")
            h.add_turn("u1", "assistant", f"reply {i}")
        session = h.get_context("u1")
        # max_turns=3 means at most 6 messages (3 exchanges × 2)
        self.assertLessEqual(len(session), 6)

    def test_history_contains_most_recent_exchange(self):
        """After capping, the MOST RECENT messages survive (not the oldest)."""
        h = _handler(max_turns=2)
        for i in range(10):
            h.add_turn("u1", "user", f"msg {i}")
            h.add_turn("u1", "assistant", f"reply {i}")
        session = h.get_context("u1")
        # The last messages must be from the final turns (8 and 9)
        contents = [m["content"] for m in session]
        self.assertIn("msg 9", contents)
        self.assertIn("reply 9", contents)
        # Older messages should be gone
        self.assertNotIn("msg 0", contents)


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

class TestConvGracefulDegradation(unittest.TestCase):
    def test_no_crash_on_nonexistent_soul(self):
        """Falls back to _FALLBACK_PERSONALITY — never raises."""
        h = _handler()  # soul_path=/nonexistent
        system, user_block = h.build_prompt("hello", "u1")
        self.assertIn("Jack", system)

    def test_no_crash_on_nonexistent_user_profile(self):
        h = _handler()  # user_path=/nonexistent
        system, _ = h.build_prompt("hello", "u1")
        # Profile missing — system should still be built
        self.assertIsInstance(system, str)
        self.assertTrue(len(system) > 10)


if __name__ == "__main__":
    unittest.main()
