"""Tests for jack_memory.queue.MemoryQueue.

All offline: uses temp directories for the queue file.
No network, no Mem0, no Qdrant.
"""

import sys
import tempfile
import threading
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from jack_memory.queue import MemoryQueue


class EnqueuePeekTest(unittest.TestCase):
    def _queue(self):
        d = tempfile.mkdtemp()
        return MemoryQueue(path=Path(d) / "queue.json")

    def test_enqueue_then_peek_returns_item(self):
        q = self._queue()
        q.enqueue({"user_id": "arnav", "messages": [{"role": "user", "content": "hi"}]})
        items = q.peek_all()
        self.assertEqual(len(items), 1)
        self.assertIn("messages", items[0])

    def test_enqueue_adds_id_and_ts(self):
        q = self._queue()
        iid = q.enqueue({"user_id": "arnav", "messages": []})
        items = q.peek_all()
        self.assertEqual(items[0]["id"], iid)
        self.assertIn("ts", items[0])

    def test_len_matches_queue_depth(self):
        q = self._queue()
        self.assertEqual(len(q), 0)
        q.enqueue({"messages": []})
        q.enqueue({"messages": []})
        self.assertEqual(len(q), 2)

    def test_enqueue_preserves_existing_id(self):
        """If item already has an 'id', enqueue must use it, not generate a new one."""
        q = self._queue()
        iid = q.enqueue({"id": "fixed-id-123", "messages": []})
        self.assertEqual(iid, "fixed-id-123")
        items = q.peek_all()
        self.assertEqual(items[0]["id"], "fixed-id-123")

    def test_peek_all_returns_fifo_order(self):
        """Items must be returned in insertion order."""
        q = self._queue()
        q.enqueue({"messages": [{"role": "user", "content": "first"}]})
        q.enqueue({"messages": [{"role": "user", "content": "second"}]})
        q.enqueue({"messages": [{"role": "user", "content": "third"}]})
        items = q.peek_all()
        self.assertEqual(items[0]["messages"][0]["content"], "first")
        self.assertEqual(items[1]["messages"][0]["content"], "second")
        self.assertEqual(items[2]["messages"][0]["content"], "third")

    def test_peek_all_on_empty_queue_returns_empty_list(self):
        q = self._queue()
        self.assertEqual(q.peek_all(), [])

    def test_enqueue_returns_string_id(self):
        q = self._queue()
        iid = q.enqueue({"messages": []})
        self.assertIsInstance(iid, str)
        self.assertTrue(len(iid) > 0)

    def test_ts_is_iso_format(self):
        """Timestamps must be parseable ISO 8601 strings."""
        import datetime
        q = self._queue()
        q.enqueue({"messages": []})
        items = q.peek_all()
        ts = items[0]["ts"]
        # Should not raise
        parsed = datetime.datetime.fromisoformat(ts)
        self.assertIsNotNone(parsed)

    def test_enqueue_merges_extra_fields(self):
        """Extra fields passed in item dict must appear in the stored record."""
        q = self._queue()
        q.enqueue({"user_id": "arnav", "messages": [], "extra_field": "hello"})
        items = q.peek_all()
        self.assertEqual(items[0]["extra_field"], "hello")
        self.assertEqual(items[0]["user_id"], "arnav")


