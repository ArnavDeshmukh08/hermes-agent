"""Tests for integrations.apollo.ApolloClient — offline, with a stubbed requests.post."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

import integrations.apollo as apollo_mod  # noqa: E402
from integrations.apollo import ApolloClient  # noqa: E402


class _Resp:
    def __init__(self, payload, status_ok=True):
        self._payload, self._ok = payload, status_ok

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("HTTP 500")

    def json(self):
        return self._payload


class TestNotConfigured:
    def test_no_key_is_noop(self, monkeypatch):
        monkeypatch.delenv("JACK_APOLLO_API_KEY", raising=False)
        client = ApolloClient()
        assert client.is_configured() is False
        assert client.enrich_person(email="a@b.com") is None

    def test_no_lookup_key_returns_none(self):
        client = ApolloClient(api_key="k")
        assert client.enrich_person() is None


class TestEnrich:
    def test_maps_apollo_person_to_slim(self, monkeypatch):
        payload = {
            "person": {
                "name": "Sarah Lee",
                "title": "Founder",
                "email": "sarah@acme.com",
                "linkedin_url": "https://linkedin.com/in/sarahlee",
                "city": "Austin",
                "organization": {"name": "Acme Med Spa"},
            }
        }
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _Resp(payload)

        monkeypatch.setattr(apollo_mod, "requests", type("R", (), {"post": staticmethod(fake_post)}), raising=False)
        # requests is imported lazily inside enrich_person via `import requests`, so patch sys.modules.
        import types

        fake_requests = types.SimpleNamespace(post=fake_post)
        monkeypatch.setitem(sys.modules, "requests", fake_requests)

        client = ApolloClient(api_key="secret-key")
        result = client.enrich_person(email="sarah@acme.com", name="Sarah Lee")

        assert result is not None
        assert result["name"] == "Sarah Lee"
        assert result["title"] == "Founder"
        assert result["company"] == "Acme Med Spa"
        assert result["source"] == "apollo"
        # Key travels in the header, never the URL/body.
        assert captured["headers"]["x-api-key"] == "secret-key"
        assert "secret-key" not in captured["url"]

    def test_http_error_returns_none(self, monkeypatch):
        import types

        def fake_post(*a, **k):
            return _Resp({}, status_ok=False)

        monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(post=fake_post))
        client = ApolloClient(api_key="k")
        assert client.enrich_person(email="x@y.com") is None

    def test_no_match_returns_none(self, monkeypatch):
        import types

        monkeypatch.setitem(
            sys.modules, "requests",
            types.SimpleNamespace(post=lambda *a, **k: _Resp({"person": None})),
        )
        client = ApolloClient(api_key="k")
        assert client.enrich_person(email="x@y.com") is None
