"""ProactiveItem dataclass and PriorityScorer — ranking and rendering logic.

Kept separate from engine.py so the scoring rules can be tested in isolation
without any I/O, network, or file-system dependencies.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Priority constants
# ---------------------------------------------------------------------------

P1 = "P1"
P2 = "P2"
P3 = "P3"

SCORE_P1 = 100
SCORE_P2 = 50
SCORE_P3 = 10

MAX_P2_PER_CYCLE = 2
MAX_P3_PER_CYCLE = 1

TONE_PREFIX: dict[str, str] = {
    P1: "🚨 Hey Arnav — {}",
    P2: "💬 Hey — {}",
    P3: "💡 {}",
}


# ---------------------------------------------------------------------------
# ProactiveItem
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProactiveItem:
    """A single proactive nudge candidate."""

    nudge_type: str   # stable dedup key, e.g. 'deadline_24h', 'siddhi_silence'
    priority: str     # 'P1' | 'P2' | 'P3'
    score: int        # 100 | 50 | 10
    message: str      # body WITHOUT tone prefix
    alone: bool       # True only for P1 items
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# PriorityScorer
# ---------------------------------------------------------------------------


class PriorityScorer:
    """Select and render a cycle's worth of proactive nudges.

    Selection rules (evaluated in priority order):
    - P1 present → send only the highest-scoring P1; drop all others.
    - P2 present (no P1) → send up to MAX_P2_PER_CYCLE items.
    - P3 present (no P1/P2) → send up to MAX_P3_PER_CYCLE items.
    - Daily cap enforced via `sent_today` parameter.
    """

    def __init__(self, max_per_day: int | None = None) -> None:
        if max_per_day is not None:
            self._max = max_per_day
        else:
            self._max = int(os.environ.get("JACK_PROACTIVE_MAX_PER_DAY", "3"))

    def select(
        self,
        items: list[ProactiveItem],
        *,
        sent_today: int = 0,
    ) -> list[ProactiveItem]:
        """Return the subset of *items* that should be sent this cycle.

        Args:
            items: All candidate nudges gathered by the engine.
            sent_today: Number of proactive messages already sent today (IST).

        Returns:
            An ordered list of ProactiveItems to send (may be empty).
        """
        if not items:
            return []

        remaining = max(0, self._max - max(0, sent_today))
        if remaining <= 0:
            return []

        # Partition by priority; within each tier sort by score DESC, nudge_type ASC.
        def _sort_key(it: ProactiveItem) -> tuple[int, str]:
            return (-it.score, it.nudge_type)

        p1 = sorted([i for i in items if i.priority == P1], key=_sort_key)
        p2 = sorted([i for i in items if i.priority == P2], key=_sort_key)
        p3 = sorted([i for i in items if i.priority == P3], key=_sort_key)

        if p1:
            return [p1[0]]
        if p2:
            candidates = p2[: min(MAX_P2_PER_CYCLE, remaining)]
            for item in candidates:
                if item.alone:
                    return [item]
            return candidates
        if p3:
            candidates = p3[: min(MAX_P3_PER_CYCLE, remaining)]
            for item in candidates:
                if item.alone:
                    return [item]
            return candidates
        return []

    @staticmethod
    def render(item: ProactiveItem) -> str:
        """Apply the tone prefix for *item*'s priority to its message.

        Unknown priority → return the message unchanged.
        """
        template = TONE_PREFIX.get(item.priority)
        if template is None:
            return item.message
        return template.format(item.message)
