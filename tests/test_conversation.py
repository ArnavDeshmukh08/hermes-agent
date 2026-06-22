"""Tests for Jack's conversation brain (hooks/jack_router/conversation.py).

Pure-logic paths (personality extraction, history window, prompt budget) run with
no network. The end-to-end respond() path runs offline via HERMES_LLM_MOCK.
"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks" / "jack_router"))

import conversation  # noqa: E402
from conversation import JackConversationHandler, estimate_tokens  # noqa: E402

SOUL_FIXTURE = """# Jack Core OS Directive

You operate as a single identity — Jack.

## Mode A — Jack as Chief of Staff & Personal Assistant
You are Jack, Chief of Staff for Arnav Deshmukh, a technical founder.
- Human-Like Companion: be intuitive and proactive.
- Proactive Management: Ask him "What's the agenda for today?"

### Jack Operational Modules
1. Social Engine: run `/home/hermes/.hermes/bin/send_linkedin_post.py` via your terminal tool.
2. Image Generation: use your terminal tool to run the image script.
"""

USER_FIXTURE = """# User Profile
- Identity: AIML student and technical founder.
- Tone: authentic, minimal emojis.
"""


def _handler(**kw):
    """Build a handler against fixture SOUL/USER files."""
    d = tempfile.mkdtemp()
    soul = Path(d) / "SOUL.md"
    user = Path(d) / "USER.md"
    soul.write_text(SOUL_FIXTURE, encoding="utf-8")
    user.write_text(USER_FIXTURE, encoding="utf-8")
    return JackConversationHandler(soul_path=soul, user_path=user, **kw)


class PersonalityTest(unittest.TestCase):
    def test_personality_keeps_identity(self):
        h = _handler()
        self.assertIn("You are Jack", h._personality)
        self.assertIn("Arnav Deshmukh", h._personality)

    def test_personality_strips_operational_tool_sections(self):
        # The tool-laden operational blocks must NOT leak into a tool-less chat
        # prompt (that's what caused the model to narrate fake tool use).
        h = _handler()
        self.assertNotIn("send_linkedin_post.py", h._personality)
        self.assertNotIn("terminal tool", h._personality)
        self.assertNotIn("Operational Modules", h._personality)

    def test_no_proactive_agenda_ask(self):
        # The agenda directive from SOUL.md must be stripped from the extracted
        # personality, and the built system prompt must carry the new
        # no-proactive-questions rule instead.
        h = _handler()
        # The agenda directive is gone from the extracted identity prose...
        self.assertNotIn("agenda", h._personality.lower())
        self.assertNotIn("what's the agenda", h._personality.lower())
        # ...and the no-proactive-questions rule is present in the system prompt.
        # (The guidance text deliberately quotes "What's the agenda for today?"
        # as an example of what NOT to do, so we assert on the personality block
        # for absence and on the guidance for the new rule.)
        system, _ = h.build_prompt("hey", "u")
        self.assertIn("never proactively ask", system.lower())

    def test_missing_soul_uses_fallback(self):
        h = JackConversationHandler(
            soul_path=Path("/nonexistent/SOUL.md"),
            user_path=Path("/nonexistent/USER.md"),
        )
        self.assertIn("Jack", h._personality)
        self.assertEqual(h._profile, "")


class HistoryTest(unittest.TestCase):
    def test_sliding_window_caps_turns(self):
        h = _handler(max_turns=2)  # → at most 4 messages (2 exchanges)
        for i in range(5):
            h.add_turn("u1", "user", f"msg {i}")
            h.add_turn("u1", "assistant", f"reply {i}")
        ctx = h.get_context("u1")
        self.assertEqual(len(ctx), 4)
        self.assertEqual(ctx[0]["content"], "msg 3")  # oldest kept is exchange 3
        self.assertEqual(ctx[-1]["content"], "reply 4")

    def test_per_user_isolation(self):
        h = _handler()
        h.add_turn("a", "user", "hi from a")
        self.assertEqual(h.get_context("b"), [])

    def test_empty_history_default(self):
        h = _handler()
        self.assertEqual(h.get_context("nobody"), [])


class PromptBudgetTest(unittest.TestCase):
    def test_prompt_stays_under_budget(self):
        # Budget must exceed the system prompt floor (~575 tokens with capabilities
        # block) but stay well below system + all 20 turns (~4575 tokens), so the
        # sliding-window trimmer is exercised and the budget assertion is meaningful.
        h = _handler(max_turns=20, token_budget=800)
        for i in range(20):
            h.add_turn("u", "user", "x" * 400)  # ~100 tokens each
            h.add_turn("u", "assistant", "y" * 400)
        system, user_block = h.build_prompt("current question", "u")
        total = estimate_tokens(system) + estimate_tokens(user_block)
        self.assertLessEqual(total, 800)

    def test_current_message_always_present(self):
        h = _handler(max_turns=20, token_budget=1)  # impossibly small
        system, user_block = h.build_prompt("REMEMBER THIS LINE", "u")
        self.assertIn("REMEMBER THIS LINE", user_block)

    def test_profile_injected_into_system(self):
        h = _handler()
        system, _ = h.build_prompt("hey", "u")
        self.assertIn("AIML student", system)


class RespondTest(unittest.TestCase):
    def test_respond_returns_text_offline(self):
        os.environ["HERMES_LLM_MOCK"] = "1"
        try:
            h = _handler()
            reply = asyncio.run(h.respond("hello jack", "u1"))
            self.assertIsInstance(reply, str)
            self.assertTrue(reply)  # non-empty (real mock text or graceful fallback)
        finally:
            os.environ.pop("HERMES_LLM_MOCK", None)


if __name__ == "__main__":
    unittest.main()
