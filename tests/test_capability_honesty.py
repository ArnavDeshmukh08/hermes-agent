"""Tests that _CAPABILITIES_GUIDANCE is always present in the system prompt
and contains the correct anti-hallucination language.

No network or LLM calls are made — only prompt-assembly logic is exercised.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks" / "jack_router"))

from conversation import JackConversationHandler  # noqa: E402

_SOUL = "You are Jack, Chief of Staff for Arnav Deshmukh, a technical founder.\n"
_USER = "# User Profile\n- Identity: AIML student and technical founder.\n"


def _handler(soul_text: str = _SOUL, user_text: str = _USER) -> JackConversationHandler:
    """Return a handler backed by temporary fixture files."""
    d = tempfile.mkdtemp()
    soul = Path(d) / "SOUL.md"
    user = Path(d) / "USER.md"
    soul.write_text(soul_text, encoding="utf-8")
    user.write_text(user_text, encoding="utf-8")
    return JackConversationHandler(soul_path=soul, user_path=user)


class CapabilityHonestyTest(unittest.TestCase):

    def _system(self, **kw) -> str:
        h = _handler(**kw)
        system, _ = h.build_prompt("test", "user1")
        return system

    def test_system_prompt_contains_capabilities(self):
        system = self._system()
        self.assertIn("Garmin", system)

    def test_system_prompt_contains_no_fabrication_rule(self):
        system = self._system()
        has_rule = (
            "never fabricate" in system.lower()
            or "NEVER invent" in system
            or "fabricating" in system
        )
        self.assertTrue(has_rule, "Hard no-fabrication rule not found in system prompt")

    def test_system_prompt_lists_garmin_capability(self):
        system = self._system()
        self.assertIn("Garmin health data", system)
        # Must indicate it is live / real access, not theoretical
        self.assertIn("LIVE ACCESS", system)

    def test_system_prompt_no_fabrication_is_mandatory_language(self):
        system = self._system()
        self.assertIn("fabricating", system)
        self.assertIn("worst", system)

    def test_capabilities_guidance_in_prompt_when_no_profile(self):
        # USER.md is empty — profile block is skipped but capabilities must still appear
        system = self._system(user_text="")
        self.assertIn("Garmin", system)
        self.assertIn("fabricating", system)


if __name__ == "__main__":
    unittest.main()
