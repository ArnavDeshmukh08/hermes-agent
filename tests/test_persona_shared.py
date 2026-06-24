"""Tests verifying persona.py is the single source of truth.

Both compose.py and conversation.py must import from jack_voice.persona —
not duplicate the persona strings. These tests prove:
1. persona.py exports the expected constants
2. compose.py's system prompt contains them (imported, not local copies)
3. conversation.py's system prompt contains them (imported, not local copies)
4. The strings are identical (one truth, not diverged copies)
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO), str(_REPO / "hooks" / "jack_router")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["HERMES_LLM_MOCK"] = "1"
os.environ.setdefault("HERMES_ROOT", str(_REPO))


class TestPersonaModuleExports(unittest.TestCase):
    def test_react_rules_exported(self):
        from jack_voice.persona import REACT_RULES
        self.assertIn("REACT FIRST", REACT_RULES)
        self.assertIn("LEAD WITH MEANING", REACT_RULES)
        self.assertIn("MATCH HIS ENERGY", REACT_RULES)  # rule 4

    def test_hard_constraints_exported(self):
        from jack_voice.persona import HARD_CONSTRAINTS
        self.assertIn("HARD RULES", HARD_CONSTRAINTS)
        self.assertIn("fabricat", HARD_CONSTRAINTS.lower())
        self.assertIn("prescri", HARD_CONSTRAINTS.lower())

    def test_fallback_personality_exported(self):
        from jack_voice.persona import FALLBACK_PERSONALITY
        self.assertIn("Jack", FALLBACK_PERSONALITY)


class TestComposePullsFromPersona(unittest.TestCase):
    """compose.py must import from persona.py, not carry local copies."""

    def _capture_system(self, data=None):
        import jack_voice.compose as _m
        _m._personality_cache = None
        captured = {}
        def _fake(system, user, **kw):
            captured["system"] = system
            return "ok"
        with patch("lib.llm.complete", side_effect=_fake):
            _m.compose_reply("health_sleep", data or {}, "test", fallback_plain="fb")
        return captured["system"]

    def test_compose_system_has_react_rules(self):
        sys_prompt = self._capture_system()
        from jack_voice.persona import REACT_RULES
        # Key phrases from REACT_RULES must appear in compose prompt
        self.assertIn("REACT FIRST", sys_prompt)
        self.assertIn("LEAD WITH MEANING", sys_prompt)

    def test_compose_system_has_hard_constraints(self):
        sys_prompt = self._capture_system()
        from jack_voice.persona import HARD_CONSTRAINTS
        self.assertIn("HARD RULES", sys_prompt)
        self.assertIn("fabricat", sys_prompt.lower())

    def test_compose_react_rules_match_persona(self):
        """The react-rules text in compose's system prompt IS the persona constant."""
        from jack_voice.persona import REACT_RULES
        sys_prompt = self._capture_system()
        # The entire REACT_RULES string must appear verbatim (not a diverged copy)
        self.assertIn(REACT_RULES, sys_prompt)

    def test_compose_hard_constraints_match_persona(self):
        from jack_voice.persona import HARD_CONSTRAINTS
        sys_prompt = self._capture_system()
        self.assertIn(HARD_CONSTRAINTS, sys_prompt)


class TestConversationPullsFromPersona(unittest.TestCase):
    """conversation.py must also import from persona.py."""

    def _build_conv_system(self):
        from conversation import JackConversationHandler
        handler = JackConversationHandler(
            soul_path=Path("/nonexistent"),  # forces fallback personality
            user_path=Path("/nonexistent"),
        )
        system, _ = handler.build_prompt("hey", "u1")
        return system

    def test_conversation_system_has_react_rules(self):
        system = self._build_conv_system()
        self.assertIn("REACT FIRST", system)
        self.assertIn("LEAD WITH MEANING", system)

    def test_conversation_system_has_hard_constraints(self):
        system = self._build_conv_system()
        self.assertIn("HARD RULES", system)
        self.assertIn("fabricat", system.lower())

    def test_conversation_react_rules_match_persona(self):
        from jack_voice.persona import REACT_RULES
        system = self._build_conv_system()
        self.assertIn(REACT_RULES, system)

    def test_conversation_hard_constraints_match_persona(self):
        from jack_voice.persona import HARD_CONSTRAINTS
        system = self._build_conv_system()
        self.assertIn(HARD_CONSTRAINTS, system)


if __name__ == "__main__":
    unittest.main()
