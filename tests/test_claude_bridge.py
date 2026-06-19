"""Tests for integrations.claude_bridge.ClaudeBridge.

All tests are fully offline: every external dependency (LLM, USER.md,
Discord) is injected as a fake. The sys.path pattern mirrors
tests/test_memory_updater.py — insert repo root so imports resolve
whether pytest is run from the repo root or from within tests/.
"""

from __future__ import annotations

import datetime
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from integrations.claude_bridge import ClaudeBridge  # noqa: E402
from jack_memory.updater import MemoryUpdater  # noqa: E402

# ---------------------------------------------------------------------------
# Minimal USER.md fixture that matches updater's expected section format.
# ---------------------------------------------------------------------------
_USER_FIXTURE = """Jack's Memory — Arnav Deshmukh
Last updated: 2026-06-01

[IDENTITY]
Name: Arnav Deshmukh

[RELATIONSHIPS]
Girlfriend: Siddhi

[WORK & PROJECTS]
Primary: Vytal Clinic OS

[CURRENT PRIORITIES]
Week of 2026-06-19:
- Build Jack

[DAILY ROUTINE — ICHALKARANJI (HOLIDAYS)]
~8:30am: Wake up

[PREFERENCES]
Food: Non-veg

[GOALS]
Short term: Launch Vytal

[THINGS JACK HAS LEARNED]
2026-06-01: Seed line.
"""


def _make_temp_user_md(content: str = _USER_FIXTURE) -> Path:
    d = tempfile.mkdtemp()
    p = Path(d) / "USER.md"
    p.write_text(content, encoding="utf-8")
    return p


class TestSummarize(unittest.TestCase):
    """Tests 1-3: summarize()"""

    # -----------------------------------------------------------------------
    # Test 1 — LLM is called with the transcript; result returned verbatim.
    # -----------------------------------------------------------------------
    def test_summarize_calls_llm(self):
        expected = "- Picked Groq as primary brain\n- Deployed Jack on VPS"
        received_args: list = []

        def fake_complete(system, user, **kw):
            received_args.append((system, user, kw))
            return expected

        bridge = ClaudeBridge(complete_fn=fake_complete)
        result = bridge.summarize("some long transcript here")

        self.assertEqual(result, expected)
        self.assertEqual(len(received_args), 1, "complete_fn should be called exactly once")
        _system, user_arg, kwargs = received_args[0]
        self.assertIn("some long transcript here", user_arg)
        self.assertEqual(kwargs.get("prefer"), "groq")

    # -----------------------------------------------------------------------
    # Test 2 — Empty / whitespace-only input → '' without calling the LLM.
    # -----------------------------------------------------------------------
    def test_summarize_empty_input_returns_empty(self):
        call_count = [0]

        def fake_complete(system, user, **kw):
            call_count[0] += 1
            return "should not be called"

        bridge = ClaudeBridge(complete_fn=fake_complete)

        self.assertEqual(bridge.summarize(""), "")
        self.assertEqual(bridge.summarize("   "), "")
        self.assertEqual(bridge.summarize("\n\t\n"), "")
        self.assertEqual(call_count[0], 0, "complete_fn must NOT be called for blank input")

    # -----------------------------------------------------------------------
    # Test 3 — LLM raises → '' returned, no exception propagated.
    # -----------------------------------------------------------------------
    def test_summarize_llm_failure_returns_empty(self):
        def fake_complete(system, user, **kw):
            raise RuntimeError("Groq is down")

        bridge = ClaudeBridge(complete_fn=fake_complete)
        result = bridge.summarize("some transcript")
        self.assertEqual(result, "")


