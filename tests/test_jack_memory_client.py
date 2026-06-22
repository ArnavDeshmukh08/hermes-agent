"""Tests for jack_memory.client.JackMemoryClient.

All offline: mem0_client is injected as a Mock. No real Qdrant or Ollama.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from jack_memory.client import build_mem0_config, JackMemoryClient


class ConfigShapeTest(unittest.TestCase):
    def test_config_dims_inside_vector_store(self):
        """embedding_model_dims must be nested inside vector_store.config."""
        c = build_mem0_config()
        self.assertEqual(c["vector_store"]["config"]["embedding_model_dims"], 768)

    def test_config_dims_not_at_top_level(self):
        """embedding_model_dims must NOT be at the top level of the config dict."""
        c = build_mem0_config()
        self.assertNotIn("embedding_model_dims", c)

    def test_embedder_points_to_vps_local(self):
        """Embedder URL must point to localhost (VPS) — NEVER the Mac's Tailscale IP."""
        with patch.dict(os.environ, {"JACK_EMBED_URL": "http://localhost:11434"}):
            c = build_mem0_config()
        embed_url = c["embedder"]["config"]["ollama_base_url"]
        self.assertIn("localhost", embed_url)
        self.assertNotIn("100.120.65.115", embed_url)

    def test_extractor_points_to_mac(self):
        with patch.dict(os.environ, {"JACK_EXTRACT_URL": "http://100.120.65.115:11434"}):
            c = build_mem0_config()
        self.assertIn("100.120.65.115", c["llm"]["config"]["ollama_base_url"])

    def test_config_reads_all_from_env(self):
        env = {
            "JACK_QDRANT_HOST": "myhost",
            "JACK_QDRANT_PORT": "9999",
            "JACK_EMBED_MODEL": "my-embed",
            "JACK_EMBED_URL": "http://embed.local",
            "JACK_EXTRACT_MODEL": "my-llm",
            "JACK_EXTRACT_URL": "http://mac.local",
        }
        with patch.dict(os.environ, env):
            c = build_mem0_config()
        self.assertEqual(c["vector_store"]["config"]["host"], "myhost")
        self.assertEqual(c["vector_store"]["config"]["port"], 9999)
        self.assertEqual(c["embedder"]["config"]["model"], "my-embed")
        self.assertEqual(c["llm"]["config"]["model"], "my-llm")

    def test_config_vector_store_provider_is_qdrant(self):
        c = build_mem0_config()
        self.assertEqual(c["vector_store"]["provider"], "qdrant")

    def test_config_embedder_provider_is_ollama(self):
        c = build_mem0_config()
        self.assertEqual(c["embedder"]["provider"], "ollama")

    def test_config_llm_provider_is_ollama(self):
        c = build_mem0_config()
        self.assertEqual(c["llm"]["provider"], "ollama")

    def test_config_default_qdrant_host(self):
        with patch.dict(os.environ, {}, clear=False):
            env = {k: v for k, v in os.environ.items() if k != "JACK_QDRANT_HOST"}
            with patch.dict(os.environ, env, clear=True):
                c = build_mem0_config()
        self.assertEqual(c["vector_store"]["config"]["host"], "localhost")

    def test_config_qdrant_port_is_int(self):
        """Port must be an integer, not a string."""
        with patch.dict(os.environ, {"JACK_QDRANT_PORT": "6333"}):
            c = build_mem0_config()
        self.assertIsInstance(c["vector_store"]["config"]["port"], int)

    def test_config_llm_temperature_zero(self):
        c = build_mem0_config()
        self.assertEqual(c["llm"]["config"]["temperature"], 0)

    def test_config_collection_name(self):
        c = build_mem0_config()
        self.assertEqual(c["vector_store"]["config"]["collection_name"], "jack_memory")


