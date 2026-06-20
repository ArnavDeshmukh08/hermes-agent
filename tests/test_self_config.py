"""Tests for jack_tools.self_config.JackSelfConfig.

All file I/O uses pytest tmp_path.
systemctl is always monkeypatched — never called for real.
_SETTLE_SECONDS is patched to 0 so tests finish immediately.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import jack_tools.self_config as sc_mod
from jack_tools.self_config import (
    ALLOWED_KEYS,
    FRIENDLY_TO_KEY,
    JackSelfConfig,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def zero_settle(monkeypatch):
    """Patch _SETTLE_SECONDS to 0 so no test sleeps."""
    monkeypatch.setattr(sc_mod, "_SETTLE_SECONDS", 0)


def _make_cfg(tmp_path: Path, restart_fn=None) -> JackSelfConfig:
    env_path = tmp_path / ".env"
    log_path = tmp_path / "logs" / "self_config.log"
    return JackSelfConfig(env_path=env_path, log_path=log_path, restart_fn=restart_fn)


def _ok_restart(_svc: str) -> bool:
    return True


def _fail_restart(_svc: str) -> bool:
    return False


# ---------------------------------------------------------------------------
# set() — valid inputs
# ---------------------------------------------------------------------------


def test_set_briefing_time_valid(tmp_path):
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    result = cfg.set("JACK_BRIEFING_TIME_IST", "08:00")
    assert result["success"] is True
    assert "JACK_BRIEFING_TIME_IST" in result["message"]
    assert cfg.get("JACK_BRIEFING_TIME_IST") == "08:00"


def test_set_briefing_time_normalises_single_digit_hour(tmp_path):
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    result = cfg.set("JACK_BRIEFING_TIME_IST", "9:05")
    assert result["success"] is True
    assert cfg.get("JACK_BRIEFING_TIME_IST") == "09:05"


def test_set_briefing_time_accepts_23_59(tmp_path):
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    result = cfg.set("JACK_BRIEFING_TIME_IST", "23:59")
    assert result["success"] is True
    assert cfg.get("JACK_BRIEFING_TIME_IST") == "23:59"


# ---------------------------------------------------------------------------
# set() — invalid time
# ---------------------------------------------------------------------------


def test_set_briefing_time_invalid_format(tmp_path):
    env_path = tmp_path / ".env"
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    result = cfg.set("JACK_BRIEFING_TIME_IST", "8am")
    assert result["success"] is False
    assert "8am" in result["message"]
    # File must NOT have been written
    assert not env_path.exists()


def test_set_briefing_time_invalid_does_not_write_file(tmp_path):
    env_path = tmp_path / ".env"
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    cfg.set("JACK_BRIEFING_TIME_IST", "25:00")  # bad hour
    assert not env_path.exists()


# ---------------------------------------------------------------------------
# set() — unknown key blocked
# ---------------------------------------------------------------------------


def test_set_unknown_key_blocked(tmp_path):
    env_path = tmp_path / ".env"
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    result = cfg.set("JACK_EVIL_KEY", "something")
    assert result["success"] is False
    assert "JACK_EVIL_KEY" in result["message"]
    # Absolutely no file must be touched
    assert not env_path.exists()


def test_set_unknown_key_message_lists_configurable(tmp_path):
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    result = cfg.set("JACK_DOES_NOT_EXIST", "x")
    assert "JACK_BRIEFING_ENABLED" in result["message"]


# ---------------------------------------------------------------------------
# set() — int validation (JACK_REMINDER_POLL_SECONDS, range 30–300)
# ---------------------------------------------------------------------------


def test_set_reminder_poll_valid(tmp_path):
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    result = cfg.set("JACK_REMINDER_POLL_SECONDS", "60")
    assert result["success"] is True
    assert cfg.get("JACK_REMINDER_POLL_SECONDS") == "60"


def test_set_reminder_poll_below_min_rejected(tmp_path):
    env_path = tmp_path / ".env"
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    result = cfg.set("JACK_REMINDER_POLL_SECONDS", "10")
    assert result["success"] is False
    assert "30" in result["message"]
    assert not env_path.exists()


def test_set_reminder_poll_above_max_rejected(tmp_path):
    env_path = tmp_path / ".env"
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    result = cfg.set("JACK_REMINDER_POLL_SECONDS", "999")
    assert result["success"] is False
    assert "300" in result["message"]
    assert not env_path.exists()


def test_set_reminder_poll_non_integer_rejected(tmp_path):
    env_path = tmp_path / ".env"
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    result = cfg.set("JACK_REMINDER_POLL_SECONDS", "fast")
    assert result["success"] is False
    assert not env_path.exists()


# ---------------------------------------------------------------------------
# set() — bool validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("true", "1"), ("True", "1"), ("1", "1"), ("yes", "1"), ("on", "1"),
    ("false", "0"), ("False", "0"), ("0", "0"), ("no", "0"), ("off", "0"),
])
def test_set_bool_variants(tmp_path, raw, expected):
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    result = cfg.set("JACK_BRIEFING_ENABLED", raw)
    assert result["success"] is True
    assert cfg.get("JACK_BRIEFING_ENABLED") == expected


def test_set_bool_invalid_rejected(tmp_path):
    env_path = tmp_path / ".env"
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    result = cfg.set("JACK_BRIEFING_ENABLED", "maybe")
    assert result["success"] is False
    assert not env_path.exists()


# ---------------------------------------------------------------------------
# Restart failure: .env IS written but success=False is returned
# ---------------------------------------------------------------------------


def test_set_returns_failure_when_restart_fails_but_env_written(tmp_path):
    env_path = tmp_path / ".env"
    cfg = _make_cfg(tmp_path, restart_fn=_fail_restart)
    result = cfg.set("JACK_BRIEFING_TIME_IST", "07:30")
    # success must be False (honest)
    assert result["success"] is False
    # but the file MUST have been written
    assert env_path.exists()
    assert "JACK_BRIEFING_TIME_IST=07:30" in env_path.read_text()
    # message must acknowledge the write happened
    assert ".env" in result["message"] or "Updated" in result["message"]


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------


def test_get_returns_current_value(tmp_path):
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    cfg.set("JACK_BRIEFING_ENABLED", "1")
    assert cfg.get("JACK_BRIEFING_ENABLED") == "1"


def test_get_returns_none_for_absent_key(tmp_path):
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    assert cfg.get("JACK_NEWS_ENABLED") is None


def test_get_returns_none_for_unknown_key(tmp_path):
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    assert cfg.get("JACK_UNKNOWN") is None


# ---------------------------------------------------------------------------
# Atomic write: other lines and comments are preserved
# ---------------------------------------------------------------------------


def test_atomic_write_preserves_other_lines(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# my comment\n"
        "SOME_OTHER_VAR=hello\n"
        "JACK_BRIEFING_ENABLED=0\n"
        "ANOTHER_VAR=world\n",
        encoding="utf-8",
    )
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    cfg.set("JACK_BRIEFING_ENABLED", "1")

    content = env_path.read_text(encoding="utf-8")
    assert "# my comment" in content
    assert "SOME_OTHER_VAR=hello" in content
    assert "ANOTHER_VAR=world" in content
    assert "JACK_BRIEFING_ENABLED=1" in content
    # old value gone
    assert "JACK_BRIEFING_ENABLED=0" not in content


def test_atomic_write_appends_when_key_absent(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING=yes\n", encoding="utf-8")
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    cfg.set("JACK_NEWS_ENABLED", "1")

    content = env_path.read_text(encoding="utf-8")
    assert "EXISTING=yes" in content
    assert "JACK_NEWS_ENABLED=1" in content


def test_atomic_write_mode_is_0600(tmp_path):
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    cfg.set("JACK_MEMORY_ENABLED", "1")
    env_path = tmp_path / ".env"
    mode = oct(env_path.stat().st_mode & 0o777)
    assert mode == "0o600"


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def test_log_line_written_on_change(tmp_path):
    log_path = tmp_path / "logs" / "self_config.log"
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    cfg.set("JACK_BRIEFING_TIME_IST", "10:00")

    assert log_path.exists()
    log_content = log_path.read_text(encoding="utf-8")
    assert "SET JACK_BRIEFING_TIME_IST=10:00" in log_content
    assert "(was" in log_content


def test_log_records_old_value(tmp_path):
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    cfg.set("JACK_BRIEFING_TIME_IST", "08:00")
    cfg.set("JACK_BRIEFING_TIME_IST", "09:00")

    log_path = tmp_path / "logs" / "self_config.log"
    log_content = log_path.read_text(encoding="utf-8")
    # Second log line should reference old value 08:00
    assert "was 08:00" in log_content


# ---------------------------------------------------------------------------
# list_configurable()
# ---------------------------------------------------------------------------


def test_list_configurable_returns_all_seven_keys(tmp_path):
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    items = cfg.list_configurable()
    keys_returned = {item["key"] for item in items}
    assert keys_returned == set(ALLOWED_KEYS.keys())
    assert len(items) == 7


def test_list_configurable_has_required_fields(tmp_path):
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    for item in cfg.list_configurable():
        assert "key" in item
        assert "friendly" in item
        assert "value" in item
        assert "type" in item
        assert "description" in item


def test_list_configurable_reflects_current_value(tmp_path):
    cfg = _make_cfg(tmp_path, restart_fn=_ok_restart)
    cfg.set("JACK_WEATHER_ENABLED", "1")
    items = {i["key"]: i for i in cfg.list_configurable()}
    assert items["JACK_WEATHER_ENABLED"]["value"] == "1"


# ---------------------------------------------------------------------------
# FRIENDLY_TO_KEY constant
# ---------------------------------------------------------------------------


def test_friendly_to_key_has_all_seven(tmp_path):
    assert len(FRIENDLY_TO_KEY) == 7
    assert FRIENDLY_TO_KEY["briefing_time"] == "JACK_BRIEFING_TIME_IST"
    assert FRIENDLY_TO_KEY["reminder_poll_seconds"] == "JACK_REMINDER_POLL_SECONDS"
    assert FRIENDLY_TO_KEY["proactive"] == "JACK_PROACTIVE_ENABLED"


# ---------------------------------------------------------------------------
# restart_service() — graceful degradation when systemctl is absent (macOS)
# ---------------------------------------------------------------------------


def test_restart_service_returns_false_not_raises_when_systemctl_missing(tmp_path, monkeypatch):
    """On macOS systemctl doesn't exist; restart_service must return False, not raise."""
    def _raise_fnf(*args, **kwargs):
        raise FileNotFoundError("systemctl not found")

    monkeypatch.setattr(subprocess, "run", _raise_fnf)
    cfg = _make_cfg(tmp_path)
    result = cfg.restart_service("jack-briefing.service")
    assert result is False


