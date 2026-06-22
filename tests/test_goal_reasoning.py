"""Tests for goal-aware proactive reasoning in proactive/reasoner.py.

These tests verify that gather_context() correctly:
1. Inserts an "# Active Goals" section into the context string.
2. Falls back to "No active goals." when no goals exist.
3. Survives gracefully when jack_goals is not importable (ImportError).
4. Attaches Garmin step data to the right goal line.
5. Contains the correct GOALS_TRACKING framing in the prompt.
6. Never contains prohibited prescriptive phrases.
7. Continues to work when GarminClient raises an exception.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone, timedelta
from unittest import TestCase
from unittest.mock import MagicMock, patch

_IST = timezone(timedelta(hours=5, minutes=30))
_NOW = datetime(2026, 6, 22, 7, 45, tzinfo=timezone.utc)


def _make_fake_goal(
    *,
    title="Run 5km daily",
    goal_type="fitness",
    target="5 km/day",
    plan="Run every morning before 8am",
    metric="garmin_steps",
    deadline="2026-08-01",
    progress_notes=(),
    last_checked=None,
):
    """Build a MagicMock that looks like a jack_goals.models.Goal."""
    g = MagicMock()
    g.title = title
    g.type = goal_type
    g.target = target
    g.plan = plan
    g.metric = metric
    g.deadline = deadline
    g.progress_notes = list(progress_notes)
    g.last_checked = last_checked
    return g


def _call_gather_context(fake_store=None, fake_calendar=None, fake_queue=None):
    """Call gather_context with minimal mocked deps.

    - memory_client: returns empty searches (no memories).
    - store: provided or MagicMock with list_pending returning [].
    - calendar_client: provided or None.
    - queue: provided or MagicMock with len() == 0.
    """
    from proactive.reasoner import gather_context

    mc = MagicMock()
    mc.search.return_value = []

    store = fake_store or MagicMock()
    store.list_pending.return_value = []

    calendar = fake_calendar  # None is acceptable

    queue = fake_queue or MagicMock()
    queue.__len__ = MagicMock(return_value=0)

    return gather_context(mc, store, calendar, queue, "arnav", _NOW)


class TestGatherContextGoalsSection(TestCase):
    """gather_context() includes a goals section in the returned string."""

    def test_gather_context_includes_goals_section(self):
        """When list_active_goals() returns one goal, '# Active Goals' appears in context."""
        goal = _make_fake_goal(metric="manual")

        with patch("jack_goals.store.list_active_goals", return_value=[goal]):
            ctx = _call_gather_context()

        self.assertIn("# Active Goals", ctx)
        self.assertIn("Run 5km daily", ctx)

    def test_gather_context_no_goals(self):
        """When list_active_goals() returns [], 'No active goals.' appears."""
        with patch("jack_goals.store.list_active_goals", return_value=[]):
            ctx = _call_gather_context()

        self.assertIn("No active goals.", ctx)

    def test_gather_context_goals_section_header_always_present(self):
        """The '# Active Goals' header is in the context whether or not goals exist."""
        with patch("jack_goals.store.list_active_goals", return_value=[]):
            ctx = _call_gather_context()

        self.assertIn("# Active Goals", ctx)

    def test_gather_context_goal_fields_in_output(self):
        """Goal title, target, deadline, and plan appear in the context line."""
        goal = _make_fake_goal(
            title="Finish thesis",
            goal_type="project",
            target="Submit by Aug 1",
            plan="Write 500 words per day",
            metric="manual",
            deadline="2026-08-01",
        )
        with patch("jack_goals.store.list_active_goals", return_value=[goal]):
            ctx = _call_gather_context()

        self.assertIn("Finish thesis", ctx)
        self.assertIn("Submit by Aug 1", ctx)
        self.assertIn("2026-08-01", ctx)
        self.assertIn("Write 500 words per day", ctx)

    def test_gather_context_goal_with_progress_note(self):
        """When a goal has progress notes, the last note appears in the context."""
        goal = _make_fake_goal(
            title="Read daily",
            metric="manual",
            progress_notes=["[2026-06-20] Read 30 pages", "[2026-06-21] Read 20 pages"],
        )
        with patch("jack_goals.store.list_active_goals", return_value=[goal]):
            ctx = _call_gather_context()

        self.assertIn("Read 20 pages", ctx)

    def test_gather_context_goal_no_deadline(self):
        """Goals without a deadline still render without crashing."""
        goal = _make_fake_goal(metric="manual", deadline=None)
        with patch("jack_goals.store.list_active_goals", return_value=[goal]):
            ctx = _call_gather_context()

        self.assertIn("# Active Goals", ctx)
        self.assertIn("Run 5km daily", ctx)


class TestGatherContextGoalsImportError(TestCase):
    """gather_context() handles ImportError from jack_goals gracefully."""

    def test_gather_context_goals_import_error(self):
        """If jack_goals is not importable, gather_context() still returns a valid string."""
        # Temporarily remove jack_goals from sys.modules so the import inside
        # gather_context() triggers an ImportError via the lazy import path.
        saved_modules = {}
        keys_to_remove = [k for k in sys.modules if k.startswith("jack_goals")]
        for k in keys_to_remove:
            saved_modules[k] = sys.modules.pop(k)

        # Replace with a module that raises ImportError on import
        import builtins
        real_import = builtins.__import__

        def import_blocker(name, *args, **kwargs):
            if name.startswith("jack_goals"):
                raise ImportError(f"Simulated missing jack_goals: {name}")
            return real_import(name, *args, **kwargs)

        try:
            with patch("builtins.__import__", side_effect=import_blocker):
                ctx = _call_gather_context()
        finally:
            # Restore sys.modules
            for k, v in saved_modules.items():
                sys.modules[k] = v

        self.assertIsInstance(ctx, str)
        self.assertIn("# Current time", ctx)
        self.assertIn("# What I remember about Arnav", ctx)

    def test_gather_context_goals_exception_does_not_crash(self):
        """If list_active_goals() raises an unexpected exception, gather_context() still works."""
        with patch("jack_goals.store.list_active_goals", side_effect=RuntimeError("disk failure")):
            ctx = _call_gather_context()

        self.assertIsInstance(ctx, str)
        self.assertIn("# Current time", ctx)


class TestGatherContextGoalsGarmin(TestCase):
    """gather_context() attaches Garmin step/run data to the goal line when available."""

    def test_gather_context_goal_with_garmin_steps(self):
        """For a garmin_steps goal, today's step count appears in the context."""
        goal = _make_fake_goal(
            title="10k steps daily",
            metric="garmin_steps",
            target="10000 steps",
        )
        fake_stats = {"steps": 7432, "calories": 300}

        with patch("jack_goals.store.list_active_goals", return_value=[goal]):
            with patch("integrations.garmin.GarminClient.get_stats", return_value=fake_stats):
                ctx = _call_gather_context()

        self.assertIn("7432", ctx)
        self.assertIn("# Active Goals", ctx)

    def test_gather_context_goal_with_garmin_runs(self):
        """For a garmin_runs goal, today's distance appears in the context."""
        goal = _make_fake_goal(
            title="Run 5km",
            metric="garmin_runs",
            target="5 km",
        )
        fake_stats = {"steps": 5000, "calories": 400, "distance_km": 4.8}

        with patch("jack_goals.store.list_active_goals", return_value=[goal]):
            with patch("integrations.garmin.GarminClient.get_stats", return_value=fake_stats):
                ctx = _call_gather_context()

        self.assertIn("4.8", ctx)

    def test_gather_context_garmin_failure_graceful(self):
        """If GarminClient.get_stats() raises, the goal still appears (without metric data)."""
        goal = _make_fake_goal(
            title="Daily steps",
            metric="garmin_steps",
            target="10000 steps",
        )

        with patch("jack_goals.store.list_active_goals", return_value=[goal]):
            with patch("integrations.garmin.GarminClient.get_stats", side_effect=ConnectionError("unreachable")):
                ctx = _call_gather_context()

        # Goal still present
        self.assertIn("Daily steps", ctx)
        # No step count appended (no crash either)
        self.assertNotIn("today's steps:", ctx)

    def test_gather_context_garmin_returns_none(self):
        """If GarminClient.get_stats() returns None, goal renders without metric data."""
        goal = _make_fake_goal(metric="garmin_steps", target="8000 steps")

        with patch("jack_goals.store.list_active_goals", return_value=[goal]):
            with patch("integrations.garmin.GarminClient.get_stats", return_value=None):
                ctx = _call_gather_context()

        self.assertIn("Run 5km daily", ctx)
        self.assertNotIn("today's steps:", ctx)