class TestPushToMemory(unittest.TestCase):
    """Tests 4-5: push_to_memory()"""

    # -----------------------------------------------------------------------
    # Test 4 — Facts are appended; file grows; pre-existing lines untouched.
    # -----------------------------------------------------------------------
    def test_push_to_memory_appends(self):
        user_path = _make_temp_user_md()
        updater = MemoryUpdater(user_path=user_path)
        original_text = user_path.read_text(encoding="utf-8")

        bridge = ClaudeBridge(updater=updater)
        count = bridge.push_to_memory(
            "- Picked Groq as primary brain",
            today=datetime.date(2026, 6, 20),
        )

        self.assertGreaterEqual(count, 1)
        new_text = user_path.read_text(encoding="utf-8")
        # The new fact must appear in the file.
        self.assertIn("Picked Groq as primary brain", new_text)
        # Pre-existing lines must all still be present (append-only).
        # The 'Last updated:' header is intentionally bumped by MemoryUpdater
        # (_bump_last_updated) — that single in-place rewrite is part of the
        # updater's documented contract, so we skip it here.
        for line in original_text.splitlines():
            if line.startswith("Last updated:"):
                continue
            self.assertIn(line, new_text.splitlines(),
                          f"Pre-existing line was removed or altered: {line!r}")

    # -----------------------------------------------------------------------
    # Test 5 — Empty summary → 0 returned, USER.md unchanged.
    # -----------------------------------------------------------------------
    def test_push_empty_summary_noop(self):
        user_path = _make_temp_user_md()
        updater = MemoryUpdater(user_path=user_path)
        original_text = user_path.read_text(encoding="utf-8")

        bridge = ClaudeBridge(updater=updater)
        count = bridge.push_to_memory("")

        self.assertEqual(count, 0)
        self.assertEqual(user_path.read_text(encoding="utf-8"), original_text)


class TestNotify(unittest.TestCase):
    """Test 6: notify()"""

    # -----------------------------------------------------------------------
    # Test 6 — send_fn receives content containing the summary; return value
    # is forwarded verbatim.
    # -----------------------------------------------------------------------
    def test_notify_uses_send_fn(self):
        captured: list[dict] = []

        def fake_send(content, **kw):
            captured.append({"content": content, "kw": kw})
            return True

        bridge = ClaudeBridge(send_fn=fake_send)
        result = bridge.notify("summary text")

        self.assertTrue(result)
        self.assertEqual(len(captured), 1, "send_fn must be called exactly once")
        self.assertIn("summary text", captured[0]["content"])


class TestRun(unittest.TestCase):
    """Test 7: run() orchestration"""

    # -----------------------------------------------------------------------
    # Test 7a — Happy path: all three steps called in order; dict has correct
    # structure with appended >= 1 and notified True.
    # -----------------------------------------------------------------------
    def test_run_orchestrates_all_three(self):
        call_order: list[str] = []

        def fake_complete(system, user, **kw):
            call_order.append("summarize")
            return "- Arnav chose Groq\n- Deployed on VPS"

        user_path = _make_temp_user_md()
        updater = MemoryUpdater(user_path=user_path)

        def fake_send(content, **kw):
            call_order.append("notify")
            self.assertIn("Arnav chose Groq", content)
            return True

        # Wrap updater.update_user_md to record the call.
        _orig_update = updater.update_user_md

        def recording_update(learnings, today=None):
            call_order.append("push")
            return _orig_update(learnings, today)

        updater.update_user_md = recording_update

        bridge = ClaudeBridge(
            complete_fn=fake_complete,
            updater=updater,
            send_fn=fake_send,
        )
        result = bridge.run("some transcript", today=datetime.date(2026, 6, 20))

        # Structural checks.
        self.assertIn("summary", result)
        self.assertIn("appended", result)
        self.assertIn("notified", result)
        self.assertIn("Arnav chose Groq", result["summary"])
        self.assertGreaterEqual(result["appended"], 1)
        self.assertTrue(result["notified"])
        # Order must be summarize → push → notify.
        self.assertEqual(call_order, ["summarize", "push", "notify"])

    # -----------------------------------------------------------------------
    # Test 7b — notify raises → notified=False but appended is still correct.
    # -----------------------------------------------------------------------
    def test_run_notify_failure_does_not_zero_appended(self):
        def fake_complete(system, user, **kw):
            return "- Fact from session"

        user_path = _make_temp_user_md()
        updater = MemoryUpdater(user_path=user_path)

        def exploding_send(content, **kw):
            raise OSError("Discord unreachable")

        bridge = ClaudeBridge(
            complete_fn=fake_complete,
            updater=updater,
            send_fn=exploding_send,
        )
        result = bridge.run("some transcript", today=datetime.date(2026, 6, 20))

        self.assertGreaterEqual(result["appended"], 1, "appended must survive a notify failure")
        self.assertFalse(result["notified"])
        self.assertIn("Fact from session", result["summary"])


if __name__ == "__main__":
    unittest.main()
