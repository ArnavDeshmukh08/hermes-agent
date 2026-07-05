#!/usr/bin/env python3
"""One-time Google OAuth consent for Jack (Calendar + Gmail read + Gmail drafts).

Run this ONCE, interactively, on a machine with a browser (your Mac):

    python3 bin/jack_google_auth.py

Prerequisites (one-time, in Google Cloud Console for the account you want Jack to read):
    1. Create / pick a project → enable the "Google Calendar API" and "Gmail API".
    2. Configure the OAuth consent screen (External, add your own Google account as a
       test user is enough — no verification needed for personal use).
    3. Create an OAuth client ID of type "Desktop app", download the JSON, and save it to
           ~/.hermes/google_oauth_client.json
       (or point JACK_GOOGLE_OAUTH_CLIENT at wherever you put it).

This opens a browser, you pick the Google account + grant the scopes, and the resulting
refresh token is written to ~/.hermes/google_token.json (mode 0600). After that Jack
reads that token on every start and refreshes it automatically — no re-auth on restart.

This script is the ONLY place the interactive flow runs. It writes a token and prints a
one-line confirmation; it never prints the token itself.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make `integrations` importable whether run from repo root or elsewhere.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from integrations.google_auth import (  # noqa: E402
    GOOGLE_SCOPES,
    client_secret_path,
    token_path,
)


def main() -> int:
    client_path = client_secret_path()
    tok_path = token_path()

    if not os.path.isfile(client_path):
        print(
            f"❌ OAuth client secret not found at:\n     {client_path}\n\n"
            "Create a Desktop-app OAuth client in Google Cloud Console, download the JSON, "
            "and save it there (or set JACK_GOOGLE_OAUTH_CLIENT). See the header of this "
            "script for the full one-time setup steps.",
            file=sys.stderr,
        )
        return 2

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print(
            "❌ google-auth-oauthlib is not installed. Install the integration deps:\n"
            "     pip install -r integrations/requirements.txt",
            file=sys.stderr,
        )
        return 2

    print("Opening a browser for Google consent… pick the account Jack should read.")
    flow = InstalledAppFlow.from_client_secrets_file(client_path, GOOGLE_SCOPES)
    # run_local_server spins a localhost redirect; falls back to console if no browser.
    creds = flow.run_local_server(port=0)

    Path(tok_path).parent.mkdir(parents=True, exist_ok=True)
    with open(tok_path, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())
    os.chmod(tok_path, 0o600)

    # Report which account was authorized (id_token email if present) — no token printed.
    who = ""
    try:
        who = f" for {creds.id_token.get('email')}" if getattr(creds, "id_token", None) else ""
    except Exception:  # noqa: BLE001
        who = ""

    print(
        f"✅ Google connected{who}. Token saved to {tok_path} (mode 0600).\n"
        "   Jack will read + refresh it automatically from now on — no re-auth on restart."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