class RemoveTest(unittest.TestCase):
    def _queue(self):
        d = tempfile.mkdtemp()
        return MemoryQueue(path=Path(d) / "queue.json")

    def test_remove_by_id(self):
        q = self._queue()
        iid = q.enqueue({"messages": []})
        q.remove(iid)
        self.assertEqual(len(q), 0)

    def test_remove_idempotent(self):
        """Removing an already-removed id must not raise or corrupt."""
        q = self._queue()
        iid = q.enqueue({"messages": []})
        q.remove(iid)
        q.remove(iid)  # second remove: no-op
        self.assertEqual(len(q), 0)

    def test_remove_leaves_other_items(self):
        q = self._queue()
        iid1 = q.enqueue({"messages": [{"role": "user", "content": "a"}]})
        q.enqueue({"messages": [{"role": "user", "content": "b"}]})
        q.remove(iid1)
        items = q.peek_all()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["messages"][0]["content"], "b")

    def test_remove_nonexistent_id_is_noop(self):
        """Removing a never-added id must not raise."""
        q = self._queue()
        q.enqueue({"messages": []})
        q.remove("does-not-exist")
        self.assertEqual(len(q), 1)

    def test_remove_middle_item(self):
        """Removing a middle item must preserve order of surrounding items."""
        q = self._queue()
        q.enqueue({"messages": [{"role": "user", "content": "A"}]})
        iid2 = q.enqueue({"messages": [{"role": "user", "content": "B"}]})
        q.enqueue({"messages": [{"role": "user", "content": "C"}]})
        q.remove(iid2)
        items = q.peek_all()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["messages"][0]["content"], "A")
        self.assertEqual(items[1]["messages"][0]["content"], "C")

    def test_remove_all_items_leaves_empty_queue(self):
        q = self._queue()
        ids = [q.enqueue({"messages": []}) for _ in range(5)]
        for iid in ids:
            q.remove(iid)
        self.assertEqual(len(q), 0)
        self.assertEqual(q.peek_all(), [])


class DrainTest(unittest.TestCase):
    def _queue(self):
        d = tempfile.mkdtemp()
        return MemoryQueue(path=Path(d) / "queue.json")

    def test_drain_removes_only_after_successful_add(self):
        q = self._queue()
        q.enqueue({"messages": []})
        calls = []

        def good_add(item):
            calls.append(item["id"])

        drained = q.drain(good_add)
        self.assertEqual(drained, 1)
        self.assertEqual(len(q), 0)
        self.assertEqual(len(calls), 1)

    def test_drain_does_not_remove_on_add_failure(self):
        """If add_fn raises, the item must remain in the queue."""
        q = self._queue()
        q.enqueue({"messages": []})

        def bad_add(item):
            raise ConnectionError("Mac unreachable")

        drained = q.drain(bad_add)
        self.assertEqual(drained, 0)
        self.assertEqual(len(q), 1)  # item still queued

    def test_drain_stops_on_first_failure_keeps_remainder(self):
        """After one failure, remaining items are not attempted."""
        q = self._queue()
        q.enqueue({"messages": [{"role": "user", "content": "1"}]})
        q.enqueue({"messages": [{"role": "user", "content": "2"}]})
        q.enqueue({"messages": [{"role": "user", "content": "3"}]})
        # first succeeds, second fails, third not attempted
        count = [0]

        def flaky_add(item):
            count[0] += 1
            if count[0] == 2:
                raise RuntimeError("flaked")

        drained = q.drain(flaky_add)
        self.assertEqual(drained, 1)
        self.assertEqual(len(q), 2)

    def test_drain_returns_count(self):
        q = self._queue()
        for i in range(3):
            q.enqueue({"messages": [{"role": "user", "content": str(i)}]})
        drained = q.drain(lambda item: None)
        self.assertEqual(drained, 3)
        self.assertEqual(len(q), 0)

    def test_drain_never_raises(self):
        """drain() must never propagate exceptions."""
        q = self._queue()
        q.enqueue({"messages": []})

        def always_raises(item):
            raise ValueError("boom")

        try:
            q.drain(always_raises)
        except Exception as e:
            self.fail(f"drain() raised unexpectedly: {e}")

    def test_drain_empty_queue(self):
        q = self._queue()
        self.assertEqual(q.drain(lambda item: None), 0)

    def test_drain_calls_add_fn_with_full_item(self):
        """add_fn must receive the full stored record including id and ts."""
        q = self._queue()
        q.enqueue({"user_id": "arnav", "messages": [{"role": "user", "content": "test"}]})
        received = []

        def capture_add(item):
            received.append(item)

        q.drain(capture_add)
        self.assertEqual(len(received), 1)
        self.assertIn("id", received[0])
        self.assertIn("ts", received[0])
        self.assertIn("user_id", received[0])
        self.assertEqual(received[0]["user_id"], "arnav")

    def test_drain_fifo_order(self):
        """drain must process items in insertion order."""
        q = self._queue()
        for i in range(5):
            q.enqueue({"messages": [{"role": "user", "content": str(i)}]})
        order = []

        def record_add(item):
            order.append(item["messages"][0]["content"])

        q.drain(record_add)
        self.assertEqual(order, ["0", "1", "2", "3", "4"])

    def test_drain_never_raises_even_on_corrupt_peek(self):
        """drain must swallow all internal errors, including a corrupt queue file."""
        d = tempfile.mkdtemp()
        p = Path(d) / "queue.json"
        p.write_text("this is not json", encoding="utf-8")
        q = MemoryQueue(path=p)
        try:
            result = q.drain(lambda item: None)
        except Exception as e:
            self.fail(f"drain() raised on corrupt file: {e}")
        # Corrupt file reads as empty — drained == 0
        self.assertEqual(result, 0)

    def test_drain_partial_success_leaves_correct_remainder(self):
        """Verify that exactly the failed + subsequent items remain after partial drain."""
        q = self._queue()
        ids = []
        for i in range(4):
            iid = q.enqueue({"messages": [{"role": "user", "content": str(i)}]})
            ids.append(iid)

        # Fail on item index 2 (third item)
        attempt = [0]

        def fail_on_third(item):
            attempt[0] += 1
            if attempt[0] == 3:
                raise RuntimeError("fail on third")

        drained = q.drain(fail_on_third)
        self.assertEqual(drained, 2)
        remaining = q.peek_all()
        self.assertEqual(len(remaining), 2)
        # Items at index 2 and 3 must remain
        remaining_ids = [r["id"] for r in remaining]
        self.assertIn(ids[2], remaining_ids)
        self.assertIn(ids[3], remaining_ids)


