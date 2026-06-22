import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pytest
from proactive.scorer import P1, P2, P3, SCORE_P1, SCORE_P2, SCORE_P3, ProactiveItem
from proactive.reasoner import ProactiveReasoner, gather_context

_IST = timezone(timedelta(hours=5, minutes=30))
_NOW = datetime(2026, 6, 22, 7, 45, tzinfo=timezone.utc)  # 13:15 IST — not quiet hours

_CONTEXT_STR = (
    "# Current time\n"
    "Sunday 2026-06-22 12:00 IST\n\n"
    "# What I remember about Arnav\n"
    "- Test memory\n\n"
    "# Calendar today\n"
    "No calendar events.\n\n"
    "# Pending reminders\n"
    "No pending reminders.\n\n"
    "# Already surfaced today\n"
    "Nothing surfaced yet today.\n\n"
    "# Memory queue\n"
    "0 memories still pending write."
)


@pytest.fixture
def fake_memory_client():
    mc = MagicMock()
    mc.search.return_value = [{"memory": "Arnav is building Vytal"}, {"memory": "Arnav likes chai"}]
    return mc


@pytest.fixture
def fake_store():
    st = MagicMock()
    st.list_pending.return_value = [{"message": "Call doctor", "fire_at": "2026-06-22T10:00:00Z"}]
    return st


@pytest.fixture
def fake_calendar():
    cal = MagicMock()
    cal.events_summary_text.return_value = "10:00 Standup"
    return cal


@pytest.fixture
def fake_queue():
    q = MagicMock()
    q.__len__ = MagicMock(return_value=2)
    return q


@pytest.fixture
def reasoner():
    return ProactiveReasoner()


def test_reason_returns_parsed_items(reasoner):
    payload = json.dumps([{
        "priority": "P2", "message": "Drink water", "nudge_type": "hydration",
        "alone": False, "reasoning": "midday"
    }])
    with patch.object(reasoner, "_mac_ollama_complete", return_value=payload):
        items = reasoner.reason(_CONTEXT_STR)
    assert len(items) == 1
    it = items[0]
    assert it.priority == P2
    assert it.score == SCORE_P2
    assert it.message == "Drink water"
    assert it.nudge_type == "hydration"
    assert it.alone is False
    assert it.meta["source"] == "reasoner"
    assert it.meta["reasoning"] == "midday"


def test_reason_returns_empty_on_no_items(reasoner):
    with patch.object(reasoner, "_mac_ollama_complete", return_value="[]"):
        items = reasoner.reason(_CONTEXT_STR)
    assert items == []


def test_reason_returns_empty_on_malformed_json(reasoner, caplog):
    with caplog.at_level(logging.ERROR, logger="proactive.reasoner"):
        with patch.object(reasoner, "_mac_ollama_complete", return_value="not json at all"):
            items = reasoner.reason(_CONTEXT_STR)
    assert items == []
    assert any("parse" in r.message.lower() or "json" in r.message.lower() for r in caplog.records)


def test_reason_returns_empty_on_mac_down(reasoner, caplog):
    with caplog.at_level(logging.INFO, logger="proactive.reasoner"):
        with patch.object(reasoner, "_mac_ollama_complete", return_value=None):
            items = reasoner.reason(_CONTEXT_STR)
    assert items == []
    assert any("Mac unreachable" in r.message for r in caplog.records)


def test_reason_never_raises():
    # ProactiveReasoner takes no data deps; Mac also returns []
    r = ProactiveReasoner()
    with patch.object(r, "_mac_ollama_complete", return_value="[]"):
        result = r.reason(_CONTEXT_STR)
    assert result == []


def test_gather_context_handles_memory_client_failure(fake_store, fake_calendar):
    bad_mc = MagicMock()
    bad_mc.search.side_effect = RuntimeError("qdrant down")
    ctx = gather_context(bad_mc, fake_store, fake_calendar, None, "arnav", _NOW)
    assert isinstance(ctx, str)
    # Should still return a valid string — no memories section populated, but string is formed
    assert "# Current time" in ctx
    assert "# What I remember about Arnav" in ctx


def test_items_respect_priority_schema(reasoner):
    payload = json.dumps([
        {"priority": "P9", "message": "bad", "nudge_type": "x", "alone": False, "reasoning": "r"},
        {"priority": "P1", "message": "good", "nudge_type": "y", "alone": True, "reasoning": "r"},
    ])
    with patch.object(reasoner, "_mac_ollama_complete", return_value=payload):
        items = reasoner.reason(_CONTEXT_STR)
    assert len(items) == 1
    assert items[0].priority == P1
    assert items[0].nudge_type == "y"


def test_proactive_item_conversion(reasoner):
    item = reasoner._to_proactive_item({
        "priority": "P1", "message": " urgent task ", "nudge_type": " deadline_check ",
        "alone": True, "reasoning": "time sensitive"
    })
    assert item is not None
    assert item.priority == P1
    assert item.score == SCORE_P1
    assert item.message == "urgent task"
    assert item.nudge_type == "deadline_check"
    assert item.alone is True
    assert item.meta == {"source": "reasoner", "reasoning": "time sensitive"}

    # Missing message → None
    assert reasoner._to_proactive_item({"priority": "P2", "nudge_type": "x", "reasoning": "r"}) is None
    # Non-str nudge_type → None
    assert reasoner._to_proactive_item({"priority": "P2", "message": "hi", "nudge_type": 99, "reasoning": "r"}) is None
    # Bad priority → None
    assert reasoner._to_proactive_item({"priority": "P9", "message": "hi", "nudge_type": "x", "reasoning": "r"}) is None


def test_mac_ollama_returns_none_on_exception(reasoner, caplog):
    import urllib.error
    with caplog.at_level(logging.WARNING, logger="proactive.reasoner"):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
            result = reasoner._mac_ollama_complete("sys", "user")
    assert result is None
    assert any("URLError" in r.message for r in caplog.records)


def test_parse_strips_markdown_fences(reasoner):
    raw = '```json\n[{"priority":"P3","message":"m","nudge_type":"n","alone":false,"reasoning":"r"}]\n```'
    with patch.object(reasoner, "_mac_ollama_complete", return_value=raw):
        items = reasoner.reason(_CONTEXT_STR)
    assert len(items) == 1
    assert items[0].priority == P3


def test_last_raw_response_set_after_reason(reasoner):
    payload = "[]"
    with patch.object(reasoner, "_mac_ollama_complete", return_value=payload):
        reasoner.reason(_CONTEXT_STR)
    assert reasoner.last_raw_response == payload


def test_last_raw_response_none_when_mac_down(reasoner):
    with patch.object(reasoner, "_mac_ollama_complete", return_value=None):
        reasoner.reason(_CONTEXT_STR)
    assert reasoner.last_raw_response is None


def test_reasoner_never_calls_qdrant_directly():
    """ProactiveReasoner takes no data deps — Qdrant access is impossible from inside reason()."""
    import inspect
    import proactive.reasoner as mod

    # ProactiveReasoner.__init__ must accept no positional args beyond self
    sig = inspect.signature(mod.ProactiveReasoner.__init__)
    params = [p for p in sig.parameters if p != "self"]
    assert params == [], f"ProactiveReasoner.__init__ must take no deps, got: {params}"

    # reason() must take a string, not a user_id
    sig_r = inspect.signature(mod.ProactiveReasoner.reason)
    param_names = [p for p in sig_r.parameters if p != "self"]
    assert param_names == ["context_str"], f"reason() must take only context_str, got: {param_names}"