class TestGoalsTrackingFramingInPrompt(TestCase):
    """The context string contains the GOALS_TRACKING domain and correct framing."""

    def _get_ctx(self):
        with patch("jack_goals.store.list_active_goals", return_value=[]):
            return _call_gather_context()

    def test_framing_rule_in_context_prompt(self):
        """The context string contains 'NEVER invent', 'his plan', and 'GOALS_TRACKING'."""
        ctx = self._get_ctx()
        self.assertIn("GOALS_TRACKING", ctx)
        self.assertIn("NEVER invent", ctx)
        self.assertIn("his plan", ctx)

    def test_framing_rule_forbids_prescription(self):
        """The GOALS_TRACKING instruction explicitly forbids prescriptions."""
        ctx = self._get_ctx()
        # The framing must mention forbidden categories
        self.assertIn("training schedules", ctx)
        self.assertIn("dietary advice", ctx)
        self.assertIn("medical prescriptions", ctx)

    def test_goals_tracking_domain_numbered_5(self):
        """GOALS_TRACKING is numbered as domain 5, DEFAULT is numbered 6."""
        ctx = self._get_ctx()
        self.assertIn("5. GOALS_TRACKING", ctx)
        self.assertIn("6. DEFAULT", ctx)

    def test_default_domain_is_6_not_5(self):
        """Domain 5 is GOALS_TRACKING, not DEFAULT."""
        ctx = self._get_ctx()
        # 5. DEFAULT must NOT appear; 6. DEFAULT must appear
        self.assertNotIn("5. DEFAULT", ctx)
        self.assertIn("6. DEFAULT", ctx)

    def test_reflect_his_plan_framing_present(self):
        """'Reflect his plan back to him' or similar framing is in the instructions."""
        ctx = self._get_ctx()
        self.assertIn("Reflect his plan back to him", ctx)

    def test_bias_to_silence_framing_present(self):
        """The bias-to-silence note appears in the GOALS_TRACKING domain."""
        ctx = self._get_ctx()
        self.assertIn("Bias to silence", ctx)

    def test_active_goals_section_note_about_never_prescribe(self):
        """The # Active Goals header comment warns never to invent prescriptions."""
        ctx = self._get_ctx()
        self.assertIn("Reflect his plan back — never invent prescriptions", ctx)