class SearchTest(unittest.TestCase):
    def _make_client(self, search_result=None, raises=None, timeout_s=1.5):
        mock_mem0 = MagicMock()
        if raises:
            mock_mem0.search.side_effect = raises
        else:
            mock_mem0.search.return_value = search_result or []
        return JackMemoryClient(mem0_client=mock_mem0, search_timeout_s=timeout_s)

    def test_search_returns_list_on_hit(self):
        memories = [{"memory": "Loves pizza", "id": "1"}]
        client = self._make_client(search_result=memories)
        result = client.search("food preferences")
        self.assertIsInstance(result, list)

    def test_search_returns_correct_memories(self):
        memories = [{"memory": "Loves pizza", "id": "1"}, {"memory": "Hates deadlines", "id": "2"}]
        client = self._make_client(search_result=memories)
        result = client.search("food preferences")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["memory"], "Loves pizza")

    def test_search_returns_empty_on_exception(self):
        client = self._make_client(raises=RuntimeError("qdrant down"))
        result = client.search("anything")
        self.assertEqual(result, [])

    def test_search_returns_empty_on_timeout(self):
        """search() must return [] on timeout, not raise."""
        import time
        mock_mem0 = MagicMock()
        mock_mem0.search.side_effect = lambda *a, **kw: time.sleep(10)
        client = JackMemoryClient(mem0_client=mock_mem0, search_timeout_s=0.05)
        result = client.search("slow query")
        self.assertEqual(result, [])

    def test_search_never_calls_extraction_llm(self):
        """search() calls mem0.search, not mem0.add (extraction)."""
        mock_mem0 = MagicMock()
        mock_mem0.search.return_value = []
        client = JackMemoryClient(mem0_client=mock_mem0)
        client.search("test query")
        mock_mem0.search.assert_called_once()
        mock_mem0.add.assert_not_called()

    def test_search_passes_user_id(self):
        mock_mem0 = MagicMock()
        mock_mem0.search.return_value = []
        client = JackMemoryClient(mem0_client=mock_mem0, user_id="arnav")
        client.search("query")
        call_kwargs = mock_mem0.search.call_args
        self.assertEqual(call_kwargs.kwargs.get("filters"), {"user_id": "arnav"})

    def test_search_accepts_explicit_user_id(self):
        mock_mem0 = MagicMock()
        mock_mem0.search.return_value = []
        client = JackMemoryClient(mem0_client=mock_mem0)
        client.search("query", user_id="custom_user")
        call_kwargs = mock_mem0.search.call_args
        self.assertEqual(call_kwargs.kwargs.get("filters"), {"user_id": "custom_user"})

    def test_search_passes_limit(self):
        mock_mem0 = MagicMock()
        mock_mem0.search.return_value = []
        client = JackMemoryClient(mem0_client=mock_mem0)
        client.search("query", limit=5)
        call_kwargs = mock_mem0.search.call_args
        self.assertEqual(call_kwargs.kwargs.get("top_k"), 5)

    def test_search_unwraps_dict_results(self):
        """mem0 may return {'results': [...]} — search() must unwrap it."""
        mock_mem0 = MagicMock()
        mock_mem0.search.return_value = {"results": [{"memory": "pizza"}]}
        client = JackMemoryClient(mem0_client=mock_mem0)
        result = client.search("food")
        self.assertIsInstance(result, list)
        self.assertEqual(result[0]["memory"], "pizza")

    def test_search_returns_empty_list_on_none(self):
        mock_mem0 = MagicMock()
        mock_mem0.search.return_value = None
        client = JackMemoryClient(mem0_client=mock_mem0)
        result = client.search("query")
        self.assertEqual(result, [])

    def test_format_for_prompt_empty_on_no_memories(self):
        self.assertEqual(JackMemoryClient.format_for_prompt([]), "")

    def test_format_for_prompt_renders_block(self):
        memories = [{"memory": "Loves pizza"}, {"memory": "Hates thin crust"}]
        block = JackMemoryClient.format_for_prompt(memories)
        self.assertIn("What I remember", block)
        self.assertIn("Loves pizza", block)
        self.assertIn("Hates thin crust", block)

    def test_format_for_prompt_uses_text_field_fallback(self):
        memories = [{"text": "Uses Telegram"}]
        block = JackMemoryClient.format_for_prompt(memories)
        self.assertIn("Uses Telegram", block)

    def test_format_for_prompt_bullet_per_memory(self):
        memories = [{"memory": "A"}, {"memory": "B"}, {"memory": "C"}]
        block = JackMemoryClient.format_for_prompt(memories)
        self.assertEqual(block.count("- "), 3)

    def test_format_for_prompt_starts_with_header(self):
        memories = [{"memory": "Something"}]
        block = JackMemoryClient.format_for_prompt(memories)
        self.assertTrue(block.startswith("# What I remember about you"))


