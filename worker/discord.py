"""Discord notification (mission §5).

When Hermes Prime receives `task_complete`, it sends a summary to Discord. Uses a
webhook (DISCORD_WEBHOOK_URL) via stdlib urllib — no discord.py dependency. When
no webhook is set or HERMES_DISCORD_DRYRUN is truthy, the summary is printed and
appended to out/notifications.log instead, so the flow is verifiable offline.

This module sends *notifications only*. It has no access to outreach sending —
the no-auto-send guardrail is structural.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path


def _truthy(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def format_summary(payload: dict) -> str:
    """Human-readable Discord summary from a task_complete payload."""
    spec = payload.get("spec", {})
    counts = payload.get("counts", {})
    sheet = payload.get("sheet", {})
    target = spec.get("target", "?")
    location = spec.get("location") or "—"
    where = sheet.get("path") or f"{sheet.get('backend', '?')}:{sheet.get('tab', '?')}"
    lines = [
        "✅ **Lead task complete**",
        f"• Target: **{target}**  ·  Location: {location}",
        f"• Leads written: **{counts.get('written', 0)}** "
        f"(discovered {counts.get('discovered', 0)}, validated {counts.get('validated', 0)})",
        f"• Columns: {', '.join(payload.get('columns', []))}",
        f"• Sheet: {where}",
        f"• Provenance: {counts.get('with_provenance', 0)}/{counts.get('written', 0)} rows sourced",
    ]
    if payload.get("outreach"):
        lines.append(f"• Pitches drafted: **{counts.get('pitches', 0)}** (status PENDING REVIEW — no sends)")
    if payload.get("runtime_s") is not None:
        lines.append(f"• Runtime: {payload['runtime_s']:.2f}s ({payload.get('mode', 'parallel')})")
    if payload.get("failed"):
        lines.append(f"⚠️ Failed sub-tasks: {payload['failed']}")
    return "\n".join(lines)


def send_summary(payload: dict) -> dict:
    """Send the summary. Returns {sent, transport}."""
    text = format_summary(payload)
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    dry = _truthy(os.environ.get("HERMES_DISCORD_DRYRUN", "")) or not webhook

    if dry:
        print("\n--- DISCORD (dry-run) ---\n" + text + "\n-------------------------")
        out_dir = Path(os.environ.get("HERMES_OUT_DIR") or "out")
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "notifications.log", "a", encoding="utf-8") as fh:
            fh.write(text + "\n\n")
        return {"sent": False, "transport": "dry-run"}

    data = json.dumps({"content": text}).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15):
            pass
        return {"sent": True, "transport": "webhook"}
    except Exception as e:  # noqa: BLE001 - a failed notify must not sink the run
        print(f"[discord] webhook failed: {type(e).__name__}")  # no URL/secret in the message
        return {"sent": False, "transport": "webhook-error"}
