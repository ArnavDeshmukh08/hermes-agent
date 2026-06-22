"""Shadow mode logging for ProactiveReasoner decisions.

Writes a structured record of every reasoning cycle to proactive_shadow.log WITHOUT
sending any Discord messages. Arnav reads this in the morning to evaluate reasoning
quality before enabling live mode.

All functions are best-effort — a logging failure must never crash a scheduler cycle.
"""

from __future__ import annotations

import fcntl
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from proactive.scorer import ProactiveItem, PriorityScorer

SHADOW_LOG_PATH = Path.home() / ".hermes" / "logs" / "proactive_shadow.log"
_logger = logging.getLogger("proactive.shadow")


def _build_record(
    now_ist: datetime,
    items: list[ProactiveItem],
    reasoning_raw: Optional[str],
    *,
    mac_reachable: bool,
    dedup_dropped: Optional[list[str]],
    quiet_hours: bool,
) -> str:
    lines: list[str] = []

    # Header
    ts = now_ist.strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"=== SHADOW CYCLE {ts} IST ===")

    # Mac status
    lines.append(f"Mac status: {'reachable' if mac_reachable else 'unreachable'}")

    # Raw reasoning
    if not mac_reachable and not reasoning_raw:
        raw_snippet = "(none — Mac unreachable)"
    else:
        raw_snippet = (reasoning_raw or "").replace("\n", " ")[:500]
    lines.append(f"Raw reasoning (first 500 chars): {raw_snippet}")

    # Items section
    lines.append(f"Items selected by scorer ({len(items)}):")
    for it in items:
        alone_str = str(it.alone)
        lines.append(f"  [{it.priority}] nudge_type=\"{it.nudge_type}\" alone={alone_str}")
        lines.append(f"    message: \"{it.message}\"")
        lines.append(f"    reasoning: \"{it.meta.get('reasoning', '')}\"")

    # WOULD HAVE SENT
    if quiet_hours:
        lines.append("WOULD HAVE SENT: NOTHING")
    elif items:
        rendered_parts = " | ".join(f'"{PriorityScorer.render(it)}"' for it in items)
        lines.append(f"WOULD HAVE SENT: {rendered_parts}")
    else:
        lines.append("WOULD HAVE SENT: NOTHING")

    # Dedup dropped
    lines.append(f"Dedup dropped: {dedup_dropped or []}")

    # Quiet hours
    lines.append(f"Quiet hours: {'yes' if quiet_hours else 'no'}")

    # Footer
    lines.append("===")

    return "\n".join(lines) + "\n\n"


def log_shadow_cycle(
    now_ist: datetime,
    items: list[ProactiveItem],
    reasoning_raw: Optional[str],
    *,
    mac_reachable: bool = True,
    dedup_dropped: Optional[list[str]] = None,
    quiet_hours: bool = False,
    log_path: Path = SHADOW_LOG_PATH,
) -> None:
    """Write a structured shadow-mode record without sending any messages.

    Best-effort: any failure is logged at ERROR level and silently swallowed.
    """
    try:
        record = _build_record(
            now_ist,
            items,
            reasoning_raw,
            mac_reachable=mac_reachable,
            dedup_dropped=dedup_dropped,
            quiet_hours=quiet_hours,
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(record)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as exc:
            _logger.error("shadow log write failed: %s: %s", type(exc).__name__, exc)
    except Exception as exc:
        _logger.error("log_shadow_cycle failed: %s: %s", type(exc).__name__, exc)
