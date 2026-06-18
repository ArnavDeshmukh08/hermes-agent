"""Tests for the Jack living-memory (USER.md) schema.

Validates that the seeded memory file (secrets/USER.md.seed) contains all
required sections and that section headers are correctly delimited.
"""

import os
import re
import unittest

# Locate the seed relative to this test file so the test is path-independent.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
SEED_PATH = os.path.join(_REPO_ROOT, "secrets", "USER.md.seed")

REQUIRED_SECTIONS = [
    "IDENTITY",
    "RELATIONSHIPS",
    "WORK & PROJECTS",
    "CURRENT PRIORITIES",
    "DAILY ROUTINE — ICHALKARANJI (HOLIDAYS)",
    "PREFERENCES",
    "GOALS",
    "THINGS JACK HAS LEARNED",
]

# A section header is a line that is exactly an uppercase label in brackets.
_HEADER_RE = re.compile(r"^\[.+\]$")


class MemorySchemaTest(unittest.TestCase):
    def _read_seed(self) -> str:
        if not os.path.exists(SEED_PATH):
            self.skipTest(f"seed file not found: {SEED_PATH}")
        with open(SEED_PATH, "r", encoding="utf-8") as fh:
            return fh.read()

    def test_user_md_has_all_required_sections(self):
        content = self._read_seed()
        for section in REQUIRED_SECTIONS:
            self.assertIn(
                f"[{section}]",
                content,
                msg=f"missing required section: [{section}]",
            )

    def test_sections_are_correctly_delimited(self):
        content = self._read_seed()
        lines = content.splitlines()

        # Collect header line indexes.
        header_indexes = [
            i for i, line in enumerate(lines) if _HEADER_RE.match(line)
        ]
        self.assertTrue(header_indexes, "no [SECTION] headers found")

        # Every required section must appear as an exact bracketed header line.
        header_labels = {lines[i].strip()[1:-1] for i in header_indexes}
        for section in REQUIRED_SECTIONS:
            self.assertIn(
                section,
                header_labels,
                msg=f"section [{section}] is not a correctly delimited header",
            )

        # Each section must be non-empty (have at least one content line
        # before the next header or end of file).
        for pos, start in enumerate(header_indexes):
            end = (
                header_indexes[pos + 1]
                if pos + 1 < len(header_indexes)
                else len(lines)
            )
            body = [ln for ln in lines[start + 1 : end] if ln.strip()]
            self.assertTrue(
                body,
                msg=f"section {lines[start]} is empty",
            )


if __name__ == "__main__":
    unittest.main()
