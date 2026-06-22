"""Tests for jack_memory.migrate — USER.md → Mem0 migration."""

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from jack_memory.migrate import backup_user_md, migrate, parse_sections

# Use the existing seed as our test fixture
_SEED = Path(_REPO_ROOT) / "secrets" / "USER.md.seed"
_FIXTURE = (
    _SEED.read_text("utf-8")
    if _SEED.exists()
    else """
[IDENTITY]
Name: Arnav Deshmukh
Age: 20

[RELATIONSHIPS]
Girlfriend: Siddhi

[GOALS]
Short term: Launch Vytal

[THINGS JACK HAS LEARNED]
2026-06-19: First learned item.
"""
)


class BackupTest(unittest.TestCase):
    def _user_md(self) -> Path:
        d = tempfile.mkdtemp()
        p = Path(d) / "USER.md"
        p.write_text(_FIXTURE, "utf-8")
        return p

    def test_backup_created(self):
        user_md = self._user_md()
        bak = backup_user_md(user_md)
        self.assertTrue(bak.exists())
        self.assertIn("premigration.bak", bak.name)

    def test_backup_matches_original(self):
        user_md = self._user_md()
        bak = backup_user_md(user_md)
        self.assertEqual(user_md.read_bytes(), bak.read_bytes())

    def test_backup_not_overwritten_if_exists(self):
        user_md = self._user_md()
        bak = backup_user_md(user_md)
        bak.write_text("original backup", "utf-8")
        backup_user_md(user_md)  # second call
        self.assertEqual(bak.read_text("utf-8"), "original backup")

    def test_backup_in_same_directory(self):
        user_md = self._user_md()
        bak = backup_user_md(user_md)
        self.assertEqual(bak.parent, user_md.parent)

    def test_backup_returns_path(self):
        user_md = self._user_md()
        bak = backup_user_md(user_md)
        self.assertIsInstance(bak, Path)


class ParseSectionsTest(unittest.TestCase):
    def test_parses_all_known_sections(self):
        pairs = parse_sections(_FIXTURE)
        sections = {s for s, _ in pairs}
        for expected in ("IDENTITY", "RELATIONSHIPS", "GOALS"):
            self.assertIn(expected, sections, f"Section [{expected}] not parsed")

    def test_does_not_include_section_headers(self):
        pairs = parse_sections(_FIXTURE)
        for section, line in pairs:
            self.assertFalse(line.startswith("["), f"Header leaked: {line}")

    def test_empty_lines_excluded(self):
        pairs = parse_sections(_FIXTURE)
        for section, line in pairs:
            self.assertTrue(line.strip(), "Empty line leaked into pairs")

    def test_returns_list_of_tuples(self):
        pairs = parse_sections(_FIXTURE)
        self.assertIsInstance(pairs, list)
        for item in pairs:
            self.assertIsInstance(item, tuple)
            self.assertEqual(len(item), 2)

    def test_things_learned_parsed(self):
        pairs = parse_sections(_FIXTURE)
        sections = {s for s, _ in pairs}
        self.assertIn("THINGS JACK HAS LEARNED", sections)

    def test_empty_text_returns_empty(self):
        self.assertEqual(parse_sections(""), [])

    def test_text_with_no_sections_returns_empty(self):
        self.assertEqual(parse_sections("Just some text\nNo sections here"), [])

    def test_comments_excluded(self):
        text = "[IDENTITY]\nName: Test\n# this is a comment\n"
        pairs = parse_sections(text)
        for _, line in pairs:
            self.assertFalse(line.startswith("#"), f"Comment leaked: {line}")


