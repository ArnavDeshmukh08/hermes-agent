"""Tests for the VPS-side Mac-bridge proxies (integrations.mac_remote).

Offline — the HTTP layer (_get/_post) is monkeypatched, so no real network. Verifies
each proxy maps the Mac service's JSON to the same shapes the direct clients return, and
degrades to None/[]/'' when the service is unreachable.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from integrations import mac_remote  # noqa: E402


class TestRemoteCalendar:
    def test_list_events_maps_events(self, monkeypatch):
        monkeypatch.setattr(mac_remote, "_get", lambda *a, **k: {"events": [{"summary": "Standup"}], "summary": "09:00 Standup"})
        assert mac_remote.RemoteCalendar().list_events() == [{"summary": "Standup"}]
        assert mac_remote.RemoteCalendar().events_summary_text() == "09:00 Standup"

    def test_unreachable_returns_none(self, monkeypatch):
        monkeypatch.setattr(mac_remote, "_get", lambda *a, **k: None)
        assert mac_remote.RemoteCalendar().list_events() is None
        assert mac_remote.RemoteCalendar().events_summary_text() == ""

    def test_add_event_is_noop_none(self):
        # Calendar writes are not proxied — must degrade, never raise.
        assert mac_remote.RemoteCalendar().add_event("x", None) is None


class TestRemoteGmail:
    def test_list_unread_maps_messages(self, monkeypatch):
        monkeypatch.setattr(mac_remote, "_get", lambda *a, **k: {"messages": [{"from": "A", "subject": "S"}]})
        assert mac_remote.RemoteGmail().list_unread() == [{"from": "A", "subject": "S"}]

    def test_create_draft_posts_and_maps(self, monkeypatch):
        captured = {}

        def fake_post(path, body):
            captured["path"] = path
            captured["body"] = body
            return {"draft": {"id": "d1", "message_id": "m1", "thread_id": "t1"}}

        monkeypatch.setattr(mac_remote, "_post", fake_post)
        out = mac_remote.RemoteGmail().create_draft(
            to="x@y.com", subject="Re: hi", body="hello", thread_id="t1", in_reply_to="<abc>"
        )
        assert out == {"id": "d1", "message_id": "m1", "thread_id": "t1"}
        assert captured["path"] == "/gmail/draft"
        assert captured["body"]["to"] == "x@y.com"
        assert captured["body"]["in_reply_to"] == "<abc>"

    def test_draft_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(mac_remote, "_post", lambda *a, **k: None)
        assert mac_remote.RemoteGmail().create_draft(to="a", subject="b", body="c") is None


class TestRemoteOrsa:
    def test_counts_and_summary(self, monkeypatch):
        routes = {
            "/orsa/status": {"connected": True},
            "/orsa/count": {"total": 116},
            "/orsa/new_count": {"count": 73},
            "/orsa/summary": {"summary": "• A\n• B"},
        }
        monkeypatch.setattr(mac_remote, "_get", lambda path, params=None: routes[path])
        o = mac_remote.RemoteOrsa()
        assert o.is_connected() is True
        assert o.lead_count() == 116
        assert o.new_leads_count(7) == 73
        assert o.new_leads_summary_text(7) == "• A\n• B"

    def test_unreachable_degrades(self, monkeypatch):
        monkeypatch.setattr(mac_remote, "_get", lambda *a, **k: None)
        o = mac_remote.RemoteOrsa()
        assert o.is_connected() is False
        assert o.lead_count() is None
        assert o.new_leads_count() is None
        assert o.new_leads_summary_text() == ""


class TestAuthHeader:
    def test_token_header_included_when_set(self, monkeypatch):
        monkeypatch.setenv("JACK_MAC_SERVICE_TOKEN", "sekret")
        assert mac_remote._headers().get("X-Jack-Token") == "sekret"

    def test_no_token_header_when_unset(self, monkeypatch):
        monkeypatch.delenv("JACK_MAC_SERVICE_TOKEN", raising=False)
        assert "X-Jack-Token" not in mac_remote._headers()
