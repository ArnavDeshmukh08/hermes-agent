"""Tests for jack_memory.backend — JACK_MEMORY_BACKEND flag factory."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

import jack_memory.backend as backend_mod


class BackendFlagTest(unittest.TestCase):
    def test_default_is_flatfile(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JACK_MEMORY_BACKEND", None)
            self.assertEqual(backend_mod.memory_backend(), "flatfile")

    def test_mem0_when_set(self):
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "mem0"}):
            self.assertEqual(backend_mod.memory_backend(), "mem0")

    def test_flatfile_explicit(self):
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "flatfile"}):
            self.assertEqual(backend_mod.memory_backend(), "flatfile")

    def test_case_insensitive(self):
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "MEM0"}):
            self.assertEqual(backend_mod.memory_backend(), "mem0")

    def test_whitespace_stripped(self):
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "  mem0  "}):
            self.assertEqual(backend_mod.memory_backend(), "mem0")

    def test_build_updater_returns_legacy_on_flatfile(self):
        from jack_memory.updater import MemoryUpdater
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "flatfile"}):
            d = tempfile.mkdtemp()
            up = backend_mod.build_updater(Path(d) / "USER.md")
        self.assertIsInstance(up, MemoryUpdater)

    def test_build_updater_returns_mem0_adapter_on_mem0(self):
        from jack_memory.mem0_adapter import Mem0MemoryUpdater
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "mem0"}):
            up = backend_mod.build_updater(
                Path("/tmp/fake.md"),
                client=MagicMock(),
                queue=MagicMock(),
            )
        self.assertIsInstance(up, Mem0MemoryUpdater)

    def test_build_retriever_none_on_flatfile(self):
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "flatfile"}):
            self.assertIsNone(backend_mod.build_retriever())

    def test_build_retriever_returns_client_on_mem0(self):
        mock_client = MagicMock()
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "mem0"}):
            result = backend_mod.build_retriever(client=mock_client)
        self.assertIs(result, mock_client)

    def test_build_updater_flatfile_has_run_async(self):
        """Legacy updater returned by build_updater must have run_async."""
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "flatfile"}):
            d = tempfile.mkdtemp()
            up = backend_mod.build_updater(Path(d) / "USER.md")
        self.assertTrue(hasattr(up, "run_async"))
        self.assertTrue(callable(up.run_async))

    def test_build_updater_mem0_has_run_async(self):
        """Mem0 updater returned by build_updater must have run_async."""
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "mem0"}):
            up = backend_mod.build_updater(
                Path("/tmp/fake.md"),
                client=MagicMock(),
                queue=MagicMock(),
            )
        self.assertTrue(hasattr(up, "run_async"))
        self.assertTrue(callable(up.run_async))

    def test_build_retriever_flatfile_does_not_import_client(self):
        """flatfile backend must not attempt to build JackMemoryClient."""
        with patch.dict(os.environ, {"JACK_MEMORY_BACKEND": "flatfile"}):
            # Should not raise even if jack_memory.client doesn't exist
            result = backend_mod.build_retriever()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
