"""Tests for integrations.google_provider — the direct↔remote factory switch."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from integrations import google_provider as gp  # noqa: E402
from integrations import mac_remote  # noqa: E402
from integrations.calendar import CalendarClient  # noqa: E402
from integrations.gmail import GmailClient  # noqa: E402
from integrations.orsa import OrsaClient  # noqa: E402


class TestFactoryDefaultDirect:
    def test_direct_by_default(self, monkeypatch):
        monkeypatch.delenv("JACK_GOOGLE_REMOTE", raising=False)
        assert isinstance(gp.calendar_client(), CalendarClient)
        assert isinstance(gp.gmail_client(), GmailClient)
        assert isinstance(gp.orsa_client(), OrsaClient)


class TestFactoryRemote:
    def test_remote_when_flagged(self, monkeypatch):
        monkeypatch.setenv("JACK_GOOGLE_REMOTE", "1")
        assert isinstance(gp.calendar_client(), mac_remote.RemoteCalendar)
        assert isinstance(gp.gmail_client(), mac_remote.RemoteGmail)
        assert isinstance(gp.orsa_client(), mac_remote.RemoteOrsa)

    def test_falsey_values_stay_direct(self, monkeypatch):
        monkeypatch.setenv("JACK_GOOGLE_REMOTE", "0")
        assert isinstance(gp.orsa_client(), OrsaClient)


class TestFlagsProvider:
    def test_flags_best_effort_swallows_errors(self, monkeypatch):
        # Even if the underlying reconcile explodes, the provider returns '' not raise.
        monkeypatch.setenv("JACK_GOOGLE_REMOTE", "1")
        monkeypatch.setattr(mac_remote, "unknown_contact_flags_text", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert gp.unknown_contact_flags_text() == ""
