#!/usr/bin/env python3
"""Transport-agnostic approval decision core (Phase-3).

Extracted from the retired Telegram ``approval_handler.py`` so that any
transport (Discord, Telegram, CLI) shares ONE decision path. Callers are
responsible for AUTHORIZATION *before* invoking these functions — the core
assumes the actor is already authorized and only enforces the
state/idempotency/audit invariants:

  * nonce -> queued item -> content_id resolution (+ nonce verify),
  * idempotency: an already-resolved (dequeued) item is a no-op,
  * WRITE-AHEAD: the decision line is appended to decisions.jsonl BEFORE any
    side effect,
  * ONLY ``decide(action="approve")`` writes memory/approved/ (the single
    human gate),
  * append-only audit; nothing here deletes a decision.

This module has NO transport imports and NO network — it is pure store I/O.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import contracts, store  # noqa: E402


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _resolve_by_nonce(nonce):
    """Return the pending queue item whose nonce matches, or None."""
    queue = store.read_queue() or {}
    for item in queue.get("pending", []):
        if item.get("nonce") == nonce:
            return item
    return None


def _variant_text(draft, variant_idx):
    variants = (draft or {}).get("variants") or []
    if 0 <= variant_idx < len(variants):
        return (variants[variant_idx].get("text") or "").strip()
    return ""


def _variant_score(draft, variant_idx):
    variants = (draft or {}).get("variants") or []
    if 0 <= variant_idx < len(variants):
        return variants[variant_idx].get("score")
    return None


def _set_status(content_id, status, decided_at):
    """Atomically set draft.status + approval.{decided_at,decision}."""
    def _mutate(d):
        new_d = dict(d)
        new_d["status"] = status
        approval = dict(d.get("approval") or {})
        approval["decided_at"] = decided_at
        approval["decision"] = status
        new_d["approval"] = approval
        return new_d
    return store.update_draft(content_id, _mutate)


# --------------------------------------------------------------------------
# the core decision
# --------------------------------------------------------------------------

def decide(nonce, variant_idx, action, *, decided_by, note=""):
    """Resolve an approve/reject/revise decision. Caller MUST pre-authorize.

    Returns a result dict: {"ok": bool, "action"/"reason": ..., "content_id": ...}.
    For approve/reject the decision is recorded here (write-ahead) and the item
    is dequeued. For revise the item is parked (status='revise',
    state='awaiting_revise_note') and the decision line is deferred to
    ``record_revise`` (which carries the note) — mirroring the original
    two-stage flow and avoiding a spurious empty 'revise' line.
    """
    item = _resolve_by_nonce(nonce)
    if item is None:
        # Already dequeued (resolved) — idempotent no-op.
        return {"ok": True, "reason": "already_resolved"}
    if item.get("nonce") != nonce:
        return {"ok": False, "reason": "bad_nonce"}

    content_id = item.get("content_id")

    pending = store.find_pending(content_id)
    if pending is None:
        return {"ok": True, "reason": "already_resolved"}
    if pending.get("nonce") != nonce:
        return {"ok": False, "reason": "bad_nonce"}

    if action not in ("approve", "reject", "revise"):
        return {"ok": False, "reason": "bad_action"}

    decided_at = contracts.now_iso()

    if action == "revise":
        # Park for the note (modal/reply). No decision line yet, no dequeue.
        _set_status(content_id, "revise", decided_at)

        def _await_note(it):
            new_it = dict(it)
            new_it["state"] = "awaiting_revise_note"
            return new_it

        target = store.find_pending(content_id)
        if target is not None:
            store.dequeue(content_id)
            store.enqueue(_await_note(target))
        return {"ok": True, "action": "revise", "content_id": content_id}

    # approve / reject — WRITE-AHEAD decision BEFORE any side effect.
    store.append_decision({
        "ts": decided_at,
        "content_id": content_id,
        "variant_idx": variant_idx,
        "action": action,
        "note": note,            # "" for approve/reject (validate_decision needs a str)
        "decided_by": decided_by,
    })

    draft = store.load_draft(content_id)

    if action == "approve":
        # THE SINGLE HUMAN GATE — only this branch writes memory/approved/.
        if not store.approved_exists(content_id):
            frontmatter = {
                "content_id": content_id,
                "approved_at": decided_at,
                "variant_idx": variant_idx,
                "score": _variant_score(draft, variant_idx),
            }
            store.write_approved(content_id, frontmatter, _variant_text(draft, variant_idx))
        _set_status(content_id, "approved", decided_at)
        store.dequeue(content_id)
        return {"ok": True, "action": "approve", "content_id": content_id}

    # action == "reject"
    _set_status(content_id, "rejected", decided_at)
    store.dequeue(content_id)
    return {"ok": True, "action": "reject", "content_id": content_id}


def record_revise(content_id, note, *, decided_by, variant_idx=0):
    """Record a revise note (from a Discord modal / reply). Caller pre-authorizes.

    Appends a 'revise' decision carrying the note, ensures status='revise'
    (HIGH-fix: set it here too so the draft never stays 'pending'), bumps
    revise_count/revise_note, and dequeues. MVP: record only — no auto re-CMO.
    """
    pending = store.find_pending(content_id)
    decided_at = contracts.now_iso()

    store.append_decision({
        "ts": decided_at,
        "content_id": content_id,
        "variant_idx": variant_idx,
        "action": "revise",
        "note": note or "",
        "decided_by": decided_by,
    })

    def _record(d):
        new_d = dict(d)
        new_d["status"] = "revise"
        approval = dict(d.get("approval") or {})
        approval["decided_at"] = decided_at
        approval["decision"] = "revise"
        approval["revise_note"] = note or ""
        approval["revise_count"] = (approval.get("revise_count") or 0) + 1
        new_d["approval"] = approval
        return new_d

    store.update_draft(content_id, _record)
    if pending is not None:
        store.dequeue(content_id)
    return {"ok": True, "action": "revise", "content_id": content_id, "note": note or ""}
