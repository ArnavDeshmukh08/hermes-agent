"""Goal — first-class durable goal object for Jack's goal-tracking system.

Frozen dataclass (immutability rule): mutations happen in GoalStore by
building a new dict and re-persisting, never by mutating a Goal in place.
type and metric are OPEN enums — free text is accepted; the constants below
are the canonical values the parser/reasoner understand.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Canonical (but open) vocabularies.
GOAL_TYPES = ("fitness", "financial", "project", "habit", "other")
METRICS = ("garmin_steps", "garmin_runs", "calendar", "manual", "none")
STATUSES = ("active", "paused", "done")

_GARMIN_METRICS = frozenset({"garmin_steps", "garmin_runs"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Goal:
    id: str
    title: str
    type: str                       # noqa: A003 — domain field name
    target: str
    plan: str
    metric: str
    created_at: str
    status: str = "active"
    deadline: str | None = None
    last_checked: str | None = None
    progress_notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_garmin(self) -> bool:
        return self.metric in _GARMIN_METRICS

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict. progress_notes stored as list."""
        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "target": self.target,
            "plan": self.plan,
            "metric": self.metric,
            "created_at": self.created_at,
            "status": self.status,
            "deadline": self.deadline,
            "last_checked": self.last_checked,
            "progress_notes": list(self.progress_notes),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Goal":
        """Build a Goal from a stored dict, tolerating missing optional keys
        and old shapes (progress_notes stored as list -> coerced to tuple)."""
        return cls(
            id=str(d.get("id", "")),
            title=str(d.get("title", "")),
            type=str(d.get("type", "other")),
            target=str(d.get("target", "")),
            plan=str(d.get("plan", "")),
            metric=str(d.get("metric", "none")),
            created_at=str(d.get("created_at", "")) or _now_iso(),
            status=str(d.get("status", "active")),
            deadline=d.get("deadline"),
            last_checked=d.get("last_checked"),
            progress_notes=tuple(d.get("progress_notes") or ()),
        )


def new_goal(
    *,
    title: str,
    type: str,                      # noqa: A002
    target: str,
    plan: str,
    metric: str,
    deadline: str | None = None,
) -> Goal:
    """Factory for a fresh active goal with a generated 8-char id."""
    return Goal(
        id=uuid.uuid4().hex[:8],
        title=(title or "untitled goal").strip(),
        type=(type or "other").strip().lower(),
        target=(target or "").strip(),
        plan=(plan or "").strip(),
        metric=(metric or "none").strip().lower(),
        deadline=(deadline.strip() if isinstance(deadline, str) and deadline.strip() else None),
        created_at=_now_iso(),
        status="active",
        last_checked=None,
        progress_notes=(),
    )