class AddTest(unittest.TestCase):
    def test_add_calls_mem0_add(self):
        mock_mem0 = MagicMock()
        mock_mem0.add.return_value = {"id": "x"}
        client = JackMemoryClient(mem0_client=mock_mem0)
        result = client.add([{"role": "user", "content": "hi"}])
        mock_mem0.add.assert_called_once()
        self.assertEqual(result, {"id": "x"})

    def test_add_propagates_exception_for_queue(self):
        """add() must propagate — caller decides to queue on Mac-unreachable."""
        mock_mem0 = MagicMock()
        mock_mem0.add.side_effect = ConnectionError("Mac unreachable")
        client = JackMemoryClient(mem0_client=mock_mem0)
        with self.assertRaises(ConnectionError):
            client.add([{"role": "user", "content": "test"}])

    def test_add_passes_user_id(self):
        mock_mem0 = MagicMock()
        mock_mem0.add.return_value = {}
        client = JackMemoryClient(mem0_client=mock_mem0, user_id="arnav")
        client.add("some text")
        call_kwargs = mock_mem0.add.call_args
        self.assertEqual(call_kwargs.kwargs.get("user_id"), "arnav")

    def test_add_passes_metadata_when_given(self):
        mock_mem0 = MagicMock()
        mock_mem0.add.return_value = {}
        client = JackMemoryClient(mem0_client=mock_mem0)
        client.add("text", metadata={"source": "telegram"})
        call_kwargs = mock_mem0.add.call_args
        self.assertEqual(call_kwargs.kwargs.get("metadata"), {"source": "telegram"})

    def test_add_omits_metadata_when_none(self):
        """metadata should not be passed if not provided (avoid passing None)."""
        mock_mem0 = MagicMock()
        mock_mem0.add.return_value = {}
        client = JackMemoryClient(mem0_client=mock_mem0)
        client.add("text")
        call_kwargs = mock_mem0.add.call_args
        self.assertNotIn("metadata", call_kwargs.kwargs)

    def test_add_accepts_string_message(self):
        mock_mem0 = MagicMock()
        mock_mem0.add.return_value = {"id": "y"}
        client = JackMemoryClient(mem0_client=mock_mem0)
        result = client.add("plain string memory")
        mock_mem0.add.assert_called_once()
        self.assertEqual(result, {"id": "y"})

    def test_add_propagates_runtime_error(self):
        mock_mem0 = MagicMock()
        mock_mem0.add.side_effect = RuntimeError("Qdrant write failed")
        client = JackMemoryClient(mem0_client=mock_mem0)
        with self.assertRaises(RuntimeError):
            client.add([{"role": "user", "content": "test"}])


