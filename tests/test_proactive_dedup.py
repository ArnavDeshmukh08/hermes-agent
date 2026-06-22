"""Tests for proactive dedup fixes: shadow mode logging + nudge_type normalization."""
from __future__ import annotations
import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from proactive.engine import ProactiveEngine, normalize_nudge_type
from proactive.scorer import ProactiveItem, P1, P2, SCORE_P1, SCORE_P2


def _make_item(nudge_type: str, priority: str = P2) -> ProactiveItem:
    score = SCORE_P1 if priority == P1 else SCORE_P2
    return ProactiveItem(
        nudge_type=nudge_type,
        priority=priority,
        score=score,
        message=f"Test nudge for {nudge_type}",
        alone=False,
        meta={},
    )


def _engine_with_tmp_log(tmp_path: Path) -> ProactiveEngine:
    log_path = tmp_path / "proactive_log.json"
    engine = ProactiveEngine(log_path=log_path)
    return engine


class TestNormalizeNudgeType:
    def test_canonical_map_gym_variants(self):
        assert normalize_nudge_type("gym_session") == "gym"
        assert normalize_nudge_type("gym_reminder") == "gym"
        assert normalize_nudge_type("Gym Session") == "gym"
        assert normalize_nudge_type("workout") == "gym"
        assert normalize_nudge_type("GYM") == "gym"

    def test_unknown_type_lowercased_and_normalized(self):
        assert normalize_nudge_type("Siddhi Check") == "siddhi_check"
        assert normalize_nudge_type("Goal-Update") == "goal_update"

    def test_none_and_empty(self):
        assert normalize_nudge_type(None) == ""
        assert normalize_nudge_type("") == ""


class TestDedupAfterShadowLog:
    def test_dedup_blocks_repeat_within_6h(self, tmp_path):
        engine = _engine_with_tmp_log(tmp_path)
        now = datetime(2026, 6, 22, 10, 0, tzinfo=timezone.utc)
        item = _make_item("gym_session")

        # Log it once
        engine.log_sent(item, now=now)

        # Check within 6h — should be blocked
        assert engine.already_sent_recently("gym_session", now=now + timedelta(hours=1))

    def test_canonical_map_dedup_across_variants(self, tmp_path):
        """Log 'gym_session', then check 'gym_reminder' — should still be deduped."""
        engine = _engine_with_tmp_log(tmp_path)
        now = datetime(2026, 6, 22, 10, 0, tzinfo=timezone.utc)
        engine.log_sent(_make_item("gym_session"), now=now)

        assert engine.already_sent_recently("gym_reminder", now=now + timedelta(hours=2))

    def test_dedup_expires_after_window(self, tmp_path):
        engine = _engine_with_tmp_log(tmp_path)
        now = datetime(2026, 6, 22, 10, 0, tzinfo=timezone.utc)
        engine.log_sent(_make_item("gym_session"), now=now)

        # 7h later — outside 6h window — should NOT be blocked
        assert not engine.already_sent_recently("gym_session", now=now + timedelta(hours=7))

    def test_log_sent_writes_canonical_nudge_type(self, tmp_path):
        engine = _engine_with_tmp_log(tmp_path)
        engine.log_sent(_make_item("gym_session"))
        entries = json.loads(engine._log_path.read_text())
        assert entries[0]["nudge_type"] == "gym"
        assert entries[0]["raw_nudge_type"] == "gym_session"
