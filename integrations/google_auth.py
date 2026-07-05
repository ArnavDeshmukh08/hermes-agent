"""Unified Google OAuth credential provider for Jack (Calendar + Gmail).

Why OAuth and not a service account:
    A Google *service account* can drive a *shared* calendar, but it cannot read a
    personal @gmail.com inbox or create drafts in it without domain-wide delegation
    (Workspace-only). Jack authenticates against a personal Google account, so we use
    the OAuth "installed app" flow with offline access. The one-time consent mints a
    *refresh token* that is stored on disk; every subsequent start reads that token and
    silently refreshes the short-lived access token — so Jack survives a restart
    without re-authenticating.

Design rules (mirror integrations/__init__.py):
    - All google libs are imported lazily inside functions, so the test suite and lint
      run without google-auth / google-auth-oauthlib installed.
    - Every public function degrades gracefully: returns None (never raises) when creds
      are missing, expired-and-unrefreshable, or a lib is absent.
    - Tokens are NEVER logged. Paths and boolean state only.

Scopes (read + draft, deliberately NO send capability in Jack's code):
    - calendar.readonly  — read calendar events
    - gmail.readonly     — read messages
    - gmail.compose      — create drafts (narrowest scope that drafts; there is no
                           draft-only scope). Jack's code has no send path.

One-time setup: run  python3 bin/jack_google_auth.py  (see that script).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Read + draft. Ordered/canonical so a token minted here always covers every feature.
GOOGLE_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

# Default on-disk locations (outside the git repo, in Jack's private dir).
_DEFAULT_TOKEN_PATH = os.path.expanduser("~/.hermes/google_token.json")
_DEFAULT_CLIENT_PATH = os.path.expanduser("~/.hermes/google_oauth_client.json")


def token_path() -> str:
    """Path to the stored OAuth token (refresh + access). Env-overridable."""
    return os.environ.get("JACK_GOOGLE_TOKEN", _DEFAULT_TOKEN_PATH)


def client_secret_path() -> str:
    """Path to the OAuth *client* secret json downloaded from Google Cloud. Env-overridable."""
    return os.environ.get("JACK_GOOGLE_OAUTH_CLIENT", _DEFAULT_CLIENT_PATH)


def is_connected() -> bool:
    """True if a usable token file is present (does not perform a network refresh)."""
    return os.path.isfile(token_path())


def get_credentials():
    """Return refreshed google Credentials, or None on ANY failure.

    Loads the stored token, refreshes it in place (persisting the new access token)
    when expired, and returns a ready-to-use Credentials object. Returns None when:
    the token file is missing, the google libs aren't installed, the token can't be
    refreshed (revoked / no refresh_token), or any other error. Never raises.
    """
    path = token_path()
    if not os.path.isfile(path):
        return None

    try:
        # Lazy imports — google libs are optional at import time.
        from google.auth.transport.requests import Request  # type: ignore
        from google.oauth2.credentials import Credentials  # type: ignore

        creds = Credentials.from_authorized_user_file(path, GOOGLE_SCOPES)
    except ImportError:
        logger.debug("google_auth: google libs not installed — treating as not connected")
        return None
    except Exception:  # noqa: BLE001 — malformed token file, etc.
        logger.warning("google_auth: failed to load token file (contents not logged)")
        return None

    # Valid and unexpired — use as-is.
    if getattr(creds, "valid", False):
        return creds

    # Expired but refreshable — refresh and persist.
    if getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
        try:
            creds.refresh(Request())
        except Exception:  # noqa: BLE001 — network error, revoked grant, etc.
            logger.warning("google_auth: token refresh failed — re-auth may be required")
            return None
        _persist(creds, path)
        return creds

    # No refresh token and not valid — unusable.
    logger.warning("google_auth: token present but not valid and not refreshable")
    return None


def build_service(api: str, version: str):
    """Build a googleapiclient service for *api* using OAuth creds, or None.

    e.g. build_service("calendar", "v3") / build_service("gmail", "v1").
    Never raises.
    """
    creds = get_credentials()
    if creds is None:
        return None
    try:
        from googleapiclient.discovery import build  # type: ignore

        return build(api, version, credentials=creds, cache_discovery=False)
    except ImportError:
        return None
    except Exception:  # noqa: BLE001
        logger.warning("google_auth: failed to build %s/%s service", api, version)
        return None


def _persist(creds, path: str) -> None:
    """Write refreshed creds back to *path* at mode 0600. Never raises."""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = creds.to_json()
        # Write then tighten perms — token contents never logged.
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.chmod(path, 0o600)
    except Exception:  # noqa: BLE001 — persistence failure must not break the caller
        logger.warning("google_auth: could not persist refreshed token to disk")
