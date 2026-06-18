"""test_cmo — Manual Test #2: CMO produces valid scored drafts.

Seeds a fixture research run, runs ``bin/cmo.py`` (mock LLM), and asserts:
  * a ``memory/content/<id>.json`` is produced with status "pending",
  * it has >= 2 variants,
  * all variant scores are in [0, 1],
  * it passes ``validate_draft`` (if the validator exists),
  * the consumed findings are now ``consumed == True`` in their research file.
"""

from __future__ import annotations

import glob
import json
import os
import unittest

from tests import helpers


class CmoStageTest(helpers.HermesTestCase):
    def setUp(self):
        super().setUp()
        # Seed a known-good research run so CMO has eligible (unconsumed) findings.
        self.research_run = helpers.make_research_run(n_findings=2)
        self.research_path = helpers.write_research_fixture(
            self.memory_root, self.research_run
        )

    def _run_cmo(self, argv=None):
        mode, result = self.run_bin("cmo", argv=argv)
        if mode == "subprocess":
            self.assertEqual(
                result.returncode, 0,
                msg=f"cmo.py exited non-zero.\nSTDOUT:\n{result.stdout}\n"
                    f"STDERR:\n{result.stderr}",
            )
        else:
            self.assertIn(result, (0, None), "cmo main() should return 0")

    def _latest_draft(self):
        files = sorted(glob.glob(os.path.join(str(self.content_dir()), "*.json")))
        self.assertTrue(files, "cmo.py wrote no memory/content/<id>.json")
        return files[-1]

    def test_cmo_produces_pending_scored_draft(self):
        self._run_cmo()
        draft_path = self._latest_draft()
        draft = self.read_json(draft_path)

        # content-id equals filename stem.
        self.assertEqual(
            draft.get("id"), os.path.basename(draft_path)[:-5],
            "content-id must equal the filename stem",
        )

        self.assertEqual(draft.get("status"), "pending", "draft status must be pending")

        variants = draft.get("variants") or []
        self.assertGreaterEqual(len(variants), 2, "MVP target is >= 2 variants")

        # All scores within [0, 1].
        for v in variants:
            score = v.get("score")
            self.assertIsInstance(score, (int, float), "variant score must be numeric")
            self.assertGreaterEqual(score, 0.0, "score must be >= 0")
            self.assertLessEqual(score, 1.0, "score must be <= 1")

        # Source findings recorded.
        self.assertTrue(
            draft.get("source_research_ids"),
            "draft must record the findings it consumed",
        )

    def test_cmo_draft_validates(self):
        self._run_cmo()
        draft = self.read_json(self._latest_draft())
        contracts = helpers.import_optional("lib.contracts")
        validate = getattr(contracts, "validate_draft", None) if contracts else None
        if not callable(validate):
            self.skipTest("lib.contracts.validate_draft not built yet.")
        result = validate(draft)
        if isinstance(result, tuple) and len(result) == 2:
            ok = bool(result[0])
        elif isinstance(result, (list, tuple)):
            ok = len(result) == 0
        else:
            ok = bool(result)
        self.assertTrue(ok, "validate_draft must accept a CMO-produced draft")

    def test_cmo_flips_consumed(self):
        self._run_cmo()
        draft = self.read_json(self._latest_draft())
        used_ids = set(draft.get("source_research_ids") or [])
        self.assertTrue(used_ids, "draft consumed at least one finding")

        # Re-read every research file and confirm the used findings are consumed.
        consumed_ids = set()
        for path in glob.glob(os.path.join(str(self.research_dir()), "*.json")):
            if os.path.basename(path).startswith("_"):
                continue
            run = self.read_json(path)
            for f in run.get("findings", []):
                if f.get("consumed") is True:
                    consumed_ids.add(f.get("id"))

        missing = used_ids - consumed_ids
        self.assertFalse(
            missing,
            f"these consumed findings were not flipped consumed=True: {missing}",
        )


if __name__ == "__main__":
    unittest.main()