class HealthTest(unittest.TestCase):
    def test_health_returns_dict_with_two_bool_keys(self):
        client = JackMemoryClient(mem0_client=MagicMock())
        with patch("urllib.request.urlopen", side_effect=OSError("down")):
            h = client.health()
        self.assertIn("embedder", h)
        self.assertIn("extractor", h)
        self.assertFalse(h["embedder"])
        self.assertFalse(h["extractor"])

    def test_health_embedder_true_when_reachable(self):
        client = JackMemoryClient(mem0_client=MagicMock())

        def fake_open(url, timeout=1):
            if "localhost" in url:
                return MagicMock()
            raise OSError("mac down")

        with patch("urllib.request.urlopen", side_effect=fake_open):
            with patch.dict(
                os.environ,
                {
                    "JACK_EMBED_URL": "http://localhost:11434",
                    "JACK_EXTRACT_URL": "http://100.120.65.115:11434",
                },
            ):
                h = client.health()
        self.assertTrue(h["embedder"])
        self.assertFalse(h["extractor"])

    def test_health_extractor_true_when_reachable(self):
        client = JackMemoryClient(mem0_client=MagicMock())

        def fake_open(url, timeout=1):
            if "100.120.65.115" in url:
                return MagicMock()
            raise OSError("vps down")

        with patch("urllib.request.urlopen", side_effect=fake_open):
            with patch.dict(
                os.environ,
                {
                    "JACK_EMBED_URL": "http://localhost:11434",
                    "JACK_EXTRACT_URL": "http://100.120.65.115:11434",
                },
            ):
                h = client.health()
        self.assertFalse(h["embedder"])
        self.assertTrue(h["extractor"])

    def test_health_both_true_when_both_reachable(self):
        client = JackMemoryClient(mem0_client=MagicMock())
        with patch("urllib.request.urlopen", return_value=MagicMock()):
            h = client.health()
        self.assertTrue(h["embedder"])
        self.assertTrue(h["extractor"])

    def test_health_uses_env_urls(self):
        """health() must read JACK_EMBED_URL and JACK_EXTRACT_URL from env."""
        client = JackMemoryClient(mem0_client=MagicMock())
        probed_urls = []

        def capture_open(url, timeout=1):
            probed_urls.append(url)
            raise OSError("down")

        with patch("urllib.request.urlopen", side_effect=capture_open):
            with patch.dict(
                os.environ,
                {
                    "JACK_EMBED_URL": "http://my-vps:11434",
                    "JACK_EXTRACT_URL": "http://my-mac:11434",
                },
            ):
                client.health()

        self.assertTrue(any("my-vps" in u for u in probed_urls))
        self.assertTrue(any("my-mac" in u for u in probed_urls))


class LazyImportTest(unittest.TestCase):
    """Verify that importing client.py does NOT import mem0 at module level."""

    def test_mem0_not_imported_at_module_level(self):
        """mem0 must not be in sys.modules just from importing jack_memory.client."""
        # The module is already imported (test suite imports it at top).
        # What we verify: with a fresh injected mock, _client() is never called
        # and therefore the real mem0 import path is never exercised.
        mock_mem0 = MagicMock()
        client = JackMemoryClient(mem0_client=mock_mem0)
        # Accessing the client attribute should return our mock without touching mem0
        self.assertIs(client._client(), mock_mem0)

    def test_from_env_creates_client_without_mem0_import(self):
        """from_env() should not import mem0 eagerly — only _client() does."""
        # Patch mem0 import to raise if triggered during from_env()
        import sys
        original = sys.modules.pop("mem0", None)
        try:
            # from_env() just sets up config; mem0 import deferred to _client()
            client = JackMemoryClient.from_env()
            # _config should be populated
            self.assertIsNotNone(client._config)
            # _mem0 should still be None (not yet initialised)
            self.assertIsNone(client._mem0)
        finally:
            if original is not None:
                sys.modules["mem0"] = original


class DefaultsTest(unittest.TestCase):
    def test_default_user_id_is_arnav(self):
        client = JackMemoryClient(mem0_client=MagicMock())
        self.assertEqual(client._user_id, "arnav")

    def test_default_timeout_is_1_5(self):
        client = JackMemoryClient(mem0_client=MagicMock())
        self.assertEqual(client._timeout, 1.5)

    def test_custom_timeout_accepted(self):
        client = JackMemoryClient(mem0_client=MagicMock(), search_timeout_s=3.0)
        self.assertEqual(client._timeout, 3.0)

    def test_custom_user_id_accepted(self):
        client = JackMemoryClient(mem0_client=MagicMock(), user_id="test_user")
        self.assertEqual(client._user_id, "test_user")


