"""Tests verifying the upgraded reasoning prompt structure."""
from __future__ import annotations
import pytest
from proactive.reasoner import _SYSTEM_PROMPT, gather_context
from datetime import datetime, timezone, timedelta


_IST = timezone(timedelta(hours=5, minutes=30))


def _make_context(
    events: list[dict] | None = None,
    memories: list[str] | None = None,
    now: datetime | None = None,
) -> str:
    """Build a context string via gather_context() with controllable inputs.

    events: list of CalendarClient.list_events() dicts with 'summary' and 'start.dateTime'.
    """
    from unittest.mock import MagicMock

    memory_client = MagicMock()
    memory_client.search.return_value = (
        [{"memory": m} for m in memories] if memories else []
    )

    calendar_mock = MagicMock()
    calendar_mock.list_events.return_value = events or []

    store = MagicMock()
    store.list_pending.return_value = []

    if now is None:
        now = datetime(2026, 6, 22, 8, 30, tzinfo=timezone.utc)  # 2pm IST
    return gather_context(
        memory_client, store, calendar_mock, None, "arnav", now, already_sent=[]
    )


def _event(summary: str, hour_offset: float, now: datetime | None = None) -> dict:
    """Helper: build a list_events() dict starting `hour_offset` hours from `now`."""
    if now is None:
        now = datetime(2026, 6, 22, 8, 30, tzinfo=timezone.utc)
    start_dt = now + timedelta(hours=hour_offset)
    return {
        "summary": summary,
        "start": {"dateTime": start_dt.isoformat()},
        "end": {"dateTime": (start_dt + timedelta(hours=1)).isoformat()},
    }


class TestSystemPrompt:
    def test_silence_bias_present(self):
        assert "when in doubt" in _SYSTEM_PROMPT.lower() or "doubt" in _SYSTEM_PROMPT.lower()

    def test_no_blind_calendar_grab(self):
        assert "most visible" in _SYSTEM_PROMPT.lower() or "calendar event" in _SYSTEM_PROMPT.lower()


class TestPromptStructure:
    def test_domain_evaluation_present(self):
        ctx = _make_context()
        assert "RELATIONSHIPS" in ctx
        assert "GOALS" in ctx
        assert "CALENDAR" in ctx
        assert "HEALTH" in ctx
        assert "DEFAULT" in ctx

    def test_calendar_two_hour_threshold_in_domain_instructions(self):
        ctx = _make_context()
        assert "2 HOURS" in ctx or "2 hours" in ctx
        assert "MUST NOT" in ctx

    def test_event_outside_2h_labelled_do_not_surface(self):
        ctx = _make_context(events=[_event("Gym session", hour_offset=5.0)])
        assert "do NOT surface" in ctx
        assert "Gym session" in ctx

    def test_event_within_2h_labelled_actionable(self):
        ctx = _make_context(events=[_event("Doctor appointment", hour_offset=1.5)])
        assert "actionable" in ctx.lower() or "WITHIN 2 HOURS" in ctx
        assert "Doctor appointment" in ctx

    def test_canonical_nudge_types_in_schema_hint(self):
        ctx = _make_context()
        assert "gym" in ctx
        assert "relationship_followup" in ctx
        assert "goal_nudge" in ctx
        assert "calendar_prep" in ctx
        assert "health_nudge" in ctx

    def test_silence_bias_in_context(self):
        ctx = _make_context()
        lower = ctx.lower()
        assert "silence" in lower or "when uncertain" in lower or "empty" in lower

    def test_json_format_preserved(self):
        ctx = _make_context()
        assert '"nudges"' in ctx
        assert '"priority"' in ctx
        assert '"message"' in ctx
        assert '"nudge_type"' in ctx
        assert '"reasoning"' in ctx

    def test_domain_reasoning_requirement(self):
        """Prompt must instruct LLM to name the domain in reasoning field."""
        ctx = _make_context()
        assert "domain" in ctx.lower() and "reasoning" in ctx.lower()
