"""test_contracts — Manual Test prerequisite: the frozen §1 contracts.

Covers ``lib.contracts``:
  * ``validate_finding`` / ``validate_draft`` / ``validate_decision`` accept a
    good blueprint-shape example and return errors for malformed ones
    (bad finding type, score > 1, missing source_url, bad action).
  * ``slugify`` / ``make_content_id`` / ``finding_id`` format checks.

These exercise the *pinned* names from the Stream E brief. The contracts module
is built by Stream A; if it is not yet importable the whole case skips with a
clear message rather than failing the suite.
"""

from __future__ import annotations

import unittest

from tests import helpers


def _load_contracts():
    contracts = helpers.import_optional("lib.contracts")
    if contracts is None:
        raise unittest.SkipTest("lib.contracts not built yet (Stream A).")
    return contracts


def _is_ok(result) -> bool:
    """Normalise a validator return into a pass/fail boolean.

    Validators may return either ``(ok, errors)`` tuples, a list of errors
    (empty == ok), a bool, or raise on invalid input. This helper makes the
    tests tolerant to whichever convention Stream A landed on.
    """
    if isinstance(result, tuple) and len(result) == 2:
        ok, _errors = result
        return bool(ok)
    if isinstance(result, (list, tuple)):
        return len(result) == 0
    if isinstance(result, dict):
        return not result.get("errors")
    return bool(result)


def _errors(result):
    """Extract the error collection from a validator result, if any."""
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    if isinstance(result, (list, tuple)):
        return list(result)
    if isinstance(result, dict):
        return result.get("errors")
    return None


def _validate(fn, obj):
    """Call a validator and report (ok, raised). Treats a raise as invalid."""
    try:
        result = fn(obj)
    except Exception:
        return False, True, None
    return _is_ok(result), False, _errors(result)


class FindingContractTest(helpers.HermesTestCase):
    def setUp(self):
        super().setUp()
        self.contracts = _load_contracts()
        self.validate_finding = getattr(self.contracts, "validate_finding", None)
        if not callable(self.validate_finding):
            self.skipTest("lib.contracts.validate_finding missing.")

    def _good(self):
        return helpers.make_finding(
            "20260616_clinic-no-shows", 1,
            topic="clinic no-shows",
            source_url="https://example.org/study",
            ftype="trend",
        )

    def test_accepts_good_finding(self):
        ok, raised, _ = _validate(self.validate_finding, self._good())
        self.assertTrue(ok and not raised, "valid finding must pass validation")

    def test_rejects_bad_type(self):
        bad = self._good()
        bad["type"] = "not-a-real-type"  # enum = idea|trend|competitor|hook
        ok, _raised, _ = _validate(self.validate_finding, bad)
        self.assertFalse(ok, "finding with out-of-enum type must be rejected")

    def test_rejects_missing_source_url(self):
        # Producer-enforced non-null source_url is the real-data guardrail.
        bad = self._good()
        bad.pop("source_url", None)
        ok_missing, _r, _ = _validate(self.validate_finding, bad)

        bad_null = self._good()
        bad_null["source_url"] = None
        ok_null, _r2, _ = _validate(self.validate_finding, bad_null)

        bad_empty = self._good()
        bad_empty["source_url"] = ""
        ok_empty, _r3, _ = _validate(self.validate_finding, bad_empty)

        self.assertFalse(
            ok_missing and ok_null and ok_empty,
            "finding without a real source_url must be rejected (real-data rule)",
        )


class DraftContractTest(helpers.HermesTestCase):
    def setUp(self):
        super().setUp()
        self.contracts = _load_contracts()
        self.validate_draft = getattr(self.contracts, "validate_draft", None)
        if not callable(self.validate_draft):
            self.skipTest("lib.contracts.validate_draft missing.")

    def test_accepts_good_draft(self):
        ok, raised, _ = _validate(self.validate_draft, helpers.make_pending_draft())
        self.assertTrue(ok and not raised, "valid draft must pass validation")

    def test_rejects_score_above_one(self):
        bad = helpers.make_pending_draft()
        bad["variants"][0]["score"] = 1.5  # scores are 0..1 floats
        ok, _raised, _ = _validate(self.validate_draft, bad)
        self.assertFalse(ok, "draft with score > 1 must be rejected")

    def test_rejects_score_below_zero(self):
        bad = helpers.make_pending_draft()
        bad["variants"][0]["score"] = -0.1
        ok, _raised, _ = _validate(self.validate_draft, bad)
        self.assertFalse(ok, "draft with negative score must be rejected")


