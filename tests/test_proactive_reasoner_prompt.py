"""Tests verifying the upgraded reasoning prompt structure."""
from __future__ import annotations
import pytest
from proactive.reasoner import _SYSTEM_PROMPT, gather_context
from datetime import datetime, timezone, timedelta


_IST = timezone(timedelta(hours=5, minutes=30))


def _make_context(calendar_text: str = "", memories: list[str] | None = None) -> str:
    """Build a context string via gather_context() with controllable inputs."""
    from unittest.mock import MagicMock

    memory_client = MagicMock()
    if memories:
        memory_client.search.return_value = [{"memory": m} for m in memories]
    else:
        memory_client.search.return_value = []

    calendar_mock = MagicMock()
    calendar_mock.events_summary_text.return_value = calendar_text

    store = MagicMock()
    store.list_pending.return_value = []

    now = datetime(2026, 6, 22, 14, 0, tzinfo=timezone.utc)  # 7:30pm IST
    return gather_context(
        memory_client, store, calendar_mock, None, "arnav", now, already_sent=[]
    )


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

    def test_calendar_two_hour_threshold(self):
        ctx = _make_context(calendar_text="Gym at 8pm")
        assert "2 HOURS" in ctx or "2 hours" in ctx
        assert "MUST NOT" in ctx

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
