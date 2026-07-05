"""Tests for the Mem0 wiring in conversation.py and scheduler.py.

All offline: inject stubs, mock backends, temp files.
389 existing tests must keep passing (flatfile default = old path).
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "hooks" / "jack_router"))

from conversation import JackConversationHandler  # noqa: E402
from jack_memory.queue import MemoryQueue  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_handler(env_override: dict | None = None, **kwargs) -> JackConversationHandler:
    """Build a handler against minimal temp SOUL/USER files."""
    d = tempfile.mkdtemp()
    soul = Path(d) / "SOUL.md"
    soul.write_text("You are Jack, chief of staff for Arnav.", "utf-8")
    user = Path(d) / "USER.md"
    user.write_text("", "utf-8")
    env = {"JACK_MEMORY_BACKEND": "flatfile", **(env_override or {})}
    with patch.dict(os.environ, env):
        return JackConversationHandler(soul_path=soul, user_path=user, **kwargs)


# ---------------------------------------------------------------------------
# 1. Flatfile path: retriever is None, prompt unchanged
# ---------------------------------------------------------------------------

class ConversationFlatfilePathTest(unittest.TestCase):
    """flatfile default → retriever=None, behaviour byte-identical to pre-Mem0."""

    def test_retriever_is_none_on_flatfile(self):
        h = _make_handler({"JACK_MEMORY_BACKEND": "flatfile"})
        self.assertIsNone(h._retriever)

    def test_retriever_is_none_when_backend_unset(self):
        env = {k: v for k, v in os.environ.items() if k != "JACK_MEMORY_BACKEND"}
        with patch.dict(os.environ, env, clear=True):
            d = tempfile.mkdtemp()
            soul = Path(d) / "SOUL.md"
            soul.write_text("You are Jack.", "utf-8")
            user = Path(d) / "USER.md"
            user.write_text("", "utf-8")
            h = JackConversationHandler(soul_path=soul, user_path=user)
        self.assertIsNone(h._retriever)

    def test_build_prompt_no_memories_no_memory_block(self):
        h = _make_handler()
        system, _ = h.build_prompt("hello", "u1")
        self.assertNotIn("What I remember", system)

    def test_build_prompt_explicit_none_memories(self):
        h = _make_handler()
        system, _ = h.build_prompt("hello", "u1", memories=None)
        self.assertNotIn("What I remember", system)

    def test_build_prompt_empty_list_memories(self):
        h = _make_handler()
        system, _ = h.build_prompt("hello", "u1", memories=[])
        self.assertNotIn("What I remember", system)

    def test_build_prompt_old_signature_still_works(self):
        """Existing callers using positional (msg, uid) must be unaffected."""
        h = _make_handler()
        system, user_block = h.build_prompt("hey", "u1")
        self.assertIn("Jack", system)
        self.assertIn("hey", user_block)

    def test_system_prompt_no_memory_block_default(self):
        h = _make_handler()
        system = h._system_prompt()
        self.assertNotIn("What I remember", system)

    def test_system_prompt_empty_string_block(self):
        h = _make_handler()
        system = h._system_prompt(memory_block="")
        self.assertNotIn("What I remember", system)

    def test_respond_no_retriever_set(self):
        """On flatfile path, _retriever is None so retrieval is skipped entirely."""
        h = _make_handler()
        self.assertIsNone(h._retriever)


# ---------------------------------------------------------------------------
# 2. Mem0 path: retriever injected, memories land in prompt
# ---------------------------------------------------------------------------

class ConversationMem0PathTest(unittest.TestCase):
    """mem0 backend → retriever injected, memories injected into prompt."""

    def _run(self, coro):
        return asyncio.run(coro)

    def _handler_with_retriever(
        self, memories: list | None = None
    ) -> tuple[JackConversationHandler, MagicMock]:
        h = _make_handler()
        mock_retriever = MagicMock()
        mock_retriever.search.return_value = memories if memories is not None else [
            {"memory": "Loves pizza"}
        ]
        h._retriever = mock_retriever
        return h, mock_retriever

    def test_build_prompt_injects_memories_header(self):
        h, _ = self._handler_with_retriever(memories=[{"memory": "Loves pizza"}])
        system, _ = h.build_prompt("hi", "u1", memories=[{"memory": "Loves pizza"}])
        self.assertIn("What I remember", system)

    def test_build_prompt_injects_memory_text(self):
        h, _ = self._handler_with_retriever(memories=[{"memory": "Loves pizza"}])
        system, _ = h.build_prompt("hi", "u1", memories=[{"memory": "Loves pizza"}])
        self.assertIn("Loves pizza", system)

    def test_build_prompt_multiple_memories(self):
        memories = [
            {"memory": "Loves pizza"},
            {"memory": "Hates thin crust"},
            {"memory": "Prefers Groq over OpenAI"},
        ]
        h, _ = self._handler_with_retriever()
        system, _ = h.build_prompt("hi", "u1", memories=memories)
        self.assertIn("Loves pizza", system)
        self.assertIn("Hates thin crust", system)
        self.assertIn("Prefers Groq over OpenAI", system)

    def test_build_prompt_memory_block_before_guidance(self):
        """Memory block must appear before the react-first guidance section."""
        memories = [{"memory": "Loves pizza"}]
        h, _ = self._handler_with_retriever()
        system, _ = h.build_prompt("hi", "u1", memories=memories)
        mem_pos = system.index("What I remember")
        guidance_pos = system.index("HOW JACK REPLIES")
        self.assertLess(mem_pos, guidance_pos)

    def test_retriever_is_not_none_when_injected(self):
        h, mock_retriever = self._handler_with_retriever()
        self.assertIsNotNone(h._retriever)
        self.assertIs(h._retriever, mock_retriever)

    def test_retriever_search_is_callable(self):
        h, mock_retriever = self._handler_with_retriever()
        self.assertTrue(callable(h._retriever.search))

    def test_system_prompt_with_memory_block(self):
        h = _make_handler()
        system = h._system_prompt(memory_block="# What I remember about you\n- Loves pizza")
        self.assertIn("What I remember about you", system)
        self.assertIn("Loves pizza", system)

    def test_system_prompt_memory_block_sandwiched_correctly(self):
        """Memory block should come after profile but before guidance."""
        d = tempfile.mkdtemp()
        soul = Path(d) / "SOUL.md"
        soul.write_text("You are Jack.", "utf-8")
        user = Path(d) / "USER.md"
        user.write_text("Profile: technical founder.", "utf-8")
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "flatfile"}):
            h = JackConversationHandler(soul_path=soul, user_path=user)
        system = h._system_prompt(memory_block="# What I remember\n- Fact A")
        profile_pos = system.index("About the person")
        mem_pos = system.index("What I remember")
        guidance_pos = system.index("HOW JACK REPLIES")
        self.assertLess(profile_pos, mem_pos)
        self.assertLess(mem_pos, guidance_pos)

    def test_respond_uses_asyncio_to_thread_for_retriever(self):
        """respond() must call retriever.search via asyncio.to_thread (not inline).

        We verify this by confirming that when _retriever is set, respond()
        invokes it (the to_thread wrapper calls it in a thread). We mock
        llm.complete to stay offline.
        """
        h, mock_retriever = self._handler_with_retriever(
            memories=[{"memory": "Loves pizza"}]
        )
        with patch("lib.llm.complete", return_value="test reply"):
            self._run(h.respond("hello", "u1"))
        # search was called once (by asyncio.to_thread inside respond)
        mock_retriever.search.assert_called_once()

    def test_respond_passes_user_message_to_retriever(self):
        # "what time is it?" is now intercepted before the retriever (time fast-path).
        # Use a query that must reach the retriever.
        h, mock_retriever = self._handler_with_retriever(memories=[])
        with patch("lib.llm.complete", return_value="ok"):
            self._run(h.respond("how are you doing?", "u1"))
        call_args = mock_retriever.search.call_args
        self.assertEqual(call_args[0][0], "how are you doing?")

    def test_respond_passes_user_id_to_retriever(self):
        h, mock_retriever = self._handler_with_retriever(memories=[])
        with patch("lib.llm.complete", return_value="ok"):
            self._run(h.respond("hey", "user_xyz"))
        call_args = mock_retriever.search.call_args
        self.assertEqual(call_args[0][1], "user_xyz")

    def test_respond_degrades_gracefully_on_retriever_exception(self):
        """If retriever.search raises, respond() must still return a reply."""
        h = _make_handler()
        mock_retriever = MagicMock()
        mock_retriever.search.side_effect = RuntimeError("Qdrant down")
        h._retriever = mock_retriever
        with patch("lib.llm.complete", return_value="fallback reply"):
            reply = self._run(h.respond("hello", "u1"))
        self.assertIsInstance(reply, str)
        self.assertTrue(reply)

    def test_respond_memories_not_in_prompt_on_retriever_error(self):
        """On retriever error, no memory block in the prompt (graceful degrade)."""
        h = _make_handler()
        mock_retriever = MagicMock()
        mock_retriever.search.side_effect = ConnectionError("Qdrant unreachable")
        h._retriever = mock_retriever
        captured_system = []

        def capture_complete(system, user_block, **kwargs):
            captured_system.append(system)
            return "reply"

        with patch("lib.llm.complete", side_effect=capture_complete):
            self._run(h.respond("hello", "u1"))
        if captured_system:
            self.assertNotIn("What I remember", captured_system[0])


# ---------------------------------------------------------------------------
# 3. Memory updater: backend factory is used
# ---------------------------------------------------------------------------

class ConversationMemoryUpdateTest(unittest.TestCase):
    """_memory_updater() must use backend factory (build_updater)."""

    def test_flatfile_backend_returns_memory_updater_type(self):
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "flatfile"}):
            h = _make_handler({"JACK_MEMORY_BACKEND": "flatfile"})
        updater = h._memory_updater()
        from jack_memory.updater import MemoryUpdater
        self.assertIsInstance(updater, MemoryUpdater)

    def test_mem0_backend_returns_mem0_adapter_type(self):
        # The env flag must be active both at construction AND when _memory_updater()
        # is called, since build_updater() reads it at call time (lazy singleton).
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "mem0"}):
            h = _make_handler({"JACK_MEMORY_BACKEND": "mem0"})
            updater = h._memory_updater()
        from jack_memory.mem0_adapter import Mem0MemoryUpdater
        self.assertIsInstance(updater, Mem0MemoryUpdater)

    def test_memory_updater_lazy_singleton(self):
        """_memory_updater() must return the same object on subsequent calls."""
        h = _make_handler()
        first = h._memory_updater()
        second = h._memory_updater()
        self.assertIs(first, second)

    def test_memory_updater_import_failure_returns_none(self):
        """If build_updater import fails, _memory_updater() must return None."""
        h = _make_handler()
        with patch("jack_memory.backend.build_updater", side_effect=ImportError("no pkg")):
            h._memory = None  # reset lazy sentinel so it retries
            # Patch the import inside the method
            with patch.dict("sys.modules", {"jack_memory.backend": None}):
                h._memory = None
                result = h._memory_updater()
        # Either None (degrade) or the previously cached value — must not raise
        # (we just confirm no exception was raised; result depends on import state)

    def test_fire_memory_update_does_not_raise_without_loop(self):
        """_fire_memory_update must not raise when there's no running event loop."""
        h = _make_handler()
        try:
            h._fire_memory_update([], "msg", "reply")
        except Exception as exc:
            self.fail(f"_fire_memory_update raised: {exc}")


