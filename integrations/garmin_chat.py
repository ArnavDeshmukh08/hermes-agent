"""Garmin data formatter for chat — formats health data into Jack's voice for chat replies."""

from __future__ import annotations


def format_sleep(sleep: dict | None) -> str:
    """Format a sleep dict into a single human-readable line.

    Example: "Sleep: 7.2h total, 1.8h deep, 1.4h REM (score 74)"
    Returns "" if sleep is None.
    """
    if sleep is None:
        return ""
    try:
        total = sleep["total_sleep_h"]
        deep = sleep["deep_h"]
        rem = sleep["rem_h"]
        score = sleep.get("score")

        base = f"Sleep: {total}h total, {deep}h deep, {rem}h REM"
        if score is not None:
            base += f" (score {score})"
        return base
    except Exception:
        return ""


def format_stats(stats: dict | None) -> list[str]:
    """Format a stats dict into a list of display lines.

    Example: ["Steps: 8,432", "Body battery peak: 82", "Active calories: 312", "Avg stress: 28"]
    Returns [] if stats is None.
    """
    if stats is None:
        return []
    lines: list[str] = []
    try:
        steps = stats.get("steps")
        if steps is not None:
            lines.append(f"Steps: {steps:,}")

        bb = stats.get("body_battery_high")
        if bb is not None:
            lines.append(f"Body battery peak: {bb}")

        calories = stats.get("calories")
        if calories is not None:
            lines.append(f"Active calories: {calories:,}")

        stress = stats.get("stress_avg")
        if stress is not None:
            lines.append(f"Avg stress: {stress}")
    except Exception:
        pass
    return lines


def format_garmin_for_chat(summary: dict | None) -> str:
    """Format a get_daily_summary() result into a chat-ready string.

    Returns "" if summary is None (service unreachable).
    Otherwise joins sleep + stats lines into a single block.
    """
    if summary is None:
        return ""
    try:
        parts: list[str] = []

        sleep_line = format_sleep(summary.get("sleep"))
        if sleep_line:
            parts.append(sleep_line)

        stat_lines = format_stats(summary.get("stats"))
        parts.extend(stat_lines)

        return "\n".join(parts)
    except Exception:
        return ""
