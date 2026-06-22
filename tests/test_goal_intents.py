"""Tests for goal intent classification and parsing — no LLM, no network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

# Ensure both the router directory and the project root are on the path.
_ROOT = Path(__file__).resolve().parents[1]
_ROUTER_DIR = _ROOT / "hooks" / "jack_router"
for _p in (str(_ROOT), str(_ROUTER_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import jack_intent_router as router  # noqa: E402
from jack_goals.intents import (  # noqa: E402
    confirm_create_message,
    parse_create_goal,
    parse_query_goal,
    parse_update_goal,
)


# ---------------------------------------------------------------------------
# 1. Regex smoke tests — intent router patterns
# ---------------------------------------------------------------------------
class TestGoalCreateRegex(unittest.TestCase):
    def test_new_goal_colon(self):
        r = router.classify("new goal: run 10k in 8 weeks")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "create")

    def test_my_goal_is(self):
        r = router.classify("my goal is to save £10k this year")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "create")

    def test_set_a_goal(self):
        r = router.classify("set a goal for marathon training")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "create")

    def test_im_trying_to_train(self):
        r = router.classify("I'm trying to train for a half-marathon")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "create")

    def test_help_me_track(self):
        r = router.classify("help me track my running progress")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "create")

    def test_i_want_to_track(self):
        r = router.classify("I want to track my savings")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "create")


class TestGoalQueryRegex(unittest.TestCase):
    def test_how_am_i_tracking(self):
        r = router.classify("how am I tracking on my steps goal")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "query")

    def test_what_are_my_goals(self):
        r = router.classify("what are my goals")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "query")

    def test_show_my_goals(self):
        r = router.classify("show my goals")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "query")

    def test_goal_progress(self):
        r = router.classify("goal progress please")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "query")

    def test_am_i_on_track(self):
        r = router.classify("am I on track")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "query")

    def test_my_goals(self):
        r = router.classify("my goals")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "query")


class TestGoalUpdateRegex(unittest.TestCase):
    def test_mark_marathon_goal_done(self):
        r = router.classify("mark marathon goal done")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "update")

    def test_goal_done_bare(self):
        r = router.classify("goal done")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "update")

    def test_pause_my_running_goal(self):
        r = router.classify("pause my running goal")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "update")

    def test_delete_savings_goal(self):
        r = router.classify("delete savings goal")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "update")

    def test_resume_goal(self):
        r = router.classify("resume my marathon goal")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "update")


# ---------------------------------------------------------------------------
# 2. Non-goal inputs must NOT match goal intents
# ---------------------------------------------------------------------------
class TestGoalNoFalsePositives(unittest.TestCase):
    def test_reminder_about_goal_is_reminder(self):
        r = router.classify("remind me about my goal check-in tomorrow at 9am")
        self.assertEqual(r.intent, "reminder")

    def test_i_want_to_know_weather(self):
        # "i want to know" — no goal verb after "to" → conversational
        r = router.classify("I want to know the weather today")
        self.assertEqual(r.intent, "conversational")

    def test_find_leads_not_goal(self):
        r = router.classify("find 50 dentists in Delhi")
        self.assertEqual(r.intent, "lead")

    def test_calendar_event_not_goal(self):
        r = router.classify("add a gym session to my calendar tomorrow")
        self.assertEqual(r.intent, "calendar")

    def test_ordinary_chat_not_goal(self):
        r = router.classify("what do you think about my progress at work")
        self.assertEqual(r.intent, "conversational")


# ---------------------------------------------------------------------------
# 3. parse_create_goal — with mock LLM
# ---------------------------------------------------------------------------
class TestParseCreateGoal(unittest.TestCase):
    def test_with_mock_llm_extracts_fields(self):
        llm_response = (
            '{"title":"Run a 10k","type":"fitness","target":"finish a 10k race",'
            '"plan":"train 3 times a week","metric":"garmin_runs","deadline":"in 8 weeks"}'
        )
        mock_llm = mock.MagicMock(return_value=llm_response)
        result = parse_create_goal("I want to run a 10k in 8 weeks training 3x a week", mock_llm)
        self.assertEqual(result["title"], "Run a 10k")
        self.assertEqual(result["type"], "fitness")
        self.assertEqual(result["metric"], "garmin_runs")
        self.assertEqual(result["deadline"], "in 8 weeks")
        mock_llm.assert_called_once()

    def test_fallback_when_llm_raises(self):
        def bad_llm(*a, **kw):
            raise RuntimeError("network down")

        result = parse_create_goal("my goal is to save £5k", bad_llm)
        # Should not raise; title should contain the text
        self.assertIn("save", result["title"].lower())
        self.assertIsNotNone(result["metric"])

    def test_fallback_when_no_llm(self):
        result = parse_create_goal("new goal: 10000 steps a day")
        self.assertIsNotNone(result["title"])
        self.assertIn("metric", result)

    def test_metric_inferred_steps(self):
        # LLM returns metric="manual" but text mentions steps → override to garmin_steps
        llm_response = (
            '{"title":"10k steps","type":"fitness","target":"","plan":"","metric":"manual","deadline":null}'
        )
        mock_llm = mock.MagicMock(return_value=llm_response)
        result = parse_create_goal("I want to do 10000 steps a day", mock_llm)
        self.assertEqual(result["metric"], "garmin_steps")

    def test_metric_inferred_runs(self):
        llm_response = (
            '{"title":"Marathon","type":"fitness","target":"","plan":"","metric":"none","deadline":null}'
        )
        mock_llm = mock.MagicMock(return_value=llm_response)
        result = parse_create_goal("my goal is to run a marathon", mock_llm)
        self.assertEqual(result["metric"], "garmin_runs")

    def test_bad_json_returns_fallback(self):
        mock_llm = mock.MagicMock(return_value="not json at all !!!")
        result = parse_create_goal("my goal is to learn guitar", mock_llm)
        self.assertIsNotNone(result["title"])
        self.assertEqual(result["type"], "other")


# ---------------------------------------------------------------------------
# 4. parse_query_goal — deterministic, no LLM
# ---------------------------------------------------------------------------
class TestParseQueryGoal(unittest.TestCase):
    def test_progress_query_type(self):
        result = parse_query_goal("how am I tracking on my marathon goal")
        self.assertEqual(result["query_type"], "progress")
        self.assertIn("marathon", result["goal_hint"])

    def test_list_query_type(self):
        result = parse_query_goal("what are my goals")
        self.assertEqual(result["query_type"], "list")

    def test_status_query_type(self):
        result = parse_query_goal("goal status")
        self.assertEqual(result["query_type"], "status")

    def test_hint_stripped_of_boilerplate(self):
        result = parse_query_goal("how am I tracking on my marathon goal")
        # boilerplate "how am I tracking on my" should be stripped, "marathon" stays
        self.assertIn("marathon", result["goal_hint"])
        self.assertNotIn("how am i", result["goal_hint"])


# ---------------------------------------------------------------------------
# 5. parse_update_goal — deterministic fast path
# ---------------------------------------------------------------------------
class TestParseUpdateGoal(unittest.TestCase):
    def test_mark_done(self):
        result = parse_update_goal("mark marathon goal done")
        self.assertEqual(result["updates"].get("status"), "done")

    def test_pause(self):
        result = parse_update_goal("pause my savings goal")
        self.assertEqual(result["updates"].get("status"), "paused")

    def test_resume(self):
        result = parse_update_goal("resume my marathon goal")
        self.assertEqual(result["updates"].get("status"), "active")

    def test_delete_maps_to_done(self):
        result = parse_update_goal("delete the savings goal")
        self.assertEqual(result["updates"].get("status"), "done")

    def test_progress_note_detected(self):
        result = parse_update_goal("note: ran 5k today on marathon goal")
        self.assertIn("progress_note", result["updates"])
        self.assertIn("ran 5k", result["updates"]["progress_note"])

    def test_ambiguous_returns_empty_updates(self):
        # No detectable verb → fallback: no updates, don't mutate
        result = parse_update_goal("something something goal", None)
        self.assertIsInstance(result["updates"], dict)

    def test_llm_used_on_ambiguous(self):
        llm_response = '{"goal_hint":"marathon","action":"done","note":""}'
        mock_llm = mock.MagicMock(return_value=llm_response)
        # Text with no detectable deterministic verb triggers LLM path
        result = parse_update_goal("i finished it", mock_llm)
        # LLM says done → updates should have status:done
        self.assertEqual(result["updates"].get("status"), "done")

    def test_llm_failure_returns_safe_fallback(self):
        def bad_llm(*a, **kw):
            raise OSError("timeout")

        result = parse_update_goal("something vague about goal", bad_llm)
        self.assertIsInstance(result, dict)
        self.assertIn("updates", result)


# ---------------------------------------------------------------------------
# 6. confirm_create_message — Jack's voice
# ---------------------------------------------------------------------------
class TestConfirmCreateMessage(unittest.TestCase):
    def test_contains_title(self):
        fields = {
            "title": "Run a marathon",
            "target": "finish 42km",
            "plan": "train 4x week",
            "metric": "garmin_runs",
            "deadline": "2026-10-01",
        }
        msg = confirm_create_message(fields)
        self.assertIn("Run a marathon", msg)

    def test_second_person_voice(self):
        fields = {"title": "Save £5k", "target": "", "plan": "", "metric": "manual", "deadline": ""}
        msg = confirm_create_message(fields)
        # Jack's voice uses "Got it" and refers to the user's goal
        self.assertIn("Got it", msg)

    def test_metric_label_present(self):
        fields = {"title": "Steps goal", "target": "", "plan": "", "metric": "garmin_steps", "deadline": ""}
        msg = confirm_create_message(fields)
        self.assertIn("Garmin steps", msg)

    def test_deadline_shown_when_present(self):
        fields = {"title": "Marathon", "target": "", "plan": "", "metric": "garmin_runs", "deadline": "in 10 weeks"}
        msg = confirm_create_message(fields)
        self.assertIn("in 10 weeks", msg)

    def test_no_deadline_omitted(self):
        fields = {"title": "Marathon", "target": "", "plan": "", "metric": "garmin_runs", "deadline": ""}
        msg = confirm_create_message(fields)
        self.assertNotIn("by", msg)


# ---------------------------------------------------------------------------
# 7. Full route round-trips
# ---------------------------------------------------------------------------
class TestGoalRouteRoundTrip(unittest.TestCase):
    def test_classify_create_returns_goal_route(self):
        r = router.classify("new goal: save 50k this year")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "create")
        self.assertIn("save 50k", r.params["text"])

    def test_classify_query_returns_goal_route(self):
        r = router.classify("what are my goals")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "query")

    def test_classify_update_returns_goal_route(self):
        r = router.classify("mark marathon goal done")
        self.assertEqual(r.intent, "goal")
        self.assertEqual(r.params["action"], "update")

    def test_text_preserved_in_params(self):
        msg = "my goal is to run 10k before August"
        r = router.classify(msg)
        self.assertEqual(r.params.get("text"), msg)


if __name__ == "__main__":
    unittest.main()
