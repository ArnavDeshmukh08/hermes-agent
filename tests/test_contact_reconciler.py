"""Tests for integrations.contact_reconciler — offline, with stubbed Orsa/Gmail/Apollo."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

import integrations.apollo as apollo_mod  # noqa: E402
import integrations.contact_reconciler as rec  # noqa: E402
import integrations.gmail as gmail_mod  # noqa: E402
import integrations.orsa as orsa_mod  # noqa: E402
from integrations.contact_reconciler import (  # noqa: E402
    FlaggedContact,
    flags_summary_text,
    reconcile_contacts,
    reconcile_from_gmail,
)


class _FakeOrsa:
    def __init__(self, emails):
        self._emails = emails

    def known_emails(self):
        return self._emails


class _FakeApollo:
    def __init__(self, configured=True, result=None):
        self._configured, self._result = configured, result

    def is_configured(self):
        return self._configured

    def enrich_person(self, **kwargs):
        return self._result


def _patch(monkeypatch, orsa_emails, apollo=None):
    monkeypatch.setattr(orsa_mod, "OrsaClient", lambda *a, **k: _FakeOrsa(orsa_emails))
    monkeypatch.setattr(apollo_mod, "ApolloClient", lambda *a, **k: apollo or _FakeApollo(configured=False))


class TestReconcileContacts:
    def test_flags_unknown_business_contact(self, monkeypatch):
        _patch(monkeypatch, {"known@acme.com"},
               apollo=_FakeApollo(result={"title": "CEO", "company": "NewCo"}))
        candidates = [
            {"email": "known@acme.com", "name": "Known", "seen_in": "email"},
            {"email": "stranger@newco.com", "name": "Stranger", "seen_in": "email"},
        ]
        flags = reconcile_contacts(candidates)
        assert len(flags) == 1
        assert flags[0].email == "stranger@newco.com"
        assert flags[0].apollo == {"title": "CEO", "company": "NewCo"}

    def test_skips_personal_webmail(self, monkeypatch):
        _patch(monkeypatch, set())
        flags = reconcile_contacts([
            {"email": "buddy@gmail.com", "name": "Buddy", "seen_in": "email"},
            {"email": "noreply@service.com", "name": "Robot", "seen_in": "email"},
        ])
        assert flags == []

    def test_dedupes_repeat_sender(self, monkeypatch):
        _patch(monkeypatch, set())
        flags = reconcile_contacts([
            {"email": "x@newco.com", "name": "X", "seen_in": "email"},
            {"email": "X@newco.com", "name": "X", "seen_in": "email"},
        ])
        assert len(flags) == 1

    def test_orsa_unavailable_still_flags_business(self, monkeypatch):
        # known_emails None → over-flag business contacts rather than drop them silently.
        monkeypatch.setattr(orsa_mod, "OrsaClient",
                            lambda *a, **k: type("O", (), {"known_emails": lambda self: None})())
        monkeypatch.setattr(apollo_mod, "ApolloClient", lambda *a, **k: _FakeApollo(configured=False))
        flags = reconcile_contacts([{"email": "lead@clinic.com", "name": "Lead", "seen_in": "email"}])
        assert len(flags) == 1


class TestReconcileFromGmail:
    def test_none_when_gmail_unreachable(self, monkeypatch):
        monkeypatch.setattr(gmail_mod, "GmailClient",
                            lambda *a, **k: type("G", (), {"list_unread": lambda self, **kw: None})())
        assert reconcile_from_gmail() is None

    def test_flags_from_unread_senders(self, monkeypatch):
        unread = [{"from_email": "new@clinic.com", "from": "New Clinic"}]
        monkeypatch.setattr(gmail_mod, "GmailClient",
                            lambda *a, **k: type("G", (), {"list_unread": lambda self, **kw: unread})())
        _patch(monkeypatch, set())
        flags = reconcile_from_gmail()
        assert flags is not None and len(flags) == 1
        assert flags[0].email == "new@clinic.com"


class TestSummary:
    def test_summary_mentions_not_in_orsa(self):
        flags = [FlaggedContact(email="a@b.com", name="Ann", seen_in="email",
                                apollo={"title": "CTO", "company": "Bco"})]
        text = flags_summary_text(flags)
        assert "Ann" in text and "not in Orsa" in text and "CTO" in text

    def test_empty(self):
        assert flags_summary_text([]) == ""
