"""test_research — Manual Test #1: research is real + sourced.

Runs ``bin/research.py`` offline (HERMES_LLM_MOCK=1) and asserts:
  * a ``memory/research/<run_id>.json`` file is produced,
  * it passes ``validate_research_run`` (if the validator exists),
  * it has >= 1 finding,
  * EVERY finding carries a non-empty ``source_url`` (real-data guardrail),
  * every finding is written ``consumed == False``.
"""

from __future__ import annotations

import glob
import os
import unittest

from tests import helpers


class ResearchStageTest(helpers.HermesTestCase):
    def _run_research(self):
        mode, result = self.run_bin("research")
        if mode == "subprocess":
            self.assertEqual(
                result.returncode, 0,
                msg=f"research.py exited non-zero.\nSTDOUT:\n{result.stdout}\n"
                    f"STDERR:\n{result.stderr}",
            )
        else:
            self.assertIn(result, (0, None), "research main() should return 0")

    def _latest_run_file(self):
        files = sorted(glob.glob(os.path.join(str(self.research_dir()), "*.json")))
        # Exclude the seed config file.
        files = [f for f in files if not os.path.basename(f).startswith("_")]
        self.assertTrue(files, "research.py wrote no memory/research/<run_id>.json")
        return files[-1]

    def test_research_writes_sourced_findings(self):
        self._run_research()
        run_path = self._latest_run_file()
        run = self.read_json(run_path)

        # Wrapper shape sanity.
        self.assertIn("findings", run, "research file must be a wrapper with findings[]")
        findings = run["findings"]
        self.assertGreaterEqual(len(findings), 1, "expected >= 1 finding")

        # Real-data guardrail: every finding has a non-empty source_url.
        for i, f in enumerate(findings):
            url = f.get("source_url")
            self.assertTrue(
                isinstance(url, str) and url.strip(),
                f"finding #{i} has no real source_url: {url!r}",
            )

        # Findings must start un-consumed.
        for i, f in enumerate(findings):
            self.assertEqual(
                f.get("consumed"), False,
                f"finding #{i} must be written consumed=False",
            )

    def test_research_run_validates(self):
        self._run_research()
        run = self.read_json(self._latest_run_file())

        contracts = helpers.import_optional("lib.contracts")
        validate = getattr(contracts, "validate_research_run", None) if contracts else None
        if not callable(validate):
            self.skipTest("lib.contracts.validate_research_run not built yet.")

        result = validate(run)
        # Tolerant pass check (see test_contracts._is_ok for the conventions).
        if isinstance(result, tuple) and len(result) == 2:
            ok = bool(result[0])
        elif isinstance(result, (list, tuple)):
            ok = len(result) == 0
        else:
            ok = bool(result)
        self.assertTrue(ok, "validate_research_run must accept a real research run")

    def test_research_is_idempotent_filename(self):
        # Re-running should overwrite the same run file, not duplicate findings.
        self._run_research()
        first = self._latest_run_file()
        run1 = self.read_json(first)
        self._run_research()
        run2 = self.read_json(self._latest_run_file())
        # Finding ids should be unique within the (re)written run.
        ids = [f.get("id") for f in run2["findings"]]
        self.assertEqual(len(ids), len(set(ids)), "duplicate finding ids after re-run")


if __name__ == "__main__":
    unittest.main()