def test_restart_service_rejects_unknown_service(tmp_path):
    cfg = _make_cfg(tmp_path)
    result = cfg.restart_service("evil-rm-rf.service")
    assert result is False


def test_restart_service_returns_false_on_called_process_error(tmp_path, monkeypatch):
    call_count = {"n": 0}

    def _fail_first(*args, **kwargs):
        if call_count["n"] == 0:
            call_count["n"] += 1
            raise subprocess.CalledProcessError(1, args[0])
        # is-active would never be reached
        return subprocess.CompletedProcess(args[0], 0, stdout="active")

    monkeypatch.setattr(subprocess, "run", _fail_first)
    cfg = _make_cfg(tmp_path)
    result = cfg.restart_service("jack-briefing.service")
    assert result is False


def test_restart_service_returns_true_when_active(tmp_path, monkeypatch):
    def _fake_run(args, **kwargs):
        # Both restart and is-active succeed
        return subprocess.CompletedProcess(args, 0, stdout="active\n")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    cfg = _make_cfg(tmp_path)
    result = cfg.restart_service("jack-briefing.service")
    assert result is True


def test_restart_service_returns_false_when_inactive(tmp_path, monkeypatch):
    def _fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="inactive\n")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    cfg = _make_cfg(tmp_path)
    result = cfg.restart_service("jack-briefing.service")
    assert result is False
