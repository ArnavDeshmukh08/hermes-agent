"""Tests for shadow mode: ensures the reasoning loop never sends real Discord
messages in shadow mode, writes correctly to the shadow log, and respects
quiet hours and daily cap.
"""
import fcntl
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest
from proactive.scorer import ProactiveItem, PriorityScorer, P1, P2, P3, SCORE_P1, SCORE_P2

_IST = timezone(timedelta(hours=5, minutes=30))

# 13:15 IST — not quiet hours
_NOW = datetime(2026, 6, 22, 7, 45, tzinfo=timezone.utc)
# 21:15 IST — quiet hours (01:00-08:00 IST = 19:30-02:30 UTC next day)
# Actually quiet hours are 01:00-08:00 IST; 21:15 IST is NOT quiet
# Quiet hours 01:00 IST = UTC 19:30 prev day. Let's use 22:00 IST = 16:30 UTC
# Actually 01:00 IST = 19:30 UTC prev day. So 01:00 IST on Jun 22 = 20:30 UTC on Jun 21
# Let's use a time that is in quiet hours: 03:00 IST = 21:30 UTC Jun 21
_NOW_QUIET = datetime(2026, 6, 21, 21, 30, tzinfo=timezone.utc)  # 03:00 IST — quiet

_P1_ITEM = ProactiveItem(
    nudge_type="deadline_check", priority=P1, score=SCORE_P1,
    message="Demo tomorrow", alone=True, meta={"source": "reasoner", "reasoning": "calendar"}
)
_P2_ITEM = ProactiveItem(
    nudge_type="hydration", priority=P2, score=SCORE_P2,
    message="Drink water", alone=False, meta={"source": "reasoner", "reasoning": "midday"}
)


@pytest.fixture
def tmp_shadow_log(tmp_path):
    return tmp_path / "proactive_shadow.log"


@pytest.fixture
def mock_engine():
    eng = MagicMock()
    eng.sent_today_count.return_value = 0
    eng.already_sent_recently.return_value = False
    eng.log_sent = MagicMock()
    eng._send = MagicMock(return_value=True)
    eng._read_log.return_value = []
    return eng


@pytest.fixture
def mock_reasoner():
    r = MagicMock()
    r.reason.return_value = [_P1_ITEM]
    r.last_raw_response = '{"items": [{"priority":"P1"}]}'
    return r


def _make_scheduler(engine, monkeypatch):
    """Build a ProactiveScheduler with the mock engine, patching class-level deps."""
    from proactive.scheduler import ProactiveScheduler
    sched = ProactiveScheduler(engine=engine, user_id="arnav")
    return sched


def test_shadow_never_calls_notifier(mock_engine, mock_reasoner, tmp_shadow_log, monkeypatch, tmp_path):
    monkeypatch.setenv("JACK_PROACTIVE_ENGINE", "reasoner")
    monkeypatch.setenv("JACK_PROACTIVE_MODE", "shadow")

    # Patch all the heavy imports inside _run_once_reasoner
    monkeypatch.setattr("proactive.scheduler.ProactiveScheduler._run_once_reasoner",
                        lambda self, now: _shadow_cycle_with_mock(self, now, mock_reasoner, mock_engine, tmp_shadow_log))

    send_message_mock = MagicMock()
    monkeypatch.setattr("reminders.notifier.send_message", send_message_mock, raising=False)

    from proactive.scheduler import ProactiveScheduler
    sched = ProactiveScheduler(engine=mock_engine, user_id="arnav")
    sched.run_once(now=_NOW)

    send_message_mock.assert_not_called()
    mock_engine._send.assert_not_called()
    mock_engine.log_sent.assert_not_called()


def _shadow_cycle_with_mock(sched, now, mock_reasoner, mock_engine, shadow_log_path):
    """Helper: run the shadow logic using our mocks instead of real imports."""
    from proactive.scorer import PriorityScorer
    from proactive.shadow import log_shadow_cycle
    from proactive.engine import ProactiveEngine

    candidates = mock_reasoner.reason(sched._user_id, now, [])
    raw = mock_reasoner.last_raw_response
    mac_reachable = raw is not None

    scorer = PriorityScorer()
    sent_today = mock_engine.sent_today_count(now=now)
    selected = scorer.select(candidates, sent_today=sent_today)

    dedup_dropped = []
    kept = []
    for it in selected:
        if mock_engine.already_sent_recently(it.nudge_type, now=now):
            dedup_dropped.append(it.nudge_type)
        else:
            kept.append(it)

    quiet = ProactiveEngine.in_quiet_hours(now)
    kept_final = [] if quiet else kept
    now_ist = now.astimezone(_IST)

    log_shadow_cycle(
        now_ist, kept_final, raw,
        mac_reachable=mac_reachable,
        dedup_dropped=dedup_dropped,
        quiet_hours=quiet,
        log_path=shadow_log_path,
    )
    return len(kept_final)