class MigrateTest(unittest.TestCase):
    def _setup(self) -> tuple[Path, Path]:
        d = tempfile.mkdtemp()
        user_md = Path(d) / "USER.md"
        user_md.write_text(_FIXTURE, "utf-8")
        marker = Path(d) / ".mem0_migrated"
        return user_md, marker

    def test_dry_run_calls_no_client_methods(self):
        user_md, marker = self._setup()
        mock_client = MagicMock()
        result = migrate(user_md, mock_client, marker_path=marker, dry_run=True)
        self.assertEqual(result["status"], "dry_run")
        mock_client.add.assert_not_called()
        self.assertFalse(marker.exists())

    def test_user_md_unchanged_after_migration(self):
        user_md, marker = self._setup()
        original = user_md.read_bytes()
        mock_client = MagicMock()
        migrate(user_md, mock_client, marker_path=marker)
        self.assertEqual(user_md.read_bytes(), original)  # byte-identical

    def test_migration_idempotent(self):
        user_md, marker = self._setup()
        mock_client = MagicMock()
        migrate(user_md, mock_client, marker_path=marker)
        first_count = mock_client.add.call_count
        migrate(user_md, mock_client, marker_path=marker)
        second_count = mock_client.add.call_count
        self.assertEqual(first_count, second_count)  # second run is no-op

    def test_marker_written_after_commit(self):
        user_md, marker = self._setup()
        mock_client = MagicMock()
        migrate(user_md, mock_client, marker_path=marker)
        self.assertTrue(marker.exists())

    def test_skipped_if_marker_exists(self):
        user_md, marker = self._setup()
        marker.write_text("already done", "utf-8")
        mock_client = MagicMock()
        result = migrate(user_md, mock_client, marker_path=marker)
        self.assertEqual(result["status"], "skipped")
        mock_client.add.assert_not_called()

    def test_backup_preserves_user_md(self):
        user_md, marker = self._setup()
        bak = backup_user_md(user_md)
        self.assertTrue(bak.exists())
        self.assertEqual(user_md.read_bytes(), bak.read_bytes())

    def test_commit_calls_client_add_for_each_item(self):
        user_md, marker = self._setup()
        mock_client = MagicMock()
        pairs = parse_sections(_FIXTURE)
        result = migrate(user_md, mock_client, marker_path=marker)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(mock_client.add.call_count, len(pairs))

    def test_commit_returns_counts_per_section(self):
        user_md, marker = self._setup()
        mock_client = MagicMock()
        result = migrate(user_md, mock_client, marker_path=marker)
        self.assertIsInstance(result["counts"], dict)
        self.assertGreater(len(result["counts"]), 0)

    def test_commit_total_matches_sum_of_counts(self):
        user_md, marker = self._setup()
        mock_client = MagicMock()
        result = migrate(user_md, mock_client, marker_path=marker)
        self.assertEqual(result["total"], sum(result["counts"].values()))

    def test_error_on_missing_user_md(self):
        d = tempfile.mkdtemp()
        missing = Path(d) / "NONEXISTENT.md"
        marker = Path(d) / ".mem0_migrated"
        mock_client = MagicMock()
        result = migrate(missing, mock_client, marker_path=marker)
        self.assertEqual(result["status"], "error")
        mock_client.add.assert_not_called()

    def test_client_add_called_with_messages_list(self):
        """Each client.add call must receive a messages list as first arg."""
        user_md, marker = self._setup()
        mock_client = MagicMock()
        migrate(user_md, mock_client, marker_path=marker)
        for call in mock_client.add.call_args_list:
            args, kwargs = call
            self.assertIsInstance(args[0], list)
            self.assertTrue(len(args[0]) > 0)

    def test_client_add_called_with_metadata(self):
        """Each client.add call must include metadata with section + stable_id."""
        user_md, marker = self._setup()
        mock_client = MagicMock()
        migrate(user_md, mock_client, marker_path=marker)
        for call in mock_client.add.call_args_list:
            args, kwargs = call
            meta = kwargs.get("metadata", {})
            self.assertIn("section", meta)
            self.assertIn("stable_id", meta)
            self.assertIn("category", meta)

    def test_stable_id_is_deterministic(self):
        """stable_id must be reproducible from section+line."""
        section = "IDENTITY"
        line = "Name: Arnav Deshmukh"
        expected = hashlib.sha1(f"{section}:{line}".encode()).hexdigest()[:16]
        # Migrate and find the call for this item
        d = tempfile.mkdtemp()
        user_md = Path(d) / "USER.md"
        user_md.write_text(f"[{section}]\n{line}\n", "utf-8")
        marker = Path(d) / ".m"
        mock_client = MagicMock()
        migrate(user_md, mock_client, marker_path=marker)
        call = mock_client.add.call_args_list[0]
        _, kwargs = call
        self.assertEqual(kwargs["metadata"]["stable_id"], expected)

    def test_client_add_failure_does_not_abort_migration(self):
        """If client.add raises on one item, migration continues for others."""
        text = "[IDENTITY]\nline1\nline2\nline3\n"
        d = tempfile.mkdtemp()
        user_md = Path(d) / "USER.md"
        user_md.write_text(text, "utf-8")
        marker = Path(d) / ".m"

        call_count = 0

        def failing_add(messages, metadata=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first call fails")

        mock_client = MagicMock()
        mock_client.add.side_effect = failing_add
        result = migrate(user_md, mock_client, marker_path=marker)
        # 2 of 3 calls succeed
        self.assertEqual(result["total"], 2)

    def test_dry_run_total_matches_parse_count(self):
        user_md, marker = self._setup()
        mock_client = MagicMock()
        pairs = parse_sections(_FIXTURE)
        result = migrate(user_md, mock_client, marker_path=marker, dry_run=True)
        self.assertEqual(result["total"], len(pairs))


if __name__ == "__main__":
    unittest.main()