class ConcurrencyTest(unittest.TestCase):
    def test_concurrent_enqueue_no_loss(self):
        """Parallel enqueues must not lose items (flock serialises them)."""
        d = tempfile.mkdtemp()
        q = MemoryQueue(path=Path(d) / "queue.json")
        N = 20
        errors = []

        def do_enqueue(i):
            try:
                q.enqueue({"messages": [{"role": "user", "content": str(i)}]})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_enqueue, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Enqueue errors: {errors}")
        self.assertEqual(len(q), N)

    def test_concurrent_enqueue_and_remove_no_corruption(self):
        """Concurrent enqueues and removes must not corrupt the queue file."""
        d = tempfile.mkdtemp()
        q = MemoryQueue(path=Path(d) / "queue.json")
        N = 10
        enqueued_ids = []
        lock = threading.Lock()
        errors = []

        def do_enqueue(i):
            try:
                iid = q.enqueue({"messages": [{"role": "user", "content": str(i)}]})
                with lock:
                    enqueued_ids.append(iid)
            except Exception as e:
                with lock:
                    errors.append(e)

        # First enqueue N items
        threads = [threading.Thread(target=do_enqueue, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(q), N)

        # Now remove them concurrently
        remove_errors = []

        def do_remove(iid):
            try:
                q.remove(iid)
            except Exception as e:
                remove_errors.append(e)

        rthreads = [threading.Thread(target=do_remove, args=(iid,)) for iid in enqueued_ids]
        for t in rthreads:
            t.start()
        for t in rthreads:
            t.join()

        self.assertEqual(len(remove_errors), 0)
        self.assertEqual(len(q), 0)


class PersistenceTest(unittest.TestCase):
    def test_queue_survives_reopen(self):
        """MemoryQueue reads from disk — a new instance sees items from before."""
        d = tempfile.mkdtemp()
        p = Path(d) / "queue.json"
        q1 = MemoryQueue(path=p)
        q1.enqueue({"messages": [{"role": "user", "content": "persisted"}]})
        # Create a new instance pointing at the same file
        q2 = MemoryQueue(path=p)
        items = q2.peek_all()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["messages"][0]["content"], "persisted")

    def test_queue_created_in_nonexistent_dir(self):
        """MemoryQueue must create parent directories if they don't exist."""
        d = tempfile.mkdtemp()
        nested_path = Path(d) / "deep" / "nested" / "queue.json"
        q = MemoryQueue(path=nested_path)
        q.enqueue({"messages": []})
        self.assertEqual(len(q), 1)
        self.assertTrue(nested_path.exists())

    def test_empty_queue_file_reads_as_empty_list(self):
        """A missing queue file must return [] without raising."""
        d = tempfile.mkdtemp()
        p = Path(d) / "queue.json"
        # File does not exist yet
        q = MemoryQueue(path=p)
        self.assertEqual(q.peek_all(), [])
        self.assertEqual(len(q), 0)

    def test_corrupt_queue_file_reads_as_empty(self):
        """Corrupt JSON in the queue file must be treated as empty, not raise."""
        d = tempfile.mkdtemp()
        p = Path(d) / "queue.json"
        p.write_text("{not valid json[[[", encoding="utf-8")
        q = MemoryQueue(path=p)
        self.assertEqual(q.peek_all(), [])

    def test_multiple_enqueues_accumulate(self):
        """Multiple sequential enqueues on the same instance must all persist."""
        d = tempfile.mkdtemp()
        p = Path(d) / "queue.json"
        q = MemoryQueue(path=p)
        for i in range(5):
            q.enqueue({"messages": [{"role": "user", "content": str(i)}]})
        # Re-open from disk
        q2 = MemoryQueue(path=p)
        self.assertEqual(len(q2), 5)

    def test_file_permissions_are_restrictive(self):
        """Queue file must be created with mode 0o600 (owner read/write only)."""
        d = tempfile.mkdtemp()
        p = Path(d) / "queue.json"
        q = MemoryQueue(path=p)
        q.enqueue({"messages": []})
        import stat
        mode = stat.S_IMODE(p.stat().st_mode)
        self.assertEqual(mode, 0o600)