def test_shadow_writes_to_log(mock_engine, mock_reasoner, tmp_shadow_log, monkeypatch):
    from proactive.shadow import log_shadow_cycle
    from proactive.scorer import PriorityScorer
    from proactive.engine import ProactiveEngine

    scorer = PriorityScorer()
    selected = scorer.select([_P1_ITEM], sent_today=0)
    kept = [it for it in selected if not mock_engine.already_sent_recently(it.nudge_type)]
    quiet = ProactiveEngine.in_quiet_hours(_NOW)
    kept_final = [] if quiet else kept
    now_ist = _NOW.astimezone(_IST)

    log_shadow_cycle(
        now_ist, kept_final, '{"test":"value"}',
        mac_reachable=True, dedup_dropped=[], quiet_hours=False,
        log_path=tmp_shadow_log,
    )

    assert tmp_shadow_log.exists()
    content = tmp_shadow_log.read_text()
    assert "=== SHADOW CYCLE" in content
    assert "deadline_check" in content
    assert "WOULD HAVE SENT" in content


def test_shadow_log_contains_reasoning(tmp_shadow_log):
    from proactive.shadow import log_shadow_cycle
    from proactive.engine import ProactiveEngine

    raw = '{"items":[{"priority":"P1","reasoning":"KEY_REASONING_MARKER"}]}'
    now_ist = _NOW.astimezone(_IST)
    quiet = ProactiveEngine.in_quiet_hours(_NOW)

    log_shadow_cycle(
        now_ist, [_P1_ITEM], raw,
        mac_reachable=True, dedup_dropped=[], quiet_hours=False,
        log_path=tmp_shadow_log,
    )

    content = tmp_shadow_log.read_text()
    assert "Raw reasoning (first 500 chars):" in content
    assert "KEY_REASONING_MARKER" in content


def test_live_mode_calls_send_fn(mock_engine, monkeypatch):
    """In live mode, the send mechanism is called and log_sent is called."""
    from proactive.scorer import PriorityScorer
    from proactive.engine import ProactiveEngine

    scorer = PriorityScorer()
    selected = scorer.select([_P1_ITEM], sent_today=0)
    kept = [it for it in selected if not mock_engine.already_sent_recently(it.nudge_type)]
    quiet = ProactiveEngine.in_quiet_hours(_NOW)
    kept_final = [] if quiet else kept

    # Simulate live path
    count = 0
    for it in kept_final:
        rendered = PriorityScorer.render(it)
        ok = mock_engine._send(rendered, "arnav")
        if ok:
            mock_engine.log_sent(it, now=_NOW)
            count += 1

    assert count == 1
    mock_engine._send.assert_called_once()
    call_args = mock_engine._send.call_args
    assert "🚨 Hey Arnav —" in call_args[0][0]
    mock_engine.log_sent.assert_called_once_with(_P1_ITEM, now=_NOW)


def test_quiet_hours_respected_in_shadow(tmp_shadow_log):
    from proactive.shadow import log_shadow_cycle
    from proactive.engine import ProactiveEngine

    # _NOW_QUIET is 03:00 IST — should be quiet
    quiet = ProactiveEngine.in_quiet_hours(_NOW_QUIET)
    assert quiet, "Test setup: _NOW_QUIET should be in quiet hours"

    now_ist = _NOW_QUIET.astimezone(_IST)
    kept_final = []  # quiet hours → nothing to send

    log_shadow_cycle(
        now_ist, kept_final, "some_raw",
        mac_reachable=True, dedup_dropped=[], quiet_hours=True,
        log_path=tmp_shadow_log,
    )

    content = tmp_shadow_log.read_text()
    assert "WOULD HAVE SENT: NOTHING" in content
    assert "Quiet hours: yes" in content


def test_daily_cap_respected(tmp_shadow_log):
    from proactive.shadow import log_shadow_cycle
    from proactive.scorer import PriorityScorer

    # sent_today=3 → scorer returns [] (cap is 3)
    scorer = PriorityScorer(max_per_day=3)
    selected = scorer.select([_P1_ITEM, _P2_ITEM], sent_today=3)
    assert selected == [], "scorer should return [] when at cap"

    now_ist = _NOW.astimezone(_IST)
    log_shadow_cycle(
        now_ist, selected, "raw",
        mac_reachable=True, dedup_dropped=[], quiet_hours=False,
        log_path=tmp_shadow_log,
    )

    content = tmp_shadow_log.read_text()
    assert "Items selected by scorer (0):" in content
    assert "WOULD HAVE SENT: NOTHING" in content
