"""Shared hermetic-test scaffolding for the Hermes Phase-2 content pipeline.

Stream E — Test Harness. Everything here is offline and deterministic:

* a fresh temp memory root per test (``HERMES_MEMORY_ROOT`` + ``HERMES_MEMORY_DIR``
  are both set so we work whether a stream read the blueprint env name or the
  memory-layer-spec env name),
* the LLM forced into mock mode (``HERMES_LLM_MOCK=1``),
* Discord forced into dry-run mode (``HERMES_DISCORD_DRYRUN=1``),
* the allowlist pinned to user ``42`` and the approval channel to ``777``.

Repo root is placed on ``sys.path`` so ``import lib.*`` and the ``bin/*`` scripts
resolve. The bin scripts are driven through an importable ``main()`` when one
exists (preferred); a subprocess fallback is provided for scripts without one.

These helpers intentionally talk only to the *pinned* API surface described in
Stream E's brief. Where a needed symbol is not yet provided by the parallel
streams, the calling test degrades gracefully (skips) rather than inventing API.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Repo root = parent of the ``tests`` directory.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Source seeds that ship in the repo's own memory tree.
REPO_MEMORY = REPO_ROOT / "memory"
SEED_SOURCES = REPO_MEMORY / "research" / "_sources.json"
SEED_STYLE = REPO_MEMORY / "voice" / "style.md"
SEED_SAMPLES = REPO_MEMORY / "voice" / "samples.md"

ALLOWED_USER_ID = 42
APPROVAL_CHANNEL_ID = 777
WRONG_CHANNEL_ID = 555
UNAUTHORIZED_USER_ID = 999

_ENV_KEYS = (
    "HERMES_MEMORY_ROOT", "HERMES_MEMORY_DIR", "HERMES_LLM_MOCK",
    "HERMES_DISCORD_DRYRUN", "DISCORD_ALLOWED_USERS", "DISCORD_APPROVAL_CHANNEL_ID",
)


def _set_env(memory_root: Path) -> dict:
    """Build the hermetic environment dict and apply it to ``os.environ``."""
    env = {
        "HERMES_MEMORY_ROOT": str(memory_root),
        "HERMES_MEMORY_DIR": str(memory_root),
        "HERMES_LLM_MOCK": "1",
        "HERMES_DISCORD_DRYRUN": "1",
        "DISCORD_ALLOWED_USERS": str(ALLOWED_USER_ID),
        "DISCORD_APPROVAL_CHANNEL_ID": str(APPROVAL_CHANNEL_ID),
    }
    os.environ.update(env)
    return env


def _seed_memory_tree(memory_root: Path) -> None:
    """Create the memory folder tree and drop in the four seed files.

    Prefers ``store.ensure_tree()`` if the store module exposes it; otherwise
    builds the directories directly. Either way the seeds are copied/created so
    every stage has the files it expects.
    """
    # Try the real ensure_tree first so tests exercise the production path when
    # it exists. Fall back to manual mkdir if the symbol is not yet built.
    ensured = False
    try:  # pragma: no cover - depends on parallel stream availability
        store = importlib.import_module("lib.store")
        ensure = getattr(store, "ensure_tree", None)
        if callable(ensure):
            # ensure_tree may take the root or read it from env; support both.
            try:
                ensure(memory_root)
            except TypeError:
                ensure()
            ensured = True
    except Exception:
        ensured = False

    if not ensured:
        for sub in ("research", "content", "approved", "voice", "approvals"):
            (memory_root / sub).mkdir(parents=True, exist_ok=True)

    # Always (re)assert the dirs exist even if ensure_tree built a subset.
    for sub in ("research", "content", "approved", "voice", "approvals"):
        (memory_root / sub).mkdir(parents=True, exist_ok=True)

    # Seed files — copy the repo's canonical seeds when present.
    if SEED_SOURCES.exists():
        shutil.copy2(SEED_SOURCES, memory_root / "research" / "_sources.json")
    if SEED_STYLE.exists():
        shutil.copy2(SEED_STYLE, memory_root / "voice" / "style.md")
    if SEED_SAMPLES.exists():
        shutil.copy2(SEED_SAMPLES, memory_root / "voice" / "samples.md")

    # Approvals seeds: empty queue + empty append-only ledger.
    queue = memory_root / "approvals" / "queue.json"
    queue.write_text(
        json.dumps({"updated_at": None, "pending": []}), encoding="utf-8"
    )
    decisions = memory_root / "approvals" / "decisions.jsonl"
    decisions.write_text("", encoding="utf-8")


class HermesTestCase(unittest.TestCase):
    """Base class giving every test a fresh, hermetic temp memory root."""

    def setUp(self) -> None:
        self._saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}
        self._tmp = Path(tempfile.mkdtemp(prefix="hermes-test-"))
        self.memory_root = self._tmp / "memory"
        self.memory_root.mkdir(parents=True, exist_ok=True)
        self.env = _set_env(self.memory_root)
        _seed_memory_tree(self.memory_root)

    def tearDown(self) -> None:
        # Restore prior env values (or remove keys that were not set before).
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ---- path helpers -------------------------------------------------

    def research_dir(self) -> Path:
        return self.memory_root / "research"

    def content_dir(self) -> Path:
        return self.memory_root / "content"

    def approved_dir(self) -> Path:
        return self.memory_root / "approved"

    def approvals_dir(self) -> Path:
        return self.memory_root / "approvals"

    def queue_path(self) -> Path:
        return self.approvals_dir() / "queue.json"

    def decisions_path(self) -> Path:
        return self.approvals_dir() / "decisions.jsonl"

    # ---- json helpers -------------------------------------------------

    def read_json(self, path: Path) -> dict:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def read_decisions(self) -> list:
        text = self.decisions_path().read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def read_queue(self) -> dict:
        return self.read_json(self.queue_path())

    # ---- bin runner ---------------------------------------------------

    def run_bin(self, module_name: str, argv=None):
        """Run a ``bin/<module>.py`` stage with the hermetic env.

        Preference order:
          1. import ``bin.<module>`` and call ``main(argv)`` / ``main()`` — runs
             in-process so the temp env is honoured.
          2. fall back to ``subprocess`` with the same env.

        Returns a tuple ``(mode, result)`` where mode is "import" (result is the
        main() return value, normalised to int 0 on None) or "subprocess"
        (result is a CompletedProcess). Raises ``unittest.SkipTest`` if the
        module is entirely absent.
        """
        argv = list(argv or [])
        mod = None
        try:
            mod = importlib.import_module(f"bin.{module_name}")
            importlib.reload(mod)
        except Exception:
            mod = None

        main = getattr(mod, "main", None) if mod is not None else None
        if callable(main):
            try:
                rv = main(argv)
            except TypeError:
                # main() that takes no args; emulate argv via sys.argv.
                saved = sys.argv
                sys.argv = [f"bin/{module_name}.py", *argv]
                try:
                    rv = main()
                finally:
                    sys.argv = saved
            return "import", (0 if rv is None else rv)

        # Subprocess fallback.
        script = REPO_ROOT / "bin" / f"{module_name}.py"
        if not script.exists():
            raise unittest.SkipTest(
                f"bin/{module_name}.py not present and bin.{module_name}.main "
                "not importable; stage not built yet."
            )
        proc_env = os.environ.copy()
        result = subprocess.run(
            [sys.executable, str(script), *argv],
            env=proc_env,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        return "subprocess", result


# ---- fixture builders (used by cmo/approval tests when upstream stages
#      are not available) ------------------------------------------------


def make_finding(run_id: str, n: int, *, topic: str, source_url: str,
                 ftype: str = "trend", consumed: bool = False) -> dict:
    """Build a blueprint-shape Finding object (id = ``<run_id>#<n>``)."""
    return {
        "id": f"{run_id}#{n}",
        "run_date": run_id.split("_", 1)[0][:4] + "-"
        + run_id.split("_", 1)[0][4:6] + "-"
        + run_id.split("_", 1)[0][6:8],
        "type": ftype,
        "topic": topic,
        "summary": f"Finding {n}: clinics lose patients to no-shows in {topic}.",
        "source_url": source_url,
        "raw_excerpt": "Around 22% of clinic slots go to no-shows; most recoverable.",
        "tags": ["no-shows", "retention", "whatsapp"],
        "consumed": consumed,
    }


def make_research_run(run_id: str = "20260616_clinic-no-shows",
                      topic: str = "clinic no-shows", n_findings: int = 2) -> dict:
    """Build a blueprint-shape research run wrapper with ``n_findings``."""
    run_date = f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"
    findings = [
        make_finding(
            run_id, i + 1, topic=topic,
            source_url=f"https://example.org/clinic-study-{i + 1}",
            ftype="trend" if i % 2 == 0 else "idea",
        )
        for i in range(n_findings)
    ]
    return {
        "run_id": run_id,
        "run_date": run_date,
        "topic": topic,
        "findings": findings,
    }


def make_pending_draft(content_id: str = "20260616-0905-noshow-cost",
                       source_ids=None, n_variants: int = 2) -> dict:
    """Build a blueprint-shape Content draft (status pending, N variants)."""
    if source_ids is None:
        source_ids = ["20260616_clinic-no-shows#1"]
    variants = []
    for i in range(n_variants):
        variants.append({
            "idx": i,
            "text": (
                f"Variant {i}: Most clinics lose 1 in 4 patients to no-shows. "
                "One timed WhatsApp nudge recovered them. What's your lever?"
            ),
            "angle": "stat-led hook" if i == 0 else "how-to",
            "score": round(0.85 - 0.1 * i, 2),
            "score_breakdown": {
                "hook": 0.9 - 0.1 * i,
                "clarity": 0.85,
                "voice_match": 0.8,
                "cta": 0.8,
            },
        })
    return {
        "id": content_id,
        "created_at": "2026-06-16T09:05:00+05:30",
        "source_research_ids": list(source_ids),
        "persona": "jack",
        "platform": "linkedin",
        "variants": variants,
        "status": "pending",
        "approval": {
            "transport": None,
            "message_id": None,
            "channel_id": None,
            "nonce": None,
            "dispatched_at": None,
            "decided_at": None,
            "decision": None,
            "revise_count": 0,
            "revise_note": None,
        },
    }


def write_research_fixture(memory_root: Path, run: dict | None = None) -> Path:
    """Write a research run wrapper into the temp tree, return its path."""
    if run is None:
        run = make_research_run()
    path = memory_root / "research" / f"{run['run_id']}.json"
    path.write_text(json.dumps(run, indent=2), encoding="utf-8")
    return path


def write_draft_fixture(memory_root: Path, draft: dict | None = None) -> Path:
    """Write a content draft into the temp tree, return its path."""
    if draft is None:
        draft = make_pending_draft()
    path = memory_root / "content" / f"{draft['id']}.json"
    path.write_text(json.dumps(draft, indent=2), encoding="utf-8")
    return path


def interaction(nonce: str, variant: int, action: str, *,
                user_id: int = ALLOWED_USER_ID,
                channel_id: int = APPROVAL_CHANNEL_ID) -> dict:
    """Build a Discord button-interaction dict for ``handle_interaction``.

    Shape:
        {"data": {"custom_id": "apr:<nonce>:<variant>:<action>"},
         "user": {"id": <user>},
         "channel_id": <channel>}
    ``action`` is the short form a|r|v.
    """
    return {
        "data": {"custom_id": f"apr:{nonce}:{variant}:{action}"},
        "user": {"id": user_id},
        "channel_id": channel_id,
    }


def import_optional(module_name: str):
    """Import a module, returning None if it is not yet available."""
    try:
        return importlib.import_module(module_name)
    except Exception:
        return None
