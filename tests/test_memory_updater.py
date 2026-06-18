"""Tests for jack_memory.updater.MemoryUpdater + the conversation memory-query path.

All tests are offline: extraction uses an injected `complete_fn`, and the
conversation memory-query test uses HERMES_LLM_MOCK. USER.md fixtures live in
temp files so nothing touches Arnav's real memory.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "hooks" / "jack_router"))

from jack_memory.updater import MemoryUpdater  # noqa: E402

USER_FIXTURE = """Jack's Memory — Arnav Deshmukh
Last updated: 2026-06-01

This file is Jack's living memory.

[IDENTITY]
Name: Arnav Deshmukh
Age: 20

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
Food: Non-veg, pizza

[GOALS]
Short term: Launch Vytal

[THINGS JACK HAS LEARNED]
(Jack adds entries here automatically after conversations)
2026-06-01: Seed line.
"""


def _temp_user_md(content: str = USER_FIXTURE) -> Path:
    d = tempfile.mkdtemp()
    p = Path(d) / "USER.md"
    p.write_text(content, encoding="utf-8")
    return p


class ExtractTest(unittest.TestCase):
    def test_extract_learns_new_preference(self):
        def fake_complete(system, user, **kw):
            return json.dumps(
                [{"section": "PREFERENCES", "fact": "Hates thin-crust pizza"}]
            )

        upd = MemoryUpdater(user_path=_temp_user_md(), complete_fn=fake_complete)
        learnings = upd.extract_learnings([], "i hate thin crust", "noted")
        self.assertEqual(
            learnings, [{"section": "PREFERENCES", "fact": "Hates thin-crust pizza"}]
        )

    def test_extract_ignores_vague_impressions(self):
        # The model, given a vague turn, returns [] — passed through as [].
        upd = MemoryUpdater(
            user_path=_temp_user_md(), complete_fn=lambda *a, **k: "[]"
        )
        self.assertEqual(upd.extract_learnings([], "kinda tired today", "rest up"), [])

    def test_extract_returns_empty_on_nothing_new(self):
        upd = MemoryUpdater(
            user_path=_temp_user_md(), complete_fn=lambda *a, **k: "[]"
        )
        self.assertEqual(upd.extract_learnings([], "hi", "hey"), [])

    def test_extract_returns_empty_on_malformed_json(self):
        upd = MemoryUpdater(
            user_path=_temp_user_md(),
            complete_fn=lambda *a, **k: "not json at all {",
        )
        self.assertEqual(upd.extract_learnings([], "hi", "hey"), [])

    def test_extract_drops_invalid_section(self):
        # A fact with a section outside the 8 canonical ones is dropped here.
        upd = MemoryUpdater(
            user_path=_temp_user_md(),
            complete_fn=lambda *a, **k: json.dumps(
                [{"section": "RANDOM", "fact": "x"}]
            ),
        )
        self.assertEqual(upd.extract_learnings([], "hi", "hey"), [])


class UpdateTest(unittest.TestCase):
    def test_update_appends_to_correct_section(self):
        path = _temp_user_md()
        upd = MemoryUpdater(user_path=path, complete_fn=lambda *a, **k: "[]")
        n = upd.update_user_md(
            [{"section": "PREFERENCES", "fact": "Hates thin-crust pizza"}]
        )
        self.assertEqual(n, 1)
        text = path.read_text(encoding="utf-8")
        # Find the PREFERENCES block and assert the fact is inside it.
        pref_idx = text.index("[PREFERENCES]")
        goals_idx = text.index("[GOALS]")
        pref_block = text[pref_idx:goals_idx]
        self.assertIn("- Hates thin-crust pizza", pref_block)
        # Other sections untouched.
        self.assertNotIn("Hates thin-crust pizza", text[:pref_idx])

    def test_update_does_not_overwrite_existing_facts(self):
        path = _temp_user_md()
        original_lines = [
            ln for ln in path.read_text(encoding="utf-8").split("\n")
        ]
        upd = MemoryUpdater(user_path=path, complete_fn=lambda *a, **k: "[]")
        upd.update_user_md([{"section": "GOALS", "fact": "Wants a Masters abroad"}])
        new_text = path.read_text(encoding="utf-8")
        # Every original non-"Last updated" line must still be present verbatim.
        for ln in original_lines:
            if ln.startswith("Last updated:") or ln == "":
                continue
            self.assertIn(ln, new_text, f"original line lost: {ln!r}")

    def test_update_things_learned_is_dated(self):
        path = _temp_user_md()
        upd = MemoryUpdater(user_path=path, complete_fn=lambda *a, **k: "[]")
        today = datetime.date(2026, 6, 19)
        upd.update_user_md(
            [{"section": "THINGS JACK HAS LEARNED", "fact": "Loves shawarma"}],
            today=today,
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn("2026-06-19: Loves shawarma", text)
        # The seed dated line is still there.
        self.assertIn("2026-06-01: Seed line.", text)

    def test_update_unknown_section_routes_to_things_learned(self):
        path = _temp_user_md()
        upd = MemoryUpdater(user_path=path, complete_fn=lambda *a, **k: "[]")
        today = datetime.date(2026, 6, 19)
        # Bypass extraction validation by calling update directly with a bad section.
        upd.update_user_md([{"section": "NOPE", "fact": "Routed fact"}], today=today)
        text = path.read_text(encoding="utf-8")
        things_idx = text.index("[THINGS JACK HAS LEARNED]")
        self.assertIn("2026-06-19: Routed fact", text[things_idx:])

    def test_update_bumps_last_updated(self):
        path = _temp_user_md()
        upd = MemoryUpdater(user_path=path, complete_fn=lambda *a, **k: "[]")
        today = datetime.date(2026, 6, 19)
        upd.update_user_md(
            [{"section": "PREFERENCES", "fact": "Likes KFC"}], today=today
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn("Last updated: 2026-06-19", text)


class RunAsyncTest(unittest.TestCase):
    def test_memory_log_written(self):
        path = _temp_user_md()
        log = Path(tempfile.mkdtemp()) / "memory.log"
        upd = MemoryUpdater(
            user_path=path,
            log_path=log,
            complete_fn=lambda *a, **k: json.dumps(
                [{"section": "PREFERENCES", "fact": "Likes burgers"}]
            ),
        )
        os.environ.pop("JACK_MEMORY_ENABLED", None)
        asyncio.run(upd.run_async([], "i love burgers", "noted"))
        self.assertTrue(log.exists())
        log_text = log.read_text(encoding="utf-8")
        self.assertIn("Likes burgers", log_text)
        # And the fact actually landed in USER.md.
        self.assertIn("- Likes burgers", path.read_text(encoding="utf-8"))

    def test_memory_log_nothing_new(self):
        path = _temp_user_md()
        log = Path(tempfile.mkdtemp()) / "memory.log"
        upd = MemoryUpdater(
            user_path=path, log_path=log, complete_fn=lambda *a, **k: "[]"
        )
        os.environ.pop("JACK_MEMORY_ENABLED", None)
        asyncio.run(upd.run_async([], "hi", "hey"))
        self.assertIn("nothing new", log.read_text(encoding="utf-8"))

    def test_memory_disabled_noop(self):
        path = _temp_user_md()
        log = Path(tempfile.mkdtemp()) / "memory.log"
        before = path.read_text(encoding="utf-8")
        upd = MemoryUpdater(
            user_path=path,
            log_path=log,
            complete_fn=lambda *a, **k: json.dumps(
                [{"section": "PREFERENCES", "fact": "Should not be written"}]
            ),
        )
        os.environ["JACK_MEMORY_ENABLED"] = "0"
        try:
            asyncio.run(upd.run_async([], "i love x", "ok"))
        finally:
            os.environ.pop("JACK_MEMORY_ENABLED", None)
        # No log, no USER.md change.
        self.assertFalse(log.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), before)


class MemoryQueryTest(unittest.TestCase):
    def test_memory_query_reads_fresh_user_md(self):
        # Point the conversation handler's USER.md at a temp file, then ask the
        # memory-query phrase. The mock LLM echoes the user prompt, so a non-empty
        # reply that reflects the temp file proves it was read fresh.
        import conversation  # noqa: E402
        from conversation import JackConversationHandler  # noqa: E402

        d = tempfile.mkdtemp()
        soul = Path(d) / "SOUL.md"
        user = Path(d) / "USER.md"
        soul.write_text("You are Jack, Chief of Staff for Arnav.", encoding="utf-8")
        user.write_text(
            "[IDENTITY]\nName: Arnav\n[PREFERENCES]\nFood: shawarma\n",
            encoding="utf-8",
        )
        os.environ["HERMES_LLM_MOCK"] = "1"
        os.environ["JACK_MEMORY_ENABLED"] = "0"  # don't fire background writes
        try:
            h = JackConversationHandler(soul_path=soul, user_path=user)
            reply = asyncio.run(h.respond("what do you remember about me", "u1"))
        finally:
            os.environ.pop("HERMES_LLM_MOCK", None)
            os.environ.pop("JACK_MEMORY_ENABLED", None)
        self.assertIsInstance(reply, str)
        self.assertTrue(reply)
        # The mock echoes the user prompt, which contained the fresh USER.md.
        self.assertIn("shawarma", reply)


if __name__ == "__main__":
    unittest.main()