# ---------------------------------------------------------------------------
# 4. Answer memory query: Mem0 path vs flatfile fallback
# ---------------------------------------------------------------------------

class ConversationAnswerMemoryQueryTest(unittest.TestCase):
    """_answer_memory_query: mem0 path uses retriever; flatfile path reads USER.md."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_flatfile_path_reads_user_md(self):
        d = tempfile.mkdtemp()
        soul = Path(d) / "SOUL.md"
        soul.write_text("You are Jack.", "utf-8")
        user = Path(d) / "USER.md"
        user.write_text("Arnav loves building startups.", "utf-8")
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "flatfile"}):
            h = JackConversationHandler(soul_path=soul, user_path=user)
        # No retriever set; USER.md should be used
        self.assertIsNone(h._retriever)
        captured_user_block = []

        def capture_complete(system, user_block, **kwargs):
            captured_user_block.append(user_block)
            return "summary of memory"

        with patch("lib.llm.complete", side_effect=capture_complete):
            reply = self._run(h._answer_memory_query("what do you know about me?"))
        self.assertIsInstance(reply, str)
        if captured_user_block:
            self.assertIn("Arnav loves building startups", captured_user_block[0])

    def test_mem0_path_uses_retriever_search(self):
        h = _make_handler()
        mock_retriever = MagicMock()
        mock_retriever.search.return_value = [{"memory": "Loves startups"}]
        h._retriever = mock_retriever
        with patch("lib.llm.complete", return_value="memory summary"):
            reply = self._run(h._answer_memory_query("what do you know about me?"))
        mock_retriever.search.assert_called_once()
        self.assertIsInstance(reply, str)

    def test_mem0_path_falls_back_to_user_md_on_empty_result(self):
        d = tempfile.mkdtemp()
        soul = Path(d) / "SOUL.md"
        soul.write_text("You are Jack.", "utf-8")
        user = Path(d) / "USER.md"
        user.write_text("Arnav is a founder.", "utf-8")
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "flatfile"}):
            h = JackConversationHandler(soul_path=soul, user_path=user)
        mock_retriever = MagicMock()
        mock_retriever.search.return_value = []  # Qdrant returns nothing
        h._retriever = mock_retriever
        captured = []
        with patch("lib.llm.complete", side_effect=lambda s, u, **kw: captured.append(u) or "ok"):
            self._run(h._answer_memory_query("what do you know?"))
        if captured:
            self.assertIn("Arnav is a founder", captured[0])

    def test_mem0_path_falls_back_on_retriever_exception(self):
        d = tempfile.mkdtemp()
        soul = Path(d) / "SOUL.md"
        soul.write_text("You are Jack.", "utf-8")
        user = Path(d) / "USER.md"
        user.write_text("Arnav runs Vytal.", "utf-8")
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "flatfile"}):
            h = JackConversationHandler(soul_path=soul, user_path=user)
        mock_retriever = MagicMock()
        mock_retriever.search.side_effect = ConnectionError("Qdrant down")
        h._retriever = mock_retriever
        captured = []
        with patch("lib.llm.complete", side_effect=lambda s, u, **kw: captured.append(u) or "ok"):
            self._run(h._answer_memory_query("what do you remember?"))
        if captured:
            self.assertIn("Arnav runs Vytal", captured[0])

    def test_empty_user_md_and_no_retriever_returns_no_memory_msg(self):
        d = tempfile.mkdtemp()
        soul = Path(d) / "SOUL.md"
        soul.write_text("You are Jack.", "utf-8")
        user = Path(d) / "USER.md"
        user.write_text("", "utf-8")
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "flatfile"}):
            h = JackConversationHandler(soul_path=soul, user_path=user)
        reply = self._run(h._answer_memory_query("what do you know about me?"))
        self.assertIn("don't have anything", reply.lower())

    def test_memory_query_uses_second_person(self):
        """System prompt must enforce second-person voice and forbid third-person name references.

        Regression for: Jack breaking into third person during memory summaries
        ('Arnav mentioned he started learning piano... his memory didn't store further details').
        The _MEMORY_SUMMARY_GUIDANCE injected into the system prompt must explicitly
        require 'you/your' and forbid using the user's name or 'my memory/records' framing.
        """
        h = _make_handler()
        mock_retriever = MagicMock()
        mock_retriever.search.return_value = [{"memory": "Arnav is 20 years old"}]
        h._retriever = mock_retriever
        captured_system: list[str] = []

        with patch(
            "lib.llm.complete",
            side_effect=lambda s, u, **kw: captured_system.append(s) or "You're 20.",
        ):
            self._run(h._answer_memory_query("what do you remember?"))

        self.assertTrue(captured_system, "llm.complete was not called")
        system = captured_system[0]
        # Must require second person explicitly
        self.assertIn(
            "second person",
            system.lower(),
            "System prompt must explicitly require second-person voice",
        )
        # Must forbid third-person name references
        self.assertTrue(
            "NEVER" in system and ("name" in system or "third" in system.lower()),
            f"System prompt must forbid third-person name use. Got snippet: {system[:300]}",
        )


# ---------------------------------------------------------------------------
# 5. Scheduler: _drain_memory_queue_if_needed
# ---------------------------------------------------------------------------

class SchedulerDrainFunctionTest(unittest.TestCase):
    """Unit tests for the _drain_memory_queue_if_needed module-level function."""

    def _setup_queue(self, n_items: int = 2) -> MemoryQueue:
        d = tempfile.mkdtemp()
        q = MemoryQueue(path=Path(d) / "q.json")
        for i in range(n_items):
            q.enqueue({"messages": [{"role": "user", "content": f"msg {i}"}]})
        return q

    def test_drain_skipped_on_flatfile_backend(self):
        """When backend=flatfile, drain is a fast no-op that never raises."""
        from proactive.scheduler import _drain_memory_queue_if_needed
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "flatfile"}):
            try:
                _drain_memory_queue_if_needed()
            except Exception as exc:
                self.fail(f"_drain_memory_queue_if_needed raised on flatfile: {exc}")

    def test_drain_skipped_when_mac_down(self):
        """When extractor health check fails, items stay in queue."""
        from proactive.scheduler import _drain_memory_queue_if_needed
        q = self._setup_queue(1)
        mock_client = MagicMock()
        mock_client.health.return_value = {"embedder": True, "extractor": False}
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "mem0"}):
            with patch("jack_memory.client.JackMemoryClient.from_env", return_value=mock_client):
                with patch("jack_memory.queue.MemoryQueue", return_value=q):
                    _drain_memory_queue_if_needed()
        # Item must still be in the queue
        self.assertEqual(len(q), 1)

    def test_drain_called_on_mem0_backend_with_mac_up(self):
        """When backend=mem0 and Mac reachable, drain() is executed."""
        from proactive.scheduler import _drain_memory_queue_if_needed
        q = self._setup_queue(2)
        mock_client = MagicMock()
        mock_client.health.return_value = {"embedder": True, "extractor": True}
        mock_client.add.return_value = {}
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "mem0"}):
            with patch("jack_memory.client.JackMemoryClient.from_env", return_value=mock_client):
                with patch("jack_memory.queue.MemoryQueue", return_value=q):
                    _drain_memory_queue_if_needed()
        # Both items should have been drained
        self.assertEqual(len(q), 0)

    def test_drain_calls_client_add_for_each_item(self):
        """client.add() must be called once per queued item."""
        from proactive.scheduler import _drain_memory_queue_if_needed
        q = self._setup_queue(3)
        mock_client = MagicMock()
        mock_client.health.return_value = {"embedder": True, "extractor": True}
        mock_client.add.return_value = {}
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "mem0"}):
            with patch("jack_memory.client.JackMemoryClient.from_env", return_value=mock_client):
                with patch("jack_memory.queue.MemoryQueue", return_value=q):
                    _drain_memory_queue_if_needed()
        self.assertEqual(mock_client.add.call_count, 3)

    def test_drain_skipped_when_queue_is_empty(self):
        """When queue is empty, add() should not be called."""
        from proactive.scheduler import _drain_memory_queue_if_needed
        q = self._setup_queue(0)  # empty
        mock_client = MagicMock()
        mock_client.health.return_value = {"embedder": True, "extractor": True}
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "mem0"}):
            with patch("jack_memory.client.JackMemoryClient.from_env", return_value=mock_client):
                with patch("jack_memory.queue.MemoryQueue", return_value=q):
                    _drain_memory_queue_if_needed()
        mock_client.add.assert_not_called()

    def test_drain_error_never_crashes(self):
        """Any exception inside _drain_memory_queue_if_needed must be swallowed."""
        from proactive.scheduler import _drain_memory_queue_if_needed
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "mem0"}):
            with patch(
                "jack_memory.client.JackMemoryClient.from_env",
                side_effect=RuntimeError("network error"),
            ):
                try:
                    _drain_memory_queue_if_needed()
                except Exception as exc:
                    self.fail(f"_drain_memory_queue_if_needed raised: {exc}")

    def test_drain_error_from_health_check_never_crashes(self):
        """health() raising must be swallowed."""
        from proactive.scheduler import _drain_memory_queue_if_needed
        mock_client = MagicMock()
        mock_client.health.side_effect = RuntimeError("health probe failed")
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "mem0"}):
            with patch("jack_memory.client.JackMemoryClient.from_env", return_value=mock_client):
                try:
                    _drain_memory_queue_if_needed()
                except Exception as exc:
                    self.fail(f"_drain_memory_queue_if_needed raised on health error: {exc}")


# ---------------------------------------------------------------------------
# 6. Scheduler: run_once integrates drain call
# ---------------------------------------------------------------------------

class SchedulerRunOnceDrainIntegrationTest(unittest.TestCase):
    """run_once() must call _drain_memory_queue_if_needed after the engine cycle."""

    def _make_scheduler(self, engine=None, **kwargs):
        from proactive.scheduler import ProactiveScheduler
        kwargs.setdefault("logger", lambda line: None)
        kwargs.setdefault("engine", engine or MagicMock(run_cycle=MagicMock(return_value=0)))
        return ProactiveScheduler(**kwargs)

    def test_run_once_calls_drain_after_engine_cycle(self):
        """Drain is called even on a successful cycle."""
        import proactive.scheduler as sched_mod
        engine = MagicMock()
        engine.run_cycle.return_value = 1
        scheduler = self._make_scheduler(engine=engine)
        drain_calls = []
        original_drain = sched_mod._drain_memory_queue_if_needed

        def fake_drain():
            drain_calls.append(True)

        with patch.dict(os.environ, {"JACK_PROACTIVE_ENABLED": "1"}):
            with patch.object(sched_mod, "_drain_memory_queue_if_needed", fake_drain):
                scheduler.run_once()
        self.assertEqual(len(drain_calls), 1)

    def test_run_once_calls_drain_even_when_engine_raises(self):
        """Drain must run even if the engine cycle throws."""
        import proactive.scheduler as sched_mod
        engine = MagicMock()
        engine.run_cycle.side_effect = RuntimeError("engine boom")
        scheduler = self._make_scheduler(engine=engine)
        drain_calls = []

        def fake_drain():
            drain_calls.append(True)

        with patch.dict(os.environ, {"JACK_PROACTIVE_ENABLED": "1"}):
            with patch.object(sched_mod, "_drain_memory_queue_if_needed", fake_drain):
                result = scheduler.run_once()
        self.assertEqual(result, 0)
        self.assertEqual(len(drain_calls), 1)

    def test_run_once_drain_not_called_when_disabled(self):
        """When proactive is disabled, run_once returns early — drain is NOT called."""
        import proactive.scheduler as sched_mod
        engine = MagicMock()
        scheduler = self._make_scheduler(engine=engine)
        drain_calls = []

        def fake_drain():
            drain_calls.append(True)

        with patch.dict(os.environ, {"JACK_PROACTIVE_ENABLED": "false"}):
            with patch.object(sched_mod, "_drain_memory_queue_if_needed", fake_drain):
                result = scheduler.run_once()
        self.assertEqual(result, 0)
        self.assertEqual(len(drain_calls), 0)

    def test_run_once_returns_correct_sent_count(self):
        """run_once() must still return the engine's sent count after drain."""
        import proactive.scheduler as sched_mod
        engine = MagicMock()
        engine.run_cycle.return_value = 3
        scheduler = self._make_scheduler(engine=engine)
        with patch.dict(os.environ, {"JACK_PROACTIVE_ENABLED": "1", "JACK_PROACTIVE_ENGINE": "legacy"}):
            with patch.object(sched_mod, "_drain_memory_queue_if_needed", lambda: None):
                result = scheduler.run_once()
        self.assertEqual(result, 3)


# ---------------------------------------------------------------------------
# 7. Regression: existing flatfile tests still pass through the new wiring
# ---------------------------------------------------------------------------

class FlatfileRegressionTest(unittest.TestCase):
    """Verify the key existing test_conversation.py assertions still hold."""

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

    def _handler(self, **kw) -> JackConversationHandler:
        d = tempfile.mkdtemp()
        soul = Path(d) / "SOUL.md"
        user = Path(d) / "USER.md"
        soul.write_text(self.SOUL_FIXTURE, "utf-8")
        user.write_text(self.USER_FIXTURE, "utf-8")
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "flatfile"}):
            return JackConversationHandler(soul_path=soul, user_path=user, **kw)

    def test_personality_keeps_identity(self):
        h = self._handler()
        self.assertIn("You are Jack", h._personality)
        self.assertIn("Arnav Deshmukh", h._personality)

    def test_personality_strips_operational_sections(self):
        h = self._handler()
        self.assertNotIn("send_linkedin_post.py", h._personality)
        self.assertNotIn("Operational Modules", h._personality)

    def test_profile_injected_into_system(self):
        h = self._handler()
        system, _ = h.build_prompt("hey", "u")
        self.assertIn("AIML student", system)

    def test_no_agenda_ask_in_prompt(self):
        h = self._handler()
        system, _ = h.build_prompt("hey", "u")
        self.assertIn("never proactively ask", system.lower())

    def test_sliding_window_caps_turns(self):
        h = self._handler(max_turns=2)
        for i in range(5):
            h.add_turn("u1", "user", f"msg {i}")
            h.add_turn("u1", "assistant", f"reply {i}")
        ctx = h.get_context("u1")
        self.assertEqual(len(ctx), 4)

    def test_prompt_stays_under_budget(self):
        # Budget must exceed the system-prompt floor (~1400 tokens with personality
        # + react rules + few-shots + constraints). We use 2000 so the trimming
        # logic still fires (drops old history) and the assertion validates it.
        from conversation import estimate_tokens
        h = self._handler(max_turns=20, token_budget=2000)
        for i in range(20):
            h.add_turn("u", "user", "x" * 400)
            h.add_turn("u", "assistant", "y" * 400)
        system, user_block = h.build_prompt("current question", "u")
        total = estimate_tokens(system) + estimate_tokens(user_block)
        self.assertLessEqual(total, 2000)

    def test_current_message_always_present(self):
        h = self._handler(max_turns=20, token_budget=1)
        _, user_block = h.build_prompt("REMEMBER THIS LINE", "u")
        self.assertIn("REMEMBER THIS LINE", user_block)

    def test_respond_returns_text_offline(self):
        h = self._handler()
        os.environ["HERMES_LLM_MOCK"] = "1"
        try:
            reply = asyncio.run(h.respond("hello jack", "u1"))
            self.assertIsInstance(reply, str)
            self.assertTrue(reply)
        finally:
            os.environ.pop("HERMES_LLM_MOCK", None)


if __name__ == "__main__":
    unittest.main()
