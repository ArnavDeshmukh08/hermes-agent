"""Apollo.io enrichment for Jack — look up an unknown contact by email/name/domain.

Used when a person shows up in email or on the calendar who isn't in Orsa yet: Jack
enriches them via Apollo and flags them for review (see integrations.contact_reconciler)
rather than silently ignoring them.

Auth: an Apollo API key from the env var JACK_APOLLO_API_KEY. With no key set the
client is a no-op (returns None) — the feature simply stays dark, consistent with
"integrations fail silently, never crash the conversation".

Note: this talks to Apollo's REST API directly with `requests`; it does not use the
claude.ai Apollo MCP connector (that needs interactive OAuth and isn't available to the
running gateway). The key is read from the env at call time and never logged.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_MATCH_URL = "https://api.apollo.io/api/v1/people/match"
_DEFAULT_TIMEOUT = 8.0


def _api_key() -> str:
    return os.environ.get("JACK_APOLLO_API_KEY", "").strip()


class ApolloClient:
    """Thin wrapper over Apollo's People Match (enrichment) endpoint.

    enrich_person() returns a slim dict or None; it never raises and never logs the key.
    """

    def __init__(self, api_key: str | None = None, timeout: float | None = None) -> None:
        self._api_key = api_key if api_key is not None else _api_key()
        self._timeout = timeout if timeout is not None else float(
            os.environ.get("JACK_APOLLO_TIMEOUT", str(_DEFAULT_TIMEOUT))
        )

    def is_configured(self) -> bool:
        """True if an API key is available (does not make a network call)."""
        return bool(self._api_key)

    def enrich_person(
        self,
        *,
        email: str | None = None,
        name: str | None = None,
        domain: str | None = None,
    ) -> dict | None:
        """Look up a person and return a slim profile dict, or None.

        Slim dict: {name, title, company, email, linkedin_url, city, source: "apollo"}.
        Returns None when: no API key, no lookup key given, `requests` missing, the HTTP
        call fails, or Apollo returns no match. Never raises.
        """
        if not self._api_key:
            return None
        if not (email or name or domain):
            return None

        try:
            import requests  # lazy — treated as optional
        except ImportError:
            return None

        payload: dict[str, object] = {}
        if email:
            payload["email"] = email
        if name:
            payload["name"] = name
        if domain:
            payload["domain"] = domain

        # Apollo accepts the key via header (x-api-key). Never logged.
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "x-api-key": self._api_key,
        }

        try:
            resp = requests.post(
                _MATCH_URL, json=payload, headers=headers, timeout=self._timeout
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:  # noqa: BLE001 — network/HTTP/JSON errors all → no match
            logger.warning("apollo: enrichment lookup failed (key/inputs not logged)")
            return None

        person = data.get("person") if isinstance(data, dict) else None
        if not isinstance(person, dict):
            return None

        org = person.get("organization") or {}
        return {
            "name": person.get("name")
            or " ".join(
                p for p in (person.get("first_name"), person.get("last_name")) if p
            ).strip()
            or None,
            "title": person.get("title"),
            "company": org.get("name") if isinstance(org, dict) else None,
            "email": person.get("email") or email,
            "linkedin_url": person.get("linkedin_url"),
            "city": person.get("city"),
            "source": "apollo",
        }
