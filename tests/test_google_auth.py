"""Tests for integrations.google_auth — offline.

The google libs aren't installed in the test env, which is exactly the "degrade
gracefully" path we want to prove: missing token OR missing libs → None, never a raise.
Path resolution + env overrides are also covered.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

import integrations.google_auth as ga  # noqa: E402


class TestPaths:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("JACK_GOOGLE_TOKEN", raising=False)
        monkeypatch.delenv("JACK_GOOGLE_OAUTH_CLIENT", raising=False)
        assert ga.token_path().endswith("/.hermes/google_token.json")
        assert ga.client_secret_path().endswith("/.hermes/google_oauth_client.json")

    def test_env_override(self, monkeypatch, tmp_path):
        tok = tmp_path / "tok.json"
        monkeypatch.setenv("JACK_GOOGLE_TOKEN", str(tok))
        assert ga.token_path() == str(tok)


class TestIsConnected:
    def test_absent_token(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JACK_GOOGLE_TOKEN", str(tmp_path / "nope.json"))
        assert ga.is_connected() is False

    def test_present_token(self, monkeypatch, tmp_path):
        tok = tmp_path / "tok.json"
        tok.write_text("{}")
        monkeypatch.setenv("JACK_GOOGLE_TOKEN", str(tok))
        assert ga.is_connected() is True


class TestGetCredentialsDegrades:
    def test_missing_token_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JACK_GOOGLE_TOKEN", str(tmp_path / "absent.json"))
        assert ga.get_credentials() is None

    def test_present_token_but_no_libs_returns_none(self, monkeypatch, tmp_path):
        # google libs are not installed here → ImportError path → None (no raise).
        tok = tmp_path / "tok.json"
        tok.write_text('{"refresh_token": "x"}')
        monkeypatch.setenv("JACK_GOOGLE_TOKEN", str(tok))
        assert ga.get_credentials() is None

    def test_build_service_returns_none_without_creds(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JACK_GOOGLE_TOKEN", str(tmp_path / "absent.json"))
        assert ga.build_service("gmail", "v1") is None


class TestScopes:
    def test_scopes_cover_calendar_and_gmail(self):
        joined = " ".join(ga.GOOGLE_SCOPES)
        assert "calendar.readonly" in joined
        assert "gmail.readonly" in joined
        assert "gmail.compose" in joined

    def test_no_full_send_or_write_scope(self):
        # We deliberately avoid gmail.send / full https://mail scope.
        for scope in ga.GOOGLE_SCOPES:
            assert "gmail.send" not in scope
            assert scope != "https://mail.google.com/"
