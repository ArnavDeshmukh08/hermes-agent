"""Tests for integrations.gmail.GmailClient — offline, with a fake Gmail service.

Proves: unread listing parses headers, get_message decodes the body, create_draft
builds a valid draft — and, critically, that the client exposes NO send path.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from integrations.gmail import GmailClient  # noqa: E402


# ---------------------------------------------------------------------------
# Fake Gmail service
# ---------------------------------------------------------------------------

def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


class _Exec:
    def __init__(self, value=None, exc=None):
        self._value, self._exc = value, exc

    def execute(self):
        if self._exc:
            raise self._exc
        return self._value


class _Messages:
    def __init__(self, listing, metadata, full):
        self._listing, self._metadata, self._full = listing, metadata, full

    def list(self, **kwargs):  # noqa: A003
        return _Exec(self._listing)

    def get(self, *, userId, id, format="metadata", metadataHeaders=None):  # noqa: A002
        return _Exec(self._full if format == "full" else self._metadata.get(id))


class _Drafts:
    def __init__(self):
        self.created_body = None

    def create(self, *, userId, body):
        self.created_body = body
        return _Exec({"id": "draft-1", "message": {"id": "m-1", "threadId": "t-1"}})


class _Users:
    def __init__(self, messages, drafts):
        self._messages, self._drafts = messages, drafts

    def messages(self):
        return self._messages

    def drafts(self):
        return self._drafts


class _FakeService:
    def __init__(self, listing=None, metadata=None, full=None):
        self._drafts = _Drafts()
        self._users = _Users(
            _Messages(listing or {"messages": []}, metadata or {}, full or {}),
            self._drafts,
        )

    def users(self):
        return self._users


def _client_with(service) -> GmailClient:
    c = GmailClient()
    c._svc = service  # inject, bypass OAuth build
    return c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNotConnected:
    def test_no_service_returns_none(self, monkeypatch):
        # Force build_service to yield None (no OAuth token).
        import integrations.google_auth as ga

        monkeypatch.setattr(ga, "build_service", lambda *a, **k: None)
        client = GmailClient()
        assert client.list_unread() is None
        assert client.get_message("x") is None
        assert client.create_draft(to="a@b.com", subject="s", body="b") is None
        assert client.unread_business_summary_text() == ""


class TestListUnread:
    def test_parses_sender_and_subject(self):
        listing = {"messages": [{"id": "m1"}]}
        metadata = {
            "m1": {
                "id": "m1",
                "threadId": "t1",
                "snippet": "hello there",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Sarah Lee <sarah@acme.com>"},
                        {"name": "Subject", "value": "Partnership"},
                        {"name": "Date", "value": "Sat, 5 Jul 2026 09:00:00 +0530"},
                        {"name": "Message-ID", "value": "<abc@acme.com>"},
                    ]
                },
            }
        }
        client = _client_with(_FakeService(listing=listing, metadata=metadata))
        unread = client.list_unread()
        assert unread is not None and len(unread) == 1
        m = unread[0]
        assert m["from"] == "Sarah Lee"
        assert m["from_email"] == "sarah@acme.com"
        assert m["subject"] == "Partnership"
        assert m["rfc_message_id"] == "<abc@acme.com>"

    def test_empty_inbox_returns_empty_list(self):
        client = _client_with(_FakeService(listing={"messages": []}))
        assert client.list_unread() == []


class TestGetMessage:
    def test_decodes_plain_body(self):
        full = {
            "id": "m1",
            "threadId": "t1",
            "payload": {
                "mimeType": "multipart/alternative",
                "headers": [
                    {"name": "From", "value": "Bob <bob@corp.com>"},
                    {"name": "Subject", "value": "Quote"},
                ],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64("the body text")}},
                ],
            },
        }
        client = _client_with(_FakeService(full=full))
        msg = client.get_message("m1")
        assert msg is not None
        assert msg["body"] == "the body text"
        assert msg["from_email"] == "bob@corp.com"


class TestCreateDraft:
    def test_creates_threaded_draft(self):
        svc = _FakeService()
        client = _client_with(svc)
        result = client.create_draft(
            to="sarah@acme.com",
            subject="Re: Partnership",
            body="Hi Sarah,\n\nThanks.",
            thread_id="t1",
            in_reply_to="<abc@acme.com>",
        )
        assert result == {"id": "draft-1", "message_id": "m-1", "thread_id": "t-1"}

        body = svc._drafts.created_body
        assert body["message"]["threadId"] == "t1"
        # Decode the raw MIME and confirm headers + body round-tripped.
        raw = base64.urlsafe_b64decode(body["message"]["raw"].encode()).decode()
        assert "To: sarah@acme.com" in raw
        assert "In-Reply-To: <abc@acme.com>" in raw
        assert "Thanks." in raw


class TestNoSendPath:
    def test_client_exposes_no_send_method(self):
        """Draft-only guarantee: there is no send-capable method on the client."""
        forbidden = [n for n in dir(GmailClient) if "send" in n.lower()]
        assert forbidden == [], f"GmailClient must have no send path, found: {forbidden}"

    def test_source_has_no_send_call(self):
        # Look for actual call forms (with "("), so the docstring that *names* the
        # forbidden APIs as prose doesn't trip this guard.
        src = (_REPO_ROOT / "integrations" / "gmail.py").read_text()
        assert "messages().send(" not in src
        assert "drafts().send(" not in src