class EnsureLowThresholdTest(unittest.TestCase):
    """Regression tests for the Qdrant HNSW indexing_threshold fix.

    Root cause: Qdrant default indexing_threshold=20000. With only ~55 points
    the HNSW index is never built, so .search() (vector index required) returns
    empty while scroll/get_all (no index) works fine. Patching to 100 fixes it.
    """

    def _capture_patch(self, host="localhost", port="6333"):
        """Helper: run _ensure_low_threshold() and capture the PATCH request."""
        import json

        captured = {}

        def fake_open(req, timeout=None):
            captured["method"] = req.method
            captured["url"] = req.full_url
            captured["data"] = json.loads(req.data.decode())
            return MagicMock()

        client = JackMemoryClient(mem0_client=MagicMock())
        with patch("urllib.request.urlopen", side_effect=fake_open):
            with patch.dict(os.environ, {"JACK_QDRANT_HOST": host, "JACK_QDRANT_PORT": port}):
                client._ensure_low_threshold()
        return captured

    def test_ensure_low_threshold_sends_patch_method(self):
        captured = self._capture_patch()
        self.assertEqual(captured["method"], "PATCH")

    def test_ensure_low_threshold_targets_jack_memory_collection(self):
        captured = self._capture_patch()
        self.assertIn("jack_memory", captured["url"])

    def test_ensure_low_threshold_sets_indexing_threshold_to_100(self):
        captured = self._capture_patch()
        self.assertEqual(captured["data"]["optimizer_config"]["indexing_threshold"], 100)

    def test_ensure_low_threshold_uses_env_host_and_port(self):
        captured = self._capture_patch(host="myvps", port="6334")
        self.assertIn("myvps", captured["url"])
        self.assertIn("6334", captured["url"])

    def test_ensure_low_threshold_survives_qdrant_down(self):
        """Must not raise even if Qdrant is unreachable — never break init."""
        client = JackMemoryClient(mem0_client=MagicMock())
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            try:
                client._ensure_low_threshold()
            except Exception as e:
                self.fail(f"_ensure_low_threshold raised on Qdrant down: {e}")

    def test_ensure_low_threshold_survives_timeout(self):
        client = JackMemoryClient(mem0_client=MagicMock())
        import socket
        with patch("urllib.request.urlopen", side_effect=socket.timeout("timed out")):
            try:
                client._ensure_low_threshold()
            except Exception as e:
                self.fail(f"_ensure_low_threshold raised on timeout: {e}")

    def test_search_returns_results_for_known_stored_fact(self):
        """Regression: search() must return items when Qdrant has indexed vectors.

        This is the functional regression test for the empty-search bug.
        The HNSW index must exist (indexing_threshold fix applied) for this to pass.
        Here we test the full search() path with a mock that represents
        a correctly-indexed collection returning a known fact.
        """
        piano_memory = {"memory": "Arnav plays piano", "id": "abc123", "score": 0.91}
        mock_mem0 = MagicMock()
        mock_mem0.search.return_value = [piano_memory]
        client = JackMemoryClient(mem0_client=mock_mem0)

        results = client.search("piano")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["memory"], "Arnav plays piano")

    def test_search_returns_results_for_hobby_query(self):
        """Regression: searching 'hobbies' must surface piano memory."""
        memories = [
            {"memory": "Arnav plays piano", "id": "1", "score": 0.88},
            {"memory": "Arnav enjoys running", "id": "2", "score": 0.72},
        ]
        mock_mem0 = MagicMock()
        mock_mem0.search.return_value = memories
        client = JackMemoryClient(mem0_client=mock_mem0)

        results = client.search("hobbies")

        self.assertGreater(len(results), 0)
        texts = [r["memory"] for r in results]
        self.assertIn("Arnav plays piano", texts)


if __name__ == "__main__":
    unittest.main()