class EdgeCaseTest(unittest.TestCase):
    def _queue(self):
        d = tempfile.mkdtemp()
        return MemoryQueue(path=Path(d) / "queue.json")

    def test_enqueue_unicode_content(self):
        """Queue must handle Unicode content correctly."""
        q = self._queue()
        q.enqueue({"messages": [{"role": "user", "content": "नमस्ते 🌟 日本語"}]})
        items = q.peek_all()
        self.assertEqual(items[0]["messages"][0]["content"], "नमस्ते 🌟 日本語")

    def test_enqueue_empty_messages_list(self):
        """Empty messages list is valid and must be stored as-is."""
        q = self._queue()
        q.enqueue({"user_id": "arnav", "messages": []})
        items = q.peek_all()
        self.assertEqual(items[0]["messages"], [])

    def test_enqueue_nested_dict(self):
        """Nested structures in item dict must be preserved round-trip."""
        q = self._queue()
        q.enqueue({
            "messages": [{"role": "user", "content": "hi"}],
            "metadata": {"source": "telegram", "chat_id": 12345},
        })
        items = q.peek_all()
        self.assertEqual(items[0]["metadata"]["source"], "telegram")
        self.assertEqual(items[0]["metadata"]["chat_id"], 12345)

    def test_len_empty_after_drain(self):
        q = self._queue()
        for _ in range(3):
            q.enqueue({"messages": []})
        q.drain(lambda item: None)
        self.assertEqual(len(q), 0)

    def test_drain_returns_zero_on_empty(self):
        q = self._queue()
        self.assertEqual(q.drain(lambda item: None), 0)

    def test_remove_on_empty_queue_is_noop(self):
        """Removing from an empty queue must not raise."""
        q = self._queue()
        try:
            q.remove("any-id")
        except Exception as e:
            self.fail(f"remove() on empty queue raised: {e}")
        self.assertEqual(len(q), 0)


if __name__ == "__main__":
    unittest.main()
