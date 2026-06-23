"""Tests for goal deadline date-parsing correctness.

All tests inject a fixed today=date(2026, 6, 23) so behaviour is deterministic
regardless of when the suite runs.  The LLM is always stubbed — no network.

Cases verified:
  1. "2nd August" on 2026-06-23 → 2026-08-02 (month hasn't passed yet)
  2. LLM returns past year for a month/day in the future this year → this year
  3. LLM returns past year for a month/day already passed this year → next year
  4. Future date stated explicitly with year → preserved unchanged
  5. Natural-text deadline ("in 10 weeks") → preserved unchanged
  6. null deadline → stays None
  7. plan text captured when the user provides one
  8. plan empty when the user provides none
  9. Guard corrects date returned by LLM, prompt includes today's date
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "hooks" / "jack_router")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from jack_goals.intents import _resolve_deadline, parse_create_goal  # noqa: E402

_TODAY = date(2026, 6, 23)


def _mock_llm(response: str):
    """Return a mock LLM that always returns the given JSON string."""
    m = MagicMock(return_value=response)
    return m


# ---------------------------------------------------------------------------
# _resolve_deadline unit tests (pure, no LLM)
# ---------------------------------------------------------------------------

class TestResolveDeadline(unittest.TestCase):
    def test_future_date_unchanged(self):
        self.assertEqual(_resolve_deadline("2026-08-02", _TODAY), "2026-08-02")

    def test_far_future_year_unchanged(self):
        self.assertEqual(_resolve_deadline("2027-08-02", _TODAY), "2027-08-02")

    def test_past_date_month_not_yet_passed_this_year(self):
        # 2024-08-02 → August hasn't passed in 2026 → correct to 2026-08-02
        self.assertEqual(_resolve_deadline("2024-08-02", _TODAY), "2026-08-02")

    def test_past_date_month_already_passed_this_year(self):
        # 2024-03-15 → March 2026 already past → correct to 2027-03-15
        self.assertEqual(_resolve_deadline("2024-03-15", _TODAY), "2027-03-15")

    def test_past_date_same_year_day_passed(self):
        # 2026-03-01 is in the past (today is June 23)
        self.assertEqual(_resolve_deadline("2026-03-01", _TODAY), "2027-03-01")

    def test_none_returns_none(self):
        self.assertIsNone(_resolve_deadline(None, _TODAY))

    def test_empty_string_returns_empty(self):
        self.assertEqual(_resolve_deadline("", _TODAY), "")

    def test_natural_text_preserved(self):
        # "in 10 weeks" is not ISO — leave it alone
        self.assertEqual(_resolve_deadline("in 10 weeks", _TODAY), "in 10 weeks")

    def test_natural_text_with_month_name_preserved(self):
        self.assertEqual(_resolve_deadline("by end of August", _TODAY), "by end of August")

    def test_today_itself_is_not_past(self):
        # today == _TODAY should NOT be corrected
        self.assertEqual(_resolve_deadline("2026-06-23", _TODAY), "2026-06-23")


# ---------------------------------------------------------------------------
# parse_create_goal integration — date-aware prompt + guard
# ---------------------------------------------------------------------------

class TestParseDateIntoPrompt(unittest.TestCase):
    def test_today_appears_in_system_prompt(self):
        """parse_create_goal passes today's ISO date into the system prompt."""
        llm = _mock_llm('{"title":"Marathon","type":"fitness","target":"","plan":"","metric":"none","deadline":"2026-08-02"}')
        parse_create_goal("run a marathon on 2nd August", llm, today=_TODAY)
        call_args = llm.call_args
        system_prompt = call_args[0][0]  # first positional arg
        self.assertIn("2026-06-23", system_prompt)

    def test_partial_date_corrected_via_guard(self):
        """LLM returns 2024-08-02 → guard corrects to 2026-08-02."""
        llm = _mock_llm('{"title":"Run a marathon","type":"fitness","target":"finish a marathon","plan":"","metric":"none","deadline":"2024-08-02"}')
        result = parse_create_goal("run a marathon on 2nd August", llm, today=_TODAY)
        self.assertEqual(result["deadline"], "2026-08-02")

    def test_future_date_not_touched(self):
        """LLM returns 2027-08-02 → guard leaves it unchanged."""
        llm = _mock_llm('{"title":"Run a marathon","type":"fitness","target":"","plan":"","metric":"none","deadline":"2027-08-02"}')
        result = parse_create_goal("run a marathon on August 2 2027", llm, today=_TODAY)
        self.assertEqual(result["deadline"], "2027-08-02")

    def test_past_month_day_bumped_to_next_year(self):
        """March 15 (already passed in 2026) → guard uses 2027-03-15."""
        llm = _mock_llm('{"title":"Save 50k","type":"financial","target":"","plan":"","metric":"none","deadline":"2024-03-15"}')
        result = parse_create_goal("save 50k by march 15", llm, today=_TODAY)
        self.assertEqual(result["deadline"], "2027-03-15")

    def test_natural_text_deadline_preserved(self):
        """'in 10 weeks' is not an ISO date — must pass through unchanged."""
        llm = _mock_llm('{"title":"10k training","type":"fitness","target":"","plan":"","metric":"none","deadline":"in 10 weeks"}')
        result = parse_create_goal("I want to run 10k in 10 weeks", llm, today=_TODAY)
        self.assertEqual(result["deadline"], "in 10 weeks")

    def test_null_deadline_stays_none(self):
        llm = _mock_llm('{"title":"Learn guitar","type":"habit","target":"","plan":"","metric":"none","deadline":null}')
        result = parse_create_goal("I want to learn guitar", llm, today=_TODAY)
        self.assertIsNone(result["deadline"])


