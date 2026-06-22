"""Tests for integrations.claude_bridge.ClaudeBridge.

All tests are fully offline: every external dependency (LLM, Mem0, Discord)
is injected as a fake or MagicMock. No filesystem, network, or Qdrant access
is required to run this suite.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parents[1]
_HERMES_ROOT = _REPO_ROOT.parent  # ~/.hermes — where integrations/, jack_memory/, etc. live
sys.path.insert(0, str(_HERMES_ROOT))
sys.path.insert(0, str(_REPO_ROOT))

from integrations.claude_bridge import ClaudeBridge  # noqa: E402


def _make_mem0_result(*events: str) -> dict:
    """Build a mem0-shaped add() result with the given event types."""
    return {"results": [{"event": e, "id": f"id-{i}"} for i, e in enumerate(events)]}


class TestSummarize(unittest.TestCase):
    """Tests 1-3: summarize()"""

    def test_summarize_calls_llm(self):
        expected = "- Picked Groq as primary brain\n- Deployed Jack on VPS"
        received_args: list = []

        def fake_complete(system, user, **kw):
            received_args.append((system, user, kw))
            return expected

        bridge = ClaudeBridge(complete_fn=fake_complete)
        result = bridge.summarize("some long transcript here")

        self.assertEqual(result, expected)
        self.assertEqual(len(received_args), 1)
        _system, user_arg, kwargs = received_args[0]
        self.assertIn("some long transcript here", user_arg)
        self.assertEqual(kwargs.get("prefer"), "groq")

    def test_summarize_empty_input_returns_empty(self):
        call_count = [0]

        def fake_complete(system, user, **kw):
            call_count[0] += 1
            return "should not be called"

        bridge = ClaudeBridge(complete_fn=fake_complete)

        self.assertEqual(bridge.summarize(""), "")
        self.assertEqual(bridge.summarize("   "), "")
        self.assertEqual(bridge.summarize("\n\t\n"), "")
        self.assertEqual(call_count[0], 0)

    def test_summarize_llm_failure_returns_empty(self):
        def fake_complete(system, user, **kw):
            raise RuntimeError("Groq is down")

        bridge = ClaudeBridge(complete_fn=fake_complete)
        self.assertEqual(bridge.summarize("some transcript"), "")


class TestPushToMemory(unittest.TestCase):
    """Tests 4-6: push_to_memory()"""

    def test_push_calls_add_with_summary_text(self):
        mock_client = MagicMock()
        mock_client.add.return_value = _make_mem0_result("ADD")

        bridge = ClaudeBridge(mem0_client=mock_client)
        bridge.push_to_memory("- Picked Groq as primary brain")

        mock_client.add.assert_called_once()
        call_args = mock_client.add.call_args
        self.assertIn("Groq", call_args.args[0])

    def test_push_counts_add_and_update_events(self):
        mock_client = MagicMock()
        # Two ADD, one UPDATE, one NOOP — expect count = 3
        mock_client.add.return_value = _make_mem0_result("ADD", "ADD", "UPDATE", "NOOP")

        bridge = ClaudeBridge(mem0_client=mock_client)
        count = bridge.push_to_memory("- fact one\n- fact two\n- fact three")

        self.assertEqual(count, 3)

    def test_push_empty_summary_noop(self):
        mock_client = MagicMock()

        bridge = ClaudeBridge(mem0_client=mock_client)
        count = bridge.push_to_memory("")

        mock_client.add.assert_not_called()
        self.assertEqual(count, 0)

    def test_push_passes_source_metadata(self):
        mock_client = MagicMock()
        mock_client.add.return_value = _make_mem0_result("ADD")

        bridge = ClaudeBridge(mem0_client=mock_client)
        bridge.push_to_memory("some summary")

        call_kwargs = mock_client.add.call_args.kwargs
        self.assertEqual(call_kwargs.get("metadata", {}).get("source"), "claude_bridge")

    def test_push_returns_zero_on_add_exception(self):
        mock_client = MagicMock()
        mock_client.add.side_effect = OSError("Qdrant unreachable")

        bridge = ClaudeBridge(mem0_client=mock_client)
        count = bridge.push_to_memory("some summary")

        self.assertEqual(count, 0)

    def test_push_returns_zero_when_all_events_are_noop(self):
        mock_client = MagicMock()
        mock_client.add.return_value = _make_mem0_result("NOOP", "NOOP")

        bridge = ClaudeBridge(mem0_client=mock_client)
        count = bridge.push_to_memory("- duplicate fact already in memory")

        self.assertEqual(count, 0)


class TestNotify(unittest.TestCase):
    """Test 7: notify()"""

    def test_notify_uses_send_fn(self):
        captured: list[dict] = []

        def fake_send(content, **kw):
            captured.append({"content": content, "kw": kw})
            return True

        bridge = ClaudeBridge(send_fn=fake_send)
        result = bridge.notify("summary text")

        self.assertTrue(result)
        self.assertEqual(len(captured), 1)
        self.assertIn("summary text", captured[0]["content"])


class TestRun(unittest.TestCase):
    """Tests 8-12: run() orchestration"""

    def test_run_happy_path_all_three_steps(self):
        call_order: list[str] = []

        def fake_complete(system, user, **kw):
            call_order.append("summarize")
            return "- Arnav chose Groq\n- Deployed on VPS"

        mock_client = MagicMock()
        mock_client.add.side_effect = lambda *a, **kw: (
            call_order.append("push") or _make_mem0_result("ADD", "ADD")
        )

        def fake_send(content, **kw):
            call_order.append("notify")
            self.assertIn("Arnav chose Groq", content)
            return True

        bridge = ClaudeBridge(
            complete_fn=fake_complete,
            mem0_client=mock_client,
            send_fn=fake_send,
        )
        result = bridge.run("some transcript")

        self.assertIn("Arnav chose Groq", result["summary"])
        self.assertEqual(result["appended"], 2)
        self.assertTrue(result["notified"])
        self.assertIsNone(result["error"])
        self.assertEqual(call_order, ["summarize", "push", "notify"])

    def test_run_notify_failure_does_not_zero_appended(self):
        def fake_complete(system, user, **kw):
            return "- Fact from session"

        mock_client = MagicMock()
        mock_client.add.return_value = _make_mem0_result("ADD")

        def exploding_send(content, **kw):
            raise OSError("Discord unreachable")

        bridge = ClaudeBridge(
            complete_fn=fake_complete,
            mem0_client=mock_client,
            send_fn=exploding_send,
        )
        result = bridge.run("some transcript")

        self.assertEqual(result["appended"], 1)
        self.assertFalse(result["notified"])
        self.assertIn("Fact from session", result["summary"])

    def test_run_no_notify_when_appended_zero(self):
        """When Mem0 stores nothing (all NOOP / failure), Discord must NOT fire."""
        call_count = [0]

        def fake_complete(system, user, **kw):
            return "- duplicate fact already in memory"

        mock_client = MagicMock()
        mock_client.add.return_value = _make_mem0_result("NOOP")

        def spy_send(content, **kw):
            call_count[0] += 1
            return True

        bridge = ClaudeBridge(
            complete_fn=fake_complete,
            mem0_client=mock_client,
            send_fn=spy_send,
        )
        result = bridge.run("some transcript")

        self.assertEqual(result["appended"], 0)
        self.assertFalse(result["notified"])
        self.assertEqual(call_count[0], 0, "send_fn must NOT be called when appended=0")
        self.assertIsNotNone(result["error"])

    def test_run_blank_input_returns_error_no_llm_call(self):
        call_count = [0]

        def fake_complete(system, user, **kw):
            call_count[0] += 1
            return "should not be called"

        mock_client = MagicMock()
        bridge = ClaudeBridge(complete_fn=fake_complete, mem0_client=mock_client)

        result = bridge.run("")
        self.assertEqual(result["summary"], "")
        self.assertEqual(result["appended"], 0)
        self.assertFalse(result["notified"])
        self.assertIsNotNone(result["error"])
        self.assertEqual(call_count[0], 0)
        mock_client.add.assert_not_called()

    def test_run_llm_failure_reports_error_not_success(self):
        """LLM returning '' must surface an error, not silently succeed."""
        def fake_complete(system, user, **kw):
            raise RuntimeError("Groq timeout")

        mock_client = MagicMock()
        bridge = ClaudeBridge(complete_fn=fake_complete, mem0_client=mock_client)

        result = bridge.run("real transcript content")
        self.assertEqual(result["summary"], "")
        self.assertEqual(result["appended"], 0)
        self.assertFalse(result["notified"])
        self.assertIsNotNone(result["error"])
        mock_client.add.assert_not_called()

    def test_run_result_always_has_four_keys(self):
        """run() must always return all four keys regardless of code path."""
        bridge = ClaudeBridge(
            complete_fn=lambda *a, **kw: "",
            mem0_client=MagicMock(),
            send_fn=lambda *a, **kw: True,
        )
        result = bridge.run("x")
        self.assertIn("summary", result)
        self.assertIn("appended", result)
        self.assertIn("notified", result)
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