class DecisionContractTest(helpers.HermesTestCase):
    def setUp(self):
        super().setUp()
        self.contracts = _load_contracts()
        self.validate_decision = getattr(self.contracts, "validate_decision", None)
        if not callable(self.validate_decision):
            self.skipTest("lib.contracts.validate_decision missing.")

    def _good(self):
        return {
            "ts": "2026-06-16T10:12:00+05:30",
            "content_id": "20260616-0905-noshow-cost",
            "variant_idx": 0,
            "action": "approve",
            "note": "",
            "decided_by": "discord:42",
        }

    def test_accepts_good_decision(self):
        ok, raised, _ = _validate(self.validate_decision, self._good())
        self.assertTrue(ok and not raised, "valid decision must pass validation")

    def test_rejects_bad_action(self):
        bad = self._good()
        bad["action"] = "publish"  # action enum = approve|reject|revise
        ok, _raised, _ = _validate(self.validate_decision, bad)
        self.assertFalse(ok, "decision with bad action must be rejected")


class IdBuilderTest(helpers.HermesTestCase):
    def setUp(self):
        super().setUp()
        self.contracts = _load_contracts()

    def test_slugify(self):
        slugify = getattr(self.contracts, "slugify", None)
        if not callable(slugify):
            self.skipTest("lib.contracts.slugify missing.")
        out = slugify("Clinic no-shows & DPDP, 2026!")
        # lowercase, hyphen-joined, no leading/trailing or doubled hyphens.
        self.assertEqual(out, out.lower())
        self.assertNotIn(" ", out)
        self.assertNotIn("--", out)
        self.assertFalse(out.startswith("-") or out.endswith("-"))
        self.assertRegex(out, r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
        # Example from the memory-layer spec.
        self.assertEqual(out, "clinic-no-shows-dpdp-2026")

    def test_make_content_id(self):
        make_content_id = getattr(self.contracts, "make_content_id", None)
        if not callable(make_content_id):
            self.skipTest("lib.contracts.make_content_id missing.")
        # Pinned signature: make_content_id(slug, when=None) -> the date/time
        # come from `when` (IST clock), the slug is slugified. We pass a fixed
        # IST datetime so the assertion is deterministic.
        ist = getattr(self.contracts, "IST", None)
        from datetime import datetime, timezone, timedelta
        when = datetime(2026, 6, 16, 14, 32, tzinfo=ist or timezone(timedelta(hours=5, minutes=30)))
        cid = make_content_id("no show hook", when=when)
        # content-id = <yyyymmdd>-<hhmm>-<shortslug>
        self.assertRegex(cid, r"^\d{8}-\d{4}-[a-z0-9]+(?:-[a-z0-9]+)*$")
        self.assertEqual(cid, "20260616-1432-no-show-hook")

        # Default (no `when`) still yields a well-formed id.
        cid_now = make_content_id("retention post")
        self.assertRegex(cid_now, r"^\d{8}-\d{4}-[a-z0-9]+(?:-[a-z0-9]+)*$")

    def test_finding_id(self):
        finding_id = getattr(self.contracts, "finding_id", None)
        if not callable(finding_id):
            self.skipTest("lib.contracts.finding_id missing.")
        # Blueprint §1: finding id = <run_id>#<n> (1-based, no sha1).
        fid = finding_id("20260616_clinic-no-shows", 1)
        self.assertEqual(fid, "20260616_clinic-no-shows#1")
        self.assertRegex(fid, r"^\d{8}_[a-z0-9-]+#\d+$")


if __name__ == "__main__":
    unittest.main()
