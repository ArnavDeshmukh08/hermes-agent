"""Offline, deterministic tests for the event-driven lead worker system.

All tests run with HERMES_LLM_MOCK=1 and an isolated task/out root, so they need
no network, no LLM, and no Google creds. Mock leads are parsed from synthetic
fixture pages (grounded, never fabricated) and carry mock:// provenance.
"""

import asyncio
import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker import leadgen  # noqa: E402
from worker.events import TASK_COMPLETE, EventBus  # noqa: E402
from worker.prime import HermesPrime  # noqa: E402
from worker.queue import Task, TaskQueue, new_task_id  # noqa: E402
from worker.specs import SpecError, load_spec  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class WorkerSystemTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = {k: os.environ.get(k) for k in
                      ("HERMES_LLM_MOCK", "HERMES_TASKS_ROOT", "HERMES_OUT_DIR", "HERMES_SIM_LATENCY")}
        os.environ["HERMES_LLM_MOCK"] = "1"
        os.environ["HERMES_TASKS_ROOT"] = str(Path(self._tmp.name) / "tasks")
        os.environ["HERMES_OUT_DIR"] = str(Path(self._tmp.name) / "out")
        os.environ["HERMES_SIM_LATENCY"] = "0.02"
        leadgen.SIM_LATENCY = 0.02

    def tearDown(self):
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _spec(self, **over):
        base = {"target": "physiotherapy clinics", "location": "India",
                "columns": ["name", "clinic", "phone", "email", "website"], "count": 6}
        base.update(over)
        return load_spec(base)

    # -- spec ---------------------------------------------------------------
    def test_spec_accepts_columns_and_fields_alias(self):
        self.assertEqual(load_spec({"target": "x", "columns": ["a", "b"]}).columns, ("a", "b"))
        self.assertEqual(load_spec({"target": "x", "fields": ["c"]}).columns, ("c",))

    def test_spec_refuses_empty_target(self):
        with self.assertRaises(SpecError):
            load_spec({"target": "   ", "columns": ["a"]})

    def test_spec_defaults_and_count_clamp(self):
        s = load_spec({"target": "x"})
        self.assertEqual(s.columns[0], "name")
        self.assertEqual(load_spec({"target": "x", "count": 9999}).count, 100)

    # -- queue --------------------------------------------------------------
    def test_queue_atomic_transitions(self):
        q = TaskQueue()
        t = q.enqueue(Task(id=new_task_id("x"), kind="x", payload={"n": 1}))
        self.assertEqual(q.counts()["pending"], 1)
        claimed = q.claim()
        self.assertEqual(claimed.id, t.id)
        self.assertEqual(q.counts(), {"pending": 0, "running": 1, "completed": 0, "failed": 0})
        q.complete(claimed, {"ok": True})
        self.assertEqual(q.counts(), {"pending": 0, "running": 0, "completed": 1, "failed": 0})

    def test_queue_claim_is_exclusive(self):
        q = TaskQueue()
        for i in range(10):
            q.enqueue(Task(id=new_task_id(f"t{i}"), kind="x", payload={}))
        claimed = [q.claim() for _ in range(15)]
        got = [c for c in claimed if c]
        self.assertEqual(len(got), 10)  # exactly the 10 enqueued, never more
        self.assertEqual(len({c.id for c in got}), 10)  # no id claimed twice

    def test_queue_fail_routes_to_failed(self):
        q = TaskQueue()
        t = q.claim() or q.enqueue(Task(id=new_task_id("x"), kind="x", payload={}))
        t = q.claim()
        q.fail(t, "boom")
        self.assertEqual(q.counts()["failed"], 1)

    # -- workers / leadgen --------------------------------------------------
    def test_discovery_attaches_provenance(self):
        from worker.workers import WORKERS
        spec = self._spec()
        res = run(WORKERS["discover"].run({"source": "practo"}, spec))
        self.assertTrue(res["leads"])
        for lead in res["leads"]:
            self.assertTrue(lead["source_url"])
            self.assertTrue(lead["fetched_at"])
            self.assertTrue(lead.get("name") or lead.get("clinic"))

    def test_validation_rejects_missing_provenance(self):
        from worker.workers import WORKERS
        spec = self._spec()
        good = {"name": "Dr X", "clinic": "C", "source_url": "mock://p/1", "fetched_at": "t"}
        bad = {"name": "Dr Y", "clinic": "C"}
        self.assertTrue(run(WORKERS["validate"].run(good, spec))["valid"])
        self.assertFalse(run(WORKERS["validate"].run(bad, spec))["valid"])

    def test_dedupe_key_priority(self):
        a = {"name": "X", "phone": "+91-1", "email": "a@b.c"}
        b = {"name": "Y", "phone": "+91-1"}  # same phone -> duplicate
        self.assertEqual(len(leadgen.dedupe([a, b])), 1)

    # -- dynamic columns + provenance in the sheet --------------------------
    def test_dynamic_columns_no_code_change(self):
        # a NON-default column set incl. a social column
        spec = self._spec(columns=["name", "clinic", "phone", "instagram"], count=5)
        res = run(HermesPrime(notify=False).run_parallel(spec))
        csv_path = Path(res["sheet"]["path"])
        self.assertTrue(csv_path.exists())
        with open(csv_path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        header = rows[0]
        self.assertEqual(header, ["name", "clinic", "phone", "instagram", "source_url", "fetched_at"])
        self.assertGreater(len(rows) - 1, 0)
        for r in rows[1:]:
            row = dict(zip(header, r))
            self.assertTrue(row["source_url"])   # provenance present on every row
            self.assertTrue(row["fetched_at"])
            # social worker filled it; the @ is formula-guarded ('@...) but the handle is intact
            self.assertIn("physiotherapy", row["instagram"])

    def test_outreach_drafts_pitch_and_never_sends(self):
        spec = self._spec(outreach=True, count=4)
        res = run(HermesPrime(notify=False).run_parallel(spec))
        self.assertGreater(res["counts"]["pitches"], 0)
        with open(res["sheet"]["path"], newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        header = rows[0]
        self.assertIn("pitch", header)
        self.assertIn("status", header)
        for r in rows[1:]:
            row = dict(zip(header, r))
            self.assertEqual(row["status"], "PENDING REVIEW")  # draft-only gate

    # -- event bus ----------------------------------------------------------
    def test_task_complete_event_emitted(self):
        seen = []
        bus = EventBus()
        bus.subscribe(TASK_COMPLETE, lambda p: seen.append(p))
        prime = HermesPrime(bus=bus, notify=False)
        run(prime.run_parallel(self._spec(count=4)))
        self.assertEqual(len(seen), 1)
        self.assertIn("counts", seen[0])

    # -- queue states populated after a run ---------------------------------
    def test_run_populates_completed_queue(self):
        prime = HermesPrime(notify=False)
        run(prime.run_parallel(self._spec(count=5)))
        counts = prime.queue.counts()
        self.assertGreater(counts["completed"], 0)
        self.assertEqual(counts["pending"], 0)
        self.assertEqual(counts["running"], 0)

    # -- security regressions (from the ECC review) -------------------------
    def test_csv_formula_injection_is_neutralized(self):
        from worker.sheets import lead_to_row
        lead = {"name": "=HYPERLINK(\"http://evil\")", "phone": "+91-99", "source_url": "mock://x", "fetched_at": "t"}
        row = lead_to_row(lead, ["name", "phone"])
        self.assertTrue(row[0].startswith("'="))   # formula neutralized
        self.assertTrue(row[1].startswith("'+"))   # +91 phone also guarded (still text)

    def test_sheet_tab_path_traversal_is_slugged(self):
        spec = load_spec({"target": "x", "sheet_tab": "../../secrets/creds", "columns": ["name"]})
        self.assertNotIn("/", spec.tab)
        self.assertNotIn("..", spec.tab)

    def test_is_safe_url_blocks_private_and_bad_schemes(self):
        self.assertFalse(leadgen.is_safe_url("http://169.254.169.254/latest/meta-data/"))
        self.assertFalse(leadgen.is_safe_url("http://localhost:11434/api"))
        self.assertFalse(leadgen.is_safe_url("file:///etc/passwd"))
        self.assertFalse(leadgen.is_safe_url("https://www.facebook.com/x"))
        self.assertTrue(leadgen.is_safe_url("https://sunrisephysio.in/contact"))

    # -- parallel beats sequential ------------------------------------------
    def test_parallel_faster_than_sequential(self):
        prime = HermesPrime(notify=False)
        cmp = run(prime.compare(self._spec(count=6)))
        self.assertEqual(cmp["parallel_written"], cmp["sequential_written"])
        self.assertGreater(cmp["sequential_s"], cmp["parallel_s"])  # concurrency wins


if __name__ == "__main__":
    unittest.main()
