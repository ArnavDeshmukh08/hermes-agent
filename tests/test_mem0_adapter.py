"""Tests for jack_memory.mem0_adapter.Mem0MemoryUpdater."""

import asyncio
import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from jack_memory.mem0_adapter import Mem0MemoryUpdater
from jack_memory.queue import MemoryQueue


class Mem0AdapterTest(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_run_async_calls_client_add_on_success(self):
        mock_client = MagicMock()
        mock_client.add.return_value = {}
        d = tempfile.mkdtemp()
        queue = MemoryQueue(path=Path(d) / "q.json")
        adapter = Mem0MemoryUpdater(client=mock_client, queue=queue)
        self._run(adapter.run_async([], "hello", "hi there"))
        mock_client.add.assert_called_once()
        self.assertEqual(len(queue), 0)  # nothing queued

    def test_run_async_enqueues_when_client_raises(self):
        """Mac unreachable → conversation queued, not lost."""
        mock_client = MagicMock()
        mock_client.add.side_effect = ConnectionError("Mac down")
        d = tempfile.mkdtemp()
        queue = MemoryQueue(path=Path(d) / "q.json")
        adapter = Mem0MemoryUpdater(client=mock_client, queue=queue)
        self._run(adapter.run_async([], "test message", "test reply"))
        self.assertEqual(len(queue), 1)  # queued for later

    def test_run_async_never_raises(self):
        """run_async must swallow ALL exceptions — same contract as MemoryUpdater."""
        mock_client = MagicMock()
        mock_client.add.side_effect = Exception("catastrophic")
        d = tempfile.mkdtemp()
        queue = MemoryQueue(path=Path(d) / "q.json")
        queue.enqueue = MagicMock(side_effect=Exception("queue also broken"))
        adapter = Mem0MemoryUpdater(client=mock_client, queue=queue)
        try:
            self._run(adapter.run_async([], "msg", "reply"))
        except Exception as e:
            self.fail(f"run_async raised: {e}")

    def test_run_async_signature_matches_legacy(self):
        """run_async(history, user_message, jack_response) — exact same sig."""
        from jack_memory.updater import MemoryUpdater
        legacy_sig = inspect.signature(MemoryUpdater.run_async)
        new_sig = inspect.signature(Mem0MemoryUpdater.run_async)
        legacy_params = list(legacy_sig.parameters.keys())
        new_params = list(new_sig.parameters.keys())
        self.assertEqual(legacy_params, new_params)

    def test_build_messages_includes_current_turn(self):
        msgs = Mem0MemoryUpdater._build_messages([], "user said this", "jack replied")
        contents = [m["content"] for m in msgs]
        self.assertIn("user said this", contents)
        self.assertIn("jack replied", contents)

    def test_build_messages_includes_prior_exchange(self):
        history = [
            {"role": "user", "content": "prior question"},
            {"role": "assistant", "content": "prior answer"},
        ]
        msgs = Mem0MemoryUpdater._build_messages(history, "new q", "new a")
        self.assertTrue(len(msgs) >= 4)

    def test_run_async_noop_on_empty_messages(self):
        """Empty user message and response → client.add never called."""
        mock_client = MagicMock()
        d = tempfile.mkdtemp()
        queue = MemoryQueue(path=Path(d) / "q.json")
        adapter = Mem0MemoryUpdater(client=mock_client, queue=queue)
        self._run(adapter.run_async([], "", ""))
        mock_client.add.assert_not_called()
        self.assertEqual(len(queue), 0)

    def test_build_messages_caps_history_at_two(self):
        """Only the last two history messages are included."""
        history = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "msg2"},
            {"role": "assistant", "content": "resp2"},
        ]
        msgs = Mem0MemoryUpdater._build_messages(history, "current", "answer")
        # At most 2 history + 2 current = 4 messages
        self.assertLessEqual(len(msgs), 4)

    def test_queued_item_contains_messages_and_user_id(self):
        """Queued item must include messages and user_id for later replay."""
        mock_client = MagicMock()
        mock_client.add.side_effect = OSError("network")
        d = tempfile.mkdtemp()
        queue = MemoryQueue(path=Path(d) / "q.json")
        adapter = Mem0MemoryUpdater(client=mock_client, queue=queue, user_id="arnav")
        self._run(adapter.run_async([], "ping", "pong"))
        items = queue.peek_all()
        self.assertEqual(len(items), 1)
        self.assertIn("messages", items[0])
        self.assertEqual(items[0]["user_id"], "arnav")

    def test_log_file_written_on_success(self):
        """A line should appear in memory.log after a successful add."""
        mock_client = MagicMock()
        mock_client.add.return_value = {}
        d = tempfile.mkdtemp()
        log_path = Path(d) / "memory.log"
        queue = MemoryQueue(path=Path(d) / "q.json")
        adapter = Mem0MemoryUpdater(client=mock_client, queue=queue, log_path=log_path)
        self._run(adapter.run_async([], "hello", "hi"))
        self.assertTrue(log_path.exists())
        content = log_path.read_text()
        self.assertIn("[mem0]", content)


if __name__ == "__main__":
    unittest.main()
