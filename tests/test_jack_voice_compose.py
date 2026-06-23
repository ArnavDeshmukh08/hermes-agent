"""Tests for jack_voice.compose — the unified personality layer.

All tests run offline: HERMES_LLM_MOCK=1 makes lib.llm.complete return a
deterministic string without any network calls.  We patch the mock return to
control what the "LLM" produces so we can test the layer's behavior.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is on sys.path.
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _set_mock_env():
    os.environ["HERMES_LLM_MOCK"] = "1"
    os.environ.setdefault("HERMES_ROOT", str(_REPO))


_set_mock_env()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _make_compose():
    """Fresh import of compose_reply with module-level cache cleared."""
    import importlib
    import jack_voice.compose as _m
    _m._personality_cache = None  # reset cache so env changes take effect
    importlib.reload(_m)
    return _m.compose_reply


class _Base(unittest.TestCase):
    def setUp(self):
        _set_mock_env()
        # Clear personality cache before each test.
        import jack_voice.compose as _m
        _m._personality_cache = None
        os.environ.pop("JACK_VOICE_ENABLED", None)

    def tearDown(self):
        os.environ.pop("JACK_VOICE_ENABLED", None)


# --------------------------------------------------------------------------- #
# Core behaviour
# --------------------------------------------------------------------------- #

class TestComposeCoreReturns(_Base):
    def test_compose_returns_llm_output(self):
        """When LLM returns text, compose_reply returns it."""
        from jack_voice.compose import compose_reply
        with patch("lib.llm.complete", return_value="Great sleep, 7.2 hours!") as mock_llm:
            result = compose_reply(
                "health_sleep",
                {"total_sleep_h": 7.2},
                "how did I sleep?",
                fallback_plain="Sleep: 7.2h",
            )
        self.assertEqual(result, "Great sleep, 7.2 hours!")
        mock_llm.assert_called_once()

    def test_compose_fallback_on_llm_error(self):
        """When lib.llm.complete raises LLMError, compose returns fallback_plain."""
        from lib.llm import LLMError
        from jack_voice.compose import compose_reply
        with patch("lib.llm.complete", side_effect=LLMError("provider down")):
            result = compose_reply(
                "health_sleep",
                {"total_sleep_h": 7.2},
                "how did I sleep?",
                fallback_plain="Sleep: 7.2h",
            )
        self.assertEqual(result, "Sleep: 7.2h")

    def test_compose_fallback_on_timeout(self):
        """TimeoutError from LLM → fallback_plain returned."""
        from jack_voice.compose import compose_reply
        with patch("lib.llm.complete", side_effect=TimeoutError("took too long")):
            result = compose_reply(
                "health_stats",
                {"steps": 8432},
                "my steps",
                fallback_plain="Steps: 8,432",
            )
        self.assertEqual(result, "Steps: 8,432")

    def test_compose_never_raises(self):
        """Even on a totally unexpected exception, compose_reply never raises."""
        from jack_voice.compose import compose_reply
        with patch("lib.llm.complete", side_effect=RuntimeError("kaboom")):
            result = compose_reply(
                "reminder_set",
                {"message": "call mom"},
                "remind me to call mom at 9pm",
                fallback_plain="Got it ⏰",
            )
        self.assertEqual(result, "Got it ⏰")

    def test_compose_fallback_when_llm_returns_empty(self):
        """Empty string from LLM → fallback_plain (not an empty string to the user)."""
        from jack_voice.compose import compose_reply
        with patch("lib.llm.complete", return_value=""):
            result = compose_reply(
                "health_sleep",
                {"total_sleep_h": 5.8},
                "sleep?",
                fallback_plain="Sleep: 5.8h",
            )
        self.assertEqual(result, "Sleep: 5.8h")

    def test_compose_empty_fallback_handled(self):
        """If fallback_plain is empty string and LLM fails, '' is returned (not None)."""
        from jack_voice.compose import compose_reply
        with patch("lib.llm.complete", side_effect=RuntimeError("boom")):
            result = compose_reply(
                "goal_query",
                {"goals": []},
                "my goals?",
                fallback_plain="",
            )
        self.assertEqual(result, "")


# --------------------------------------------------------------------------- #
# Kill-switch
# --------------------------------------------------------------------------- #

class TestKillSwitch(_Base):
    def _assert_bypassed(self, value):
        os.environ["JACK_VOICE_ENABLED"] = value
        import jack_voice.compose as _m
        _m._personality_cache = None
        with patch("lib.llm.complete") as mock_llm:
            result = _m.compose_reply("health_sleep", {"x": 1}, "test", fallback_plain="plain")
        mock_llm.assert_not_called()
        self.assertEqual(result, "plain")

    def test_kill_switch_zero(self):
        self._assert_bypassed("0")

    def test_kill_switch_false(self):
        self._assert_bypassed("false")

    def test_kill_switch_no(self):
        self._assert_bypassed("no")

    def test_kill_switch_enabled_passes_through(self):
        """JACK_VOICE_ENABLED=1 (default) does call the LLM."""
        os.environ["JACK_VOICE_ENABLED"] = "1"
        import jack_voice.compose as _m
        _m._personality_cache = None
        with patch("lib.llm.complete", return_value="reply") as mock_llm:
            _m.compose_reply("health_sleep", {"x": 1}, "test", fallback_plain="plain")
        mock_llm.assert_called_once()


# --------------------------------------------------------------------------- #
# Prompt content — fact preservation
# --------------------------------------------------------------------------- #

class TestPromptContent(_Base):
    def _captured_system(self, intent, data, user_message):
        """Run compose_reply and capture the system prompt passed to LLM."""
        from jack_voice.compose import compose_reply
        captured = {}
        def _fake_complete(system, user, **_kwargs):
            captured["system"] = system
            captured["user"] = user
            return "ok"
        with patch("lib.llm.complete", side_effect=_fake_complete):
            compose_reply(intent, data, user_message, fallback_plain="fallback")
        return captured

    def test_prompt_contains_user_message(self):
        c = self._captured_system("health_sleep", {}, "how did I sleep tonight?")
        self.assertIn("how did I sleep tonight?", c["user"])

    def test_prompt_contains_hard_rules(self):
        c = self._captured_system("health_sleep", {}, "sleep?")
        self.assertIn("HARD RULES", c["system"])

    def test_prompt_contains_personality(self):
        c = self._captured_system("health_sleep", {}, "sleep?")
        # At minimum the fallback personality should appear.
        has_personality = (
            "Jack" in c["system"] or "personal" in c["system"].lower()
        )
        self.assertTrue(has_personality, "Personality text missing from system prompt")

    def test_prompt_contains_facts_block(self):
        c = self._captured_system("health_sleep", {"total_sleep_h": 7.2}, "sleep?")
        self.assertIn("FACTS", c["system"])
        self.assertIn("total_sleep_h", c["system"])

    def test_prompt_contains_no_fabrication_rule(self):
        c = self._captured_system("health_stats", {"steps": 191}, "steps?")
        self.assertIn("fabricat", c["system"].lower())

    def test_prompt_contains_no_medical_prescription_rule(self):
        c = self._captured_system("health_sleep", {}, "sleep?")
        lower = c["system"].lower()
        has_rule = "medical" in lower or "prescrib" in lower or "diet" in lower
        self.assertTrue(has_rule, "Framing/medical rule missing from system prompt")

    def test_compose_fact_preservation_sleep(self):
        """The sleep value 7.2 must appear in the system prompt FACTS block."""
        c = self._captured_system(
            "health_sleep",
            {"total_sleep_h": 7.2, "deep_h": 1.5, "rem_h": 0.9},
            "how did I sleep?",
        )
        self.assertIn("7.2", c["system"])
        self.assertIn("1.5", c["system"])

    def test_compose_fact_preservation_steps(self):
        """Step count 8432 must survive into the system prompt."""
        c = self._captured_system(
            "health_stats",
            {"steps": 8432, "calories": 312},
            "my steps",
        )
        self.assertIn("8432", c["system"])

    def test_compose_fact_preservation_reminder(self):
        """Reminder time and message must appear in the prompt."""
        c = self._captured_system(
            "reminder_set",
            {"message": "call Spandan", "fire_at_ist": "9:00 PM IST, Mon Jun 23"},
            "remind me to call Spandan at 9pm",
        )
        self.assertIn("call Spandan", c["system"])
        self.assertIn("9:00 PM", c["system"])

    def test_compose_fact_preservation_goal_title(self):
        """Goal title must survive into the prompt."""
        c = self._captured_system(
            "goal_query",
            {"goals": [{"title": "Run a marathon", "deadline": "2026-09-01"}], "count": 1},
            "how's my marathon goal?",
        )
        self.assertIn("marathon", c["system"].lower())


# --------------------------------------------------------------------------- #
# Memory context
# --------------------------------------------------------------------------- #

class TestMemoryContext(_Base):
    def test_memory_context_injected_when_provided(self):
        """Caller-provided memories appear in the system prompt."""
        from jack_voice.compose import compose_reply
        captured = {}
        def _fake(system, user, **kw):
            captured["system"] = system
            return "nice"
        with patch("lib.llm.complete", side_effect=_fake):
            compose_reply(
                "health_sleep",
                {"total_sleep_h": 6.0},
                "sleep?",
                fallback_plain="fallback",
                context={"memories": [{"memory": "Arnav loves trail running"}]},
            )
        self.assertIn("trail running", captured["system"])

    def test_no_memory_backend_skips_retrieval(self):
        """If JACK_MEMORY_BACKEND is not 'mem0', no JackMemoryClient import."""
        os.environ.pop("JACK_MEMORY_BACKEND", None)
        from jack_voice.compose import compose_reply
        with patch("jack_memory.client.JackMemoryClient") as mock_mc, \
             patch("lib.llm.complete", return_value="ok"):
            compose_reply("health_sleep", {}, "sleep?", fallback_plain="fb")
        # In non-mem0 mode, the client should NOT be called for memory retrieval
        mock_mc.from_env.assert_not_called()


# --------------------------------------------------------------------------- #
# Timeout / prefer forwarded to LLM
# --------------------------------------------------------------------------- #

class TestLLMCallParams(_Base):
    def test_timeout_forwarded(self):
        """JACK_VOICE_TIMEOUT env is forwarded to llm.complete."""
        os.environ["JACK_VOICE_TIMEOUT"] = "15"
        import jack_voice.compose as _m
        _m._personality_cache = None
        captured = {}
        def _fake(system, user, **kw):
            captured.update(kw)
            return "ok"
        with patch("lib.llm.complete", side_effect=_fake):
            _m.compose_reply("health_sleep", {}, "?", fallback_plain="fb")
        self.assertEqual(captured.get("timeout"), 15)
        os.environ.pop("JACK_VOICE_TIMEOUT", None)

    def test_prefer_groq_by_default(self):
        """Default provider is groq."""
        os.environ.pop("JACK_CHAT_PROVIDER", None)
        import jack_voice.compose as _m
        _m._personality_cache = None
        captured = {}
        def _fake(system, user, **kw):
            captured.update(kw)
            return "ok"
        with patch("lib.llm.complete", side_effect=_fake):
            _m.compose_reply("health_sleep", {}, "?", fallback_plain="fb")
        self.assertEqual(captured.get("prefer"), "groq")


if __name__ == "__main__":
    unittest.main()
