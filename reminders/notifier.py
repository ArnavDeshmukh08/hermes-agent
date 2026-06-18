"""Reminder delivery via the Discord bot REST API.

Jack fires reminders by POSTing a message to a channel using the bot token
(there is NO webhook configured for reminders). Mirrors the stdlib-only style
of worker/discord.py — no discord.py dependency.

Env creds:
- DISCORD_BOT_TOKEN     — bot token (Authorization: Bot <token>)
- DISCORD_HOME_CHANNEL  — channel id to post into
- DISCORD_ALLOWED_USERS — comma-separated ids; first id is @mentioned

Secrets are never logged. On missing creds the call is a dry-run (returns False,
does not raise) so the scheduler keeps the reminder pending for the next poll.
"""

from __future__ import annotations

import json
import os
import urllib.request

_API_BASE = "https://discord.com/api/v10"
_TIMEOUT_S = 15


def _post(url: str, data: bytes, headers: dict[str, str]) -> None:
    """Perform the HTTP POST. Isolated so tests can monkeypatch it."""
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S):  # noqa: S310 - fixed Discord host
        pass


def _default_user_id() -> str | None:
    """First id from DISCORD_ALLOWED_USERS, or None."""
    raw = os.environ.get("DISCORD_ALLOWED_USERS", "")
    for part in raw.split(","):
        part = part.strip()
        if part:
            return part
    return None


def _build_content(message: str, user_id: str | None) -> str:
    """Reminder text, optionally prefixed with an @mention."""
    if user_id:
        return f"<@{user_id}> ⏰ Hey Arnav — reminder: {message}"
    return f"⏰ Hey Arnav — reminder: {message}"


def send_reminder(message: str, *, user_id: str | None = None) -> bool:
    """Deliver a reminder to Discord.

    Retries once on failure. Returns True on success, False on dry-run (missing
    creds) or after a second failure. Never raises; never logs the token.
    """
    token = os.environ.get("DISCORD_BOT_TOKEN")
    channel = os.environ.get("DISCORD_HOME_CHANNEL")
    if not token or not channel:
        print("[reminders.notifier] missing DISCORD_BOT_TOKEN/DISCORD_HOME_CHANNEL — dry-run")
        return False

    if user_id is None:
        user_id = _default_user_id()

    url = f"{_API_BASE}/channels/{channel}/messages"
    data = json.dumps({"content": _build_content(message, user_id)}).encode("utf-8")
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }

    for attempt in (1, 2):
        try:
            _post(url, data, headers)
            return True
        except Exception as exc:  # noqa: BLE001 - delivery failure must not crash the loop
            if attempt == 2:
                # Never include the token or full URL/secret in the log line.
                print(f"[reminders.notifier] delivery failed: {type(exc).__name__}")
    return False
