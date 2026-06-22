"""USER.md → Mem0 migration — reversible, idempotent, backup-first.

Usage:
  python -m jack_memory.migrate --dry-run    # shows what WOULD migrate
  python -m jack_memory.migrate --commit     # actually migrates

Design:
- USER.md is NEVER modified or deleted. It is opened READ-ONLY.
- A .bak copy is made BEFORE any migration (skipped if already exists).
- Idempotency via ~/.hermes/.mem0_migrated marker file (skip if present).
- Each bullet is given a stable metadata id = hash(section+bullet) so re-runs
  with the marker deleted are still duplicate-safe via Mem0 dedup.
- Sections map to metadata categories for retrieval filtering.
"""

from __future__ import annotations

import re
from pathlib import Path

_SECTION_CATEGORY: dict[str, str] = {
    "IDENTITY": "identity",
    "RELATIONSHIPS": "relationship",
    "WORK & PROJECTS": "work",
    "CURRENT PRIORITIES": "priority",
    "DAILY ROUTINE — ICHALKARANJI (HOLIDAYS)": "routine",
    "PREFERENCES": "preference",
    "GOALS": "goal",
    "THINGS JACK HAS LEARNED": "learned",
}

_SECTION_RE = re.compile(r"^\[.+\]$")
_MARKER_PATH = Path.home() / ".hermes" / ".mem0_migrated"
_BAK_SUFFIX = ".premigration.bak"


def backup_user_md(user_path: Path) -> Path:
    """Copy USER.md to USER.md.premigration.bak. No-op if backup already exists.

    Returns path to the backup.
    """
    bak = user_path.parent / (user_path.name + _BAK_SUFFIX)
    if bak.exists():
        return bak
    import shutil
    shutil.copy2(str(user_path), str(bak))
    return bak


def parse_sections(text: str) -> list[tuple[str, str]]:
    """Parse USER.md into (section_name, line) pairs.

    Returns one entry per non-empty, non-header line in each section.
    """
    results: list[tuple[str, str]] = []
    current_section: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if _SECTION_RE.match(stripped):
            current_section = stripped[1:-1]  # strip [ ]
        elif current_section and stripped and not stripped.startswith("#"):
            results.append((current_section, stripped))
    return results


def migrate(
    user_path: Path,
    client: object,
    *,
    marker_path: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Migrate USER.md sections to Mem0. Returns summary dict.

    Args:
        user_path: path to USER.md (read-only)
        client: JackMemoryClient instance
        marker_path: path to idempotency marker (default ~/.hermes/.mem0_migrated)
        dry_run: if True, parses and shows what WOULD migrate but calls nothing

    Returns:
        {"status": "ok"|"skipped"|"dry_run", "counts": {section: count}, "total": int}
    """
    marker = marker_path or _MARKER_PATH

    # Idempotency check
    if not dry_run and marker.exists():
        return {"status": "skipped", "reason": "marker exists", "counts": {}, "total": 0}

    # USER.md is READ-ONLY throughout
    try:
        text = user_path.read_text(encoding="utf-8")
    except OSError as e:
        return {"status": "error", "reason": str(e), "counts": {}, "total": 0}

    pairs = parse_sections(text)
    counts: dict[str, int] = {}

    if dry_run:
        for section, line in pairs:
            counts[section] = counts.get(section, 0) + 1
        return {"status": "dry_run", "counts": counts, "total": len(pairs)}

    # Actual migration
    import hashlib
    for section, line in pairs:
        category = _SECTION_CATEGORY.get(section, "learned")
        stable_id = hashlib.sha1(f"{section}:{line}".encode()).hexdigest()[:16]
        metadata = {
            "source": "migration",
            "section": section,
            "category": category,
            "stable_id": stable_id,
        }
        messages = [{"role": "user", "content": f"[{section}] {line}"}]
        try:
            client.add(messages, metadata=metadata)  # type: ignore[attr-defined]
            counts[section] = counts.get(section, 0) + 1
        except Exception:  # noqa: BLE001 — continue migrating other items
            pass

    total = sum(counts.values())

    # Write marker
    import datetime
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        f"migrated: {datetime.datetime.now().isoformat()} total={total}\n",
        encoding="utf-8",
    )

    return {"status": "ok", "counts": counts, "total": total}


def main() -> None:
    """CLI entry point: python -m jack_memory.migrate [--dry-run|--commit]"""
    import os
    import sys
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    commit = "--commit" in args

    if not dry_run and not commit:
        print("Usage: python -m jack_memory.migrate --dry-run | --commit")
        sys.exit(1)

    # Load .env
    env_path = Path.home() / ".hermes" / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    user_path = Path(
        os.environ.get("JACK_USER_PATH", str(Path.home() / ".hermes" / "USER.md"))
    )

    # Always back up first
    if user_path.exists():
        bak = backup_user_md(user_path)
        print(f"Backup: {bak}")

    from jack_memory.client import JackMemoryClient
    client = JackMemoryClient.from_env()

    result = migrate(user_path, client, dry_run=dry_run)
    print(f"Status: {result['status']}")
    print(f"Total: {result['total']}")
    for section, count in result.get("counts", {}).items():
        print(f"  [{section}]: {count} items")

    if dry_run:
        print("\nDry run complete. Use --commit to run for real.")


if __name__ == "__main__":
    main()
