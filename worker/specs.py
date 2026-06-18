"""Lead task spec — dynamic, config-driven (LEAD-ENGINE §1).

A spec is plain JSON the user supplies. New lead-finding tasks require *no code
change*: just different `target`/`location`/`columns`. We accept both the
mission's `columns` key and LEAD-ENGINE's `fields` alias.

    {"target": "psychiatry clinics", "location": "India",
     "columns": ["name", "clinic", "phone", "email", "website"]}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# Defaults applied inline (no normalization layer — Devil's Advocate cut it).
DEFAULT_COLUMNS = ["name", "clinic", "address", "phone", "email", "website"]
DEFAULT_SOURCES = ["practo", "generic_web"]
DEFAULT_COUNT = 12
MAX_COUNT = 100  # hard ceiling: never let a spec fan out unbounded


class SpecError(ValueError):
    """Raised when a spec is structurally invalid (e.g. empty target)."""


@dataclass(frozen=True)
class LeadSpec:
    """Validated, immutable lead task spec."""

    target: str
    location: str | None = None
    columns: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_COLUMNS))
    sources: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_SOURCES))
    count: int = DEFAULT_COUNT
    outreach: bool = False
    sheet_tab: str | None = None

    @property
    def tab(self) -> str:
        return self.sheet_tab or slug(self.target, self.location)


# Map free-form column labels to the roles the workers know how to enrich, so a
# user can write "Clinic Name"/"Personalized Pitch"/"Instagram" and the right
# worker still fills it — the dynamic-columns promise, no code change per label.
_ROLE_SYNONYMS = {
    "name": {"name", "doctor", "doctor name", "contact name", "owner"},
    "clinic": {"clinic", "clinic name", "practice", "hospital", "business", "company", "org"},
    "city": {"city", "location", "town", "area"},
    "address": {"address", "addr"},
    "phone": {"phone", "mobile", "contact number", "number", "tel", "telephone"},
    "email": {"email", "e-mail", "mail", "email address"},
    "website": {"website", "web", "site", "url", "web site"},
    "instagram": {"instagram", "insta", "ig", "social", "handle", "instagram handle"},
    "pitch": {"pitch", "personalized pitch", "personalised pitch", "cold email",
              "email draft", "message", "outreach", "draft"},
}


def column_role(label) -> str | None:
    """Resolve a human column label to a canonical role (or None)."""
    key = " ".join(str(label).strip().lower().split())
    for role, syns in _ROLE_SYNONYMS.items():
        if key in syns:
            return role
    return None


def role_map(columns) -> dict:
    """{role: actual_label} for the first column matching each role."""
    out: dict = {}
    for col in columns:
        role = column_role(col)
        if role and role not in out:
            out[role] = col
    return out


def slug(*parts: str | None) -> str:
    """Stable, filesystem/tab-safe slug from spec parts."""
    raw = "_".join(p for p in parts if p)
    out = "".join(c if c.isalnum() else "_" for c in raw.lower())
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_") or "leads"


def _clean_columns(value) -> list[str]:
    """Coerce a columns/fields value to a clean ordered list of non-empty strings."""
    if not value:
        return list(DEFAULT_COLUMNS)
    cols, seen = [], set()
    for c in value:
        name = str(c).strip()
        if name and name not in seen:
            seen.add(name)
            cols.append(name)
    return cols or list(DEFAULT_COLUMNS)


def load_spec(raw: dict) -> LeadSpec:
    """Build a validated LeadSpec from a raw dict. Refuse on empty target."""
    if not isinstance(raw, dict):
        raise SpecError("spec must be a JSON object")

    target = str(raw.get("target") or "").strip()
    if not target:
        raise SpecError("spec.target is required and must be non-empty")

    location = raw.get("location")
    location = str(location).strip() if location else None

    # `columns` (mission) or `fields` (LEAD-ENGINE) — columns wins if both present.
    columns = _clean_columns(raw.get("columns") or raw.get("fields"))

    sources = raw.get("sources") or DEFAULT_SOURCES
    sources = [str(s).strip() for s in sources if str(s).strip()] or list(DEFAULT_SOURCES)

    count = raw.get("count", DEFAULT_COUNT)
    try:
        count = max(1, min(MAX_COUNT, int(count)))
    except (TypeError, ValueError):
        count = DEFAULT_COUNT

    # Route through slug() so a crafted tab (e.g. "../../secrets/x") can never
    # escape the output dir — slug() reduces it to safe [a-z0-9_].
    sheet_tab = raw.get("sheet_tab")
    sheet_tab = slug(str(sheet_tab)) if sheet_tab else None

    return LeadSpec(
        target=target,
        location=location,
        columns=tuple(columns),
        sources=tuple(sources),
        count=count,
        outreach=bool(raw.get("outreach", False)),
        sheet_tab=sheet_tab,
    )


def load_spec_json(text: str) -> LeadSpec:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise SpecError(f"spec is not valid JSON: {e}") from e
    return load_spec(raw)