# ---------------------------------------------------------------------------
# parse_create_goal — plan capture
# ---------------------------------------------------------------------------

class TestPlanCapture(unittest.TestCase):
    def test_plan_captured_when_user_provides_one(self):
        """When the user describes a plan, it must appear in result['plan']."""
        llm = _mock_llm(
            '{"title":"Run a marathon","type":"fitness","target":"finish a full marathon",'
            '"plan":"train 3 times a week building up to 40km long runs","metric":"garmin_runs","deadline":"2026-08-02"}'
        )
        result = parse_create_goal(
            "I want to run a marathon on 2nd August — I'll train 3 times a week building up to 40km long runs",
            llm,
            today=_TODAY,
        )
        self.assertIn("train 3 times a week", result["plan"])

    def test_plan_empty_when_none_stated(self):
        """No plan in user's message → plan field should be empty string."""
        llm = _mock_llm(
            '{"title":"Run a marathon","type":"fitness","target":"finish a marathon",'
            '"plan":"","metric":"garmin_runs","deadline":"2026-08-02"}'
        )
        result = parse_create_goal("run a marathon on 2nd August", llm, today=_TODAY)
        self.assertEqual(result["plan"], "")

    def test_plan_not_invented_by_llm(self):
        """If LLM somehow returns a non-empty plan the user didn't state,
        we store it (we trust the LLM here) — but the prompt instructs it not to.
        This test just confirms the field is propagated if present."""
        llm = _mock_llm(
            '{"title":"Marathon","type":"fitness","target":"","plan":"some plan","metric":"none","deadline":null}'
        )
        result = parse_create_goal("run a marathon", llm, today=_TODAY)
        self.assertEqual(result["plan"], "some plan")


# ---------------------------------------------------------------------------
# Regression: existing tests still pass with the new signature
# ---------------------------------------------------------------------------

class TestBackwardCompatibility(unittest.TestCase):
    def test_no_today_arg_uses_real_today(self):
        """Calling without today= must not raise."""
        llm = _mock_llm('{"title":"Test","type":"other","target":"","plan":"","metric":"none","deadline":null}')
        result = parse_create_goal("some goal", llm)
        self.assertIsNotNone(result)

    def test_no_llm_no_today_returns_fallback(self):
        result = parse_create_goal("my goal is to do 10000 steps")
        self.assertIsNotNone(result["title"])

    def test_future_date_in_existing_test_style(self):
        # Mirrors the existing test_with_mock_llm_extracts_fields pattern
        llm_response = (
            '{"title":"Run a 10k","type":"fitness","target":"finish a 10k race",'
            '"plan":"train 3 times a week","metric":"garmin_runs","deadline":"in 8 weeks"}'
        )
        result = parse_create_goal("I want to run a 10k in 8 weeks", _mock_llm(llm_response), today=_TODAY)
        self.assertEqual(result["title"], "Run a 10k")
        self.assertEqual(result["deadline"], "in 8 weeks")  # natural text preserved


if __name__ == "__main__":
    unittest.main()
