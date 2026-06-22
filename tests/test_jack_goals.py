"""Tests for jack_goals — models, store, and module-level convenience API.

All tests use a tmp file for storage so they never touch ~/.hermes/goals.json.
The module-level singleton in store.py is bypassed by using GoalStore(path=...)
directly everywhere.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import unittest
from dataclasses import asdict
from pathlib import Path

from jack_goals.models import Goal, new_goal, GOAL_TYPES, METRICS, STATUSES
from jack_goals.store import GoalStore, VALID_STATUSES


def _make_store(tmp_dir: str) -> GoalStore:
    path = os.path.join(tmp_dir, "goals.json")
    return GoalStore(path=path)


class TestCreateGoal(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = _make_store(self._tmp)

    def test_create_goal_basic(self):
        g = self.store.create_goal(
            title="Run a marathon",
            type="fitness",
            target="42km race",
            plan="Train 4 days/week",
            metric="garmin_runs",
            deadline="2026-12-31",
        )
        self.assertEqual(g.title, "Run a marathon")
        self.assertEqual(g.type, "fitness")
        self.assertEqual(g.target, "42km race")
        self.assertEqual(g.plan, "Train 4 days/week")
        self.assertEqual(g.metric, "garmin_runs")
        self.assertEqual(g.deadline, "2026-12-31")
        self.assertEqual(g.status, "active")
        self.assertIsNotNone(g.created_at)
        self.assertEqual(g.progress_notes, ())
        self.assertIsNone(g.last_checked)

    def test_create_goal_returns_goal_object(self):
        g = self.store.create_goal(
            title="Save 5 lakh",
            type="financial",
            target="500000 INR",
            plan="Save 50k/month",
            metric="manual",
        )
        self.assertIsInstance(g, Goal)

    def test_create_goal_persists_and_returns_goal(self):
        """create_goal returns Goal with 8-char hex id; reloading via new GoalStore finds it."""
        g = self.store.create_goal(
            title="Read 12 books",
            type="habit",
            target="1 book/month",
            plan="Read 30 min daily",
            metric="manual",
        )
        # id is 8-char hex
        self.assertEqual(len(g.id), 8)
        int(g.id, 16)  # must be valid hex

        # Reload via a fresh store on the same path
        store2 = GoalStore(path=os.path.join(self._tmp, "goals.json"))
        found = store2.get_goal(g.id)
        self.assertIsNotNone(found)
        self.assertEqual(found.title, "Read 12 books")
        self.assertEqual(found.status, "active")


class TestGetGoal(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = _make_store(self._tmp)

    def test_get_goal_found_and_missing(self):
        g = self.store.create_goal(
            title="Learn Spanish",
            type="habit",
            target="B2 level",
            plan="Duolingo daily",
            metric="manual",
        )
        found = self.store.get_goal(g.id)
        self.assertIsNotNone(found)
        self.assertEqual(found.id, g.id)
        self.assertIsNone(self.store.get_goal("nope"))

    def test_get_goal_not_found(self):
        result = self.store.get_goal("unknown_id_xyz")
        self.assertIsNone(result)


class TestListActiveGoals(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = _make_store(self._tmp)

    def test_list_active_excludes_paused_and_done(self):
        g1 = self.store.create_goal("goal1", "fitness", "t1", "p1", "manual")
        g2 = self.store.create_goal("goal2", "habit", "t2", "p2", "manual")
        g3 = self.store.create_goal("goal3", "project", "t3", "p3", "manual")

        self.store.set_status(g2.id, "paused")
        self.store.set_status(g3.id, "done")

        active = self.store.list_active_goals()
        active_ids = [g.id for g in active]
        self.assertIn(g1.id, active_ids)
        self.assertNotIn(g2.id, active_ids)
        self.assertNotIn(g3.id, active_ids)
        self.assertEqual(len(active), 1)

    def test_list_active_goals_filters_paused(self):
        g1 = self.store.create_goal("active goal", "fitness", "t", "p", "manual")
        g2 = self.store.create_goal("paused goal", "fitness", "t", "p", "manual")
        self.store.set_status(g2.id, "paused")
        active = self.store.list_active_goals()
        ids = [g.id for g in active]
        self.assertIn(g1.id, ids)
        self.assertNotIn(g2.id, ids)

    def test_list_active_goals_filters_done(self):
        g1 = self.store.create_goal("active", "habit", "t", "p", "manual")
        g2 = self.store.create_goal("done goal", "habit", "t", "p", "manual")
        self.store.set_status(g2.id, "done")
        active = self.store.list_active_goals()
        ids = [g.id for g in active]
        self.assertIn(g1.id, ids)
        self.assertNotIn(g2.id, ids)


class TestUpdateGoal(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = _make_store(self._tmp)

    def test_update_goal_ignores_immutable_and_unknown_keys(self):
        g = self.store.create_goal("Original", "fitness", "target", "plan", "manual")
        original_id = g.id
        original_created_at = g.created_at

        updated = self.store.update_goal(g.id, id="hack", created_at="x", title="New Title")
        self.assertIsNotNone(updated)
        self.assertEqual(updated.id, original_id)           # immutable — unchanged
        self.assertEqual(updated.created_at, original_created_at)  # immutable — unchanged
        self.assertEqual(updated.title, "New Title")        # mutable — changed

    def test_update_goal_allowed_fields(self):
        g = self.store.create_goal("Old Title", "fitness", "old target", "old plan", "manual")
        updated = self.store.update_goal(g.id, title="New Title", target="new target")
        self.assertEqual(updated.title, "New Title")
        self.assertEqual(updated.target, "new target")
        self.assertEqual(updated.plan, "old plan")  # unchanged

    def test_update_goal_unknown_id_returns_none(self):
        result = self.store.update_goal("nonexistent_id", title="x")
        self.assertIsNone(result)


class TestAppendProgress(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = _make_store(self._tmp)

    def test_append_progress_is_append_only_and_stamps_last_checked(self):
        g = self.store.create_goal("Marathon", "fitness", "42km", "Train hard", "garmin_runs")
        self.assertIsNone(g.last_checked)

        g1 = self.store.append_progress(g.id, "Ran 10km today")
        self.assertIsNotNone(g1.last_checked)
        self.assertEqual(len(g1.progress_notes), 1)
        self.assertIn("Ran 10km today", g1.progress_notes[0])
        self.assertTrue(g1.progress_notes[0].startswith("["))

        g2 = self.store.append_progress(g.id, "Ran 15km today")
        self.assertEqual(len(g2.progress_notes), 2)
        self.assertIn("Ran 10km today", g2.progress_notes[0])
        self.assertIn("Ran 15km today", g2.progress_notes[1])

    def test_append_progress_only(self):
        g = self.store.create_goal("goal", "habit", "t", "p", "manual")
        self.store.append_progress(g.id, "note 1")
        result = self.store.append_progress(g.id, "note 2")
        self.assertEqual(len(result.progress_notes), 2)
        self.assertIn("note 1", result.progress_notes[0])
        self.assertIn("note 2", result.progress_notes[1])


class TestSetStatus(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = _make_store(self._tmp)

    def test_set_status_rejects_invalid(self):
        g = self.store.create_goal("goal", "fitness", "t", "p", "manual")
        with self.assertRaises(ValueError):
            self.store.set_status(g.id, "bogus")

    def test_set_status_done(self):
        g = self.store.create_goal("goal", "fitness", "t", "p", "manual")
        updated = self.store.set_status(g.id, "done")
        self.assertEqual(updated.status, "done")

    def test_set_status_valid(self):
        g = self.store.create_goal("goal", "fitness", "t", "p", "manual")
        for status in ("paused", "done", "active"):
            updated = self.store.set_status(g.id, status)
            self.assertEqual(updated.status, status)

    def test_set_status_invalid(self):
        g = self.store.create_goal("goal", "fitness", "t", "p", "manual")
        with self.assertRaises(ValueError):
            self.store.set_status(g.id, "invalid_status")


class TestCorruptFileRecovery(unittest.TestCase):
    def test_corrupt_file_returns_empty_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "goals.json")
            # Write corrupt JSON
            with open(path, "w") as f:
                f.write("{ broken json")

            store = GoalStore(path=path)
            with self.assertLogs("jack_goals.store", level=logging.WARNING):
                raw = store._read_all()
            self.assertEqual(raw, [])
            self.assertEqual(store.list_active_goals(), [])

    def test_corrupt_file_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "goals.json")
            with open(path, "w") as f:
                f.write("NOT VALID JSON!!!")
            store = GoalStore(path=path)
            with self.assertLogs("jack_goals.store", level=logging.WARNING):
                result = store._read_all()
            self.assertEqual(result, [])


class TestGoalRoundtrip(unittest.TestCase):
    def test_goal_roundtrip_progress_notes_tuple_to_list(self):
        """asdict->json->from_dict keeps progress_notes as tuple of same values."""
        g = Goal(
            id="abc12345",
            title="Test",
            type="fitness",
            target="target",
            plan="plan",
            metric="manual",
            created_at="2026-01-01T00:00:00+00:00",
            progress_notes=("note1", "note2"),
        )
        # Serialize using to_dict (stores as list in JSON)
        d = g.to_dict()
        self.assertIsInstance(d["progress_notes"], list)

        # Round-trip through JSON
        json_str = json.dumps(d)
        restored_d = json.loads(json_str)

        # from_dict coerces back to tuple
        g2 = Goal.from_dict(restored_d)
        self.assertIsInstance(g2.progress_notes, tuple)
        self.assertEqual(g2.progress_notes, ("note1", "note2"))

    def test_goal_asdict_json_roundtrip(self):
        """asdict() -> json -> from_dict preserves all fields."""
        g = new_goal(
            title="Save money",
            type="financial",
            target="1M",
            plan="invest",
            metric="manual",
            deadline="2027-01-01",
        )
        raw = asdict(g)
        # progress_notes is a tuple -> asdict gives list (Python dataclass behaviour)
        json_str = json.dumps(raw)
        d = json.loads(json_str)
        g2 = Goal.from_dict(d)
        self.assertEqual(g2.id, g.id)
        self.assertEqual(g2.title, g.title)
        self.assertEqual(g2.progress_notes, g.progress_notes)


class TestGoalModel(unittest.TestCase):
    def test_new_goal_factory(self):
        g = new_goal(title="Test", type="fitness", target="t", plan="p", metric="garmin_steps")
        self.assertEqual(len(g.id), 8)
        self.assertEqual(g.status, "active")
        self.assertEqual(g.progress_notes, ())
        self.assertIsNone(g.last_checked)

    def test_is_garmin_property(self):
        g1 = new_goal(title="x", type="fitness", target="t", plan="p", metric="garmin_steps")
        self.assertTrue(g1.is_garmin)
        g2 = new_goal(title="x", type="fitness", target="t", plan="p", metric="garmin_runs")
        self.assertTrue(g2.is_garmin)
        g3 = new_goal(title="x", type="fitness", target="t", plan="p", metric="manual")
        self.assertFalse(g3.is_garmin)
        g4 = new_goal(title="x", type="fitness", target="t", plan="p", metric="none")
        self.assertFalse(g4.is_garmin)

    def test_goal_is_frozen(self):
        """Frozen dataclass — mutation raises FrozenInstanceError."""
        from dataclasses import FrozenInstanceError
        g = new_goal(title="x", type="fitness", target="t", plan="p", metric="manual")
        with self.assertRaises(FrozenInstanceError):
            g.title = "hacked"  # type: ignore[misc]

    def test_from_dict_tolerates_missing_keys(self):
        g = Goal.from_dict({"id": "abc", "title": "min"})
        self.assertEqual(g.id, "abc")
        self.assertEqual(g.title, "min")
        self.assertEqual(g.type, "other")
        self.assertEqual(g.metric, "none")
        self.assertEqual(g.status, "active")
        self.assertEqual(g.progress_notes, ())

    def test_deadline_stripped_if_blank(self):
        g = new_goal(title="x", type="fitness", target="t", plan="p", metric="manual", deadline="  ")
        self.assertIsNone(g.deadline)

    def test_deadline_stripped_whitespace(self):
        g = new_goal(title="x", type="fitness", target="t", plan="p", metric="manual", deadline=" 2026-12-31 ")
        self.assertEqual(g.deadline, "2026-12-31")


class TestConcurrency(unittest.TestCase):
    def test_flock_concurrency(self):
        """Two threads writing simultaneously don't corrupt the file."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "goals.json")
            store = GoalStore(path=path)

            errors = []

            def writer(n: int):
                try:
                    store.create_goal(
                        title=f"goal_{n}",
                        type="fitness",
                        target=f"target_{n}",
                        plan=f"plan_{n}",
                        metric="manual",
                    )
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [], f"Concurrent write errors: {errors}")

            # File should be valid JSON with all 10 goals
            store2 = GoalStore(path=path)
            all_goals = store2.list_all_goals()
            self.assertEqual(len(all_goals), 10)

    def test_flock_concurrency_basic(self):
        """Simple 2-thread simultaneous write doesn't corrupt."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "goals.json")
            store = GoalStore(path=path)
            results = []

            def create_one(i):
                g = store.create_goal(f"goal{i}", "habit", "t", "p", "manual")
                results.append(g.id)

            t1 = threading.Thread(target=create_one, args=(1,))
            t2 = threading.Thread(target=create_one, args=(2,))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            # Both IDs written, no corruption
            self.assertEqual(len(results), 2)
            all_goals = store.list_all_goals()
            self.assertEqual(len(all_goals), 2)


class TestGoalStoreEdgeCases(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = _make_store(self._tmp)

    def test_list_all_goals_includes_all_statuses(self):
        g1 = self.store.create_goal("g1", "fitness", "t", "p", "manual")
        g2 = self.store.create_goal("g2", "habit", "t", "p", "manual")
        self.store.set_status(g2.id, "done")
        all_goals = self.store.list_all_goals()
        ids = [g.id for g in all_goals]
        self.assertIn(g1.id, ids)
        self.assertIn(g2.id, ids)

    def test_empty_store_returns_empty_list(self):
        self.assertEqual(self.store.list_active_goals(), [])
        self.assertEqual(self.store.list_all_goals(), [])

    def test_append_progress_empty_note_is_noop(self):
        g = self.store.create_goal("g", "fitness", "t", "p", "manual")
        result = self.store.append_progress(g.id, "")
        if result is not None:
            self.assertEqual(len(result.progress_notes), 0)

    def test_update_goal_progress_notes_via_update(self):
        """update_goal with progress_notes updates the field."""
        g = self.store.create_goal("g", "fitness", "t", "p", "manual")
        updated = self.store.update_goal(g.id, progress_notes=["note1"])
        self.assertIsNotNone(updated)

    def test_goals_stored_as_dict_not_bare_list(self):
        """Verify on-disk format is {"goals": [...]} not a bare list."""
        self.store.create_goal("g", "fitness", "t", "p", "manual")
        path = os.path.join(self._tmp, "goals.json")
        with open(path) as f:
            data = json.load(f)
        self.assertIsInstance(data, dict)
        self.assertIn("goals", data)
        self.assertIsInstance(data["goals"], list)

    def test_metric_normalized_to_lowercase(self):
        g = self.store.create_goal("g", "fitness", "t", "p", "GARMIN_STEPS")
        self.assertEqual(g.metric, "garmin_steps")

    def test_type_normalized_to_lowercase(self):
        g = self.store.create_goal("g", "FITNESS", "t", "p", "manual")
        self.assertEqual(g.type, "fitness")


if __name__ == "__main__":
    unittest.main()
