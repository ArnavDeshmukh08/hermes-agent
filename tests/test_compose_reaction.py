"""Tests verifying the react-first compose prompt structure.

These tests inspect the system prompt Jack sends to the LLM, confirming:
- The react-first instruction block is present
- Few-shot examples are included
- Energy-matching instruction is present
- Fact preservation is enforced (numbers survive in the prompt)
- Prescription/fabrication guardrails intact
- Fallback behaviour unchanged
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ["HERMES_LLM_MOCK"] = "1"
os.environ.setdefault("HERMES_ROOT", str(_REPO))


class _Base(unittest.TestCase):
    def setUp(self):
        import jack_voice.compose as _m
        _m._personality_cache = None
        os.environ.pop("JACK_VOICE_ENABLED", None)

    def _capture(self, intent="health_sleep", data=None, user_message="test"):
        from jack_voice.compose import compose_reply
        if data is None:
            data = {}
        captured = {}
        def _fake(system, user, **kw):
            captured["system"] = system
            captured["user"] = user
            return "reply"
        with patch("lib.llm.complete", side_effect=_fake):
            compose_reply(intent, data, user_message, fallback_plain="fallback")
        return captured


# ---------------------------------------------------------------------------
# React-first rules present in the prompt
# ---------------------------------------------------------------------------

class TestReactFirstRulesInPrompt(_Base):
    def test_react_rules_section_present(self):
        c = self._capture()
        self.assertIn("REACT FIRST", c["system"])

    def test_lead_with_meaning_rule(self):
        c = self._capture()
        self.assertIn("LEAD WITH MEANING", c["system"])

    def test_compare_to_patterns_rule(self):
        c = self._capture()
        lower = c["system"].lower()
        self.assertIn("pattern", lower)

    def test_energy_matching_instruction_present(self):
        c = self._capture()
        lower = c["system"].lower()
        self.assertIn("energy", lower)

    def test_brief_rule_present(self):
        c = self._capture()
        # Should instruct brevity — 1-2 or 1-3 sentences
        lower = c["system"].lower()
        self.assertIn("brief", lower)

    def test_sound_human_rule_present(self):
        c = self._capture()
        lower = c["system"].lower()
        self.assertIn("human", lower)


# ---------------------------------------------------------------------------
# Few-shot examples in the prompt
# ---------------------------------------------------------------------------

class TestFewShotExamplesInPrompt(_Base):
    def test_examples_section_present(self):
        c = self._capture()
        self.assertIn("EXAMPLES", c["system"])

    def test_bad_good_contrast_present(self):
        # The examples label wrong/right answers to teach the pattern
        c = self._capture()
        self.assertIn("WRONG", c["system"])
        self.assertIn("RIGHT", c["system"])

    def test_sleep_example_present(self):
        c = self._capture()
        # The rough-night example should have restatement vs reaction contrast
        self.assertIn("Rough", c["system"])

    def test_reminder_example_present(self):
        c = self._capture()
        self.assertIn("Spandan", c["system"])

    def test_steps_example_present(self):
        c = self._capture()
        self.assertIn("rest day", c["system"].lower())

    def test_goal_example_present(self):
        c = self._capture()
        self.assertIn("marathon", c["system"].lower())


# ---------------------------------------------------------------------------
# Absolute constraints still present
# ---------------------------------------------------------------------------

class TestAbsoluteConstraints(_Base):
    def test_hard_rules_still_present(self):
        c = self._capture()
        self.assertIn("HARD RULES", c["system"])

    def test_no_fabrication_rule(self):
        c = self._capture()
        self.assertIn("fabricat", c["system"].lower())

    def test_no_medical_prescription_rule(self):
        c = self._capture()
        lower = c["system"].lower()
        has_rule = "medical" in lower or "prescrib" in lower or "diet" in lower
        self.assertTrue(has_rule)

    def test_honesty_rule_present(self):
        c = self._capture()
        lower = c["system"].lower()
        self.assertIn("honest", lower)

    def test_facts_preserved_in_prompt(self):
        c = self._capture(
            "health_sleep",
            {"total_sleep_h": 5.8, "rem_h": 0.4},
            "how did I sleep",
        )
        self.assertIn("5.8", c["system"])
        self.assertIn("0.4", c["system"])


# ---------------------------------------------------------------------------
# Fact values must reach the FACTS block (data preservation path)
# ---------------------------------------------------------------------------

class TestFactPreservation(_Base):
    def test_sleep_numbers_in_prompt(self):
        c = self._capture(
            "health_sleep",
            {"total_sleep_h": 5.8, "deep_h": 1.4, "rem_h": 0.4, "score": 62},
            "how did I sleep",
        )
        for val in ("5.8", "1.4", "0.4", "62"):
            self.assertIn(val, c["system"], f"Expected {val!r} in system prompt")

    def test_steps_in_prompt(self):
        c = self._capture("health_stats", {"steps": 191, "body_battery_high": 45}, "steps?")
        self.assertIn("191", c["system"])
        self.assertIn("45", c["system"])

    def test_reminder_facts_in_prompt(self):
        c = self._capture(
            "reminder_set",
            {"message": "call Spandan", "fire_at_ist": "9:00 PM IST"},
            "remind me to call Spandan",
        )
        self.assertIn("call Spandan", c["system"])
        self.assertIn("9:00 PM", c["system"])


# ---------------------------------------------------------------------------
# Fallback path unchanged
# ---------------------------------------------------------------------------

class TestFallbackUnchanged(_Base):
    def test_fallback_on_llm_error(self):
        from jack_voice.compose import compose_reply
        with patch("lib.llm.complete", side_effect=RuntimeError("down")):
            result = compose_reply("health_sleep", {"x": 1}, "sleep?", fallback_plain="Sleep: 5.8h")
        self.assertEqual(result, "Sleep: 5.8h")

    def test_kill_switch_bypasses_react_prompt(self):
        os.environ["JACK_VOICE_ENABLED"] = "0"
        from jack_voice.compose import compose_reply
        with patch("lib.llm.complete") as mock_llm:
            result = compose_reply("health_sleep", {}, "sleep?", fallback_plain="plain")
        mock_llm.assert_not_called()
        self.assertEqual(result, "plain")
        os.environ.pop("JACK_VOICE_ENABLED")


if __name__ == "__main__":
    unittest.main()
