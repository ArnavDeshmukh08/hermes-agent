"""Jack intent-router hook.

On `gateway:startup` (and `session:start` as a fallback) this wraps the live
Discord adapter's `_handle_message` so operational messages bypass the 29.6k-token
agent loop and dispatch straight to the worker. Everything lives in user space
(`~/.hermes/hooks/`) — ZERO edits to framework files, so a framework reinstall
can't silently wipe it (the hook reinstalls on next startup).

Flow for an operational message:
  @Jack find 100 psychiatry clinics in India
    → classify() → Route("lead", {...})
    → reply "on it · task <id>" via the LIVE bot (same channel)
    → background task runs the worker (bounded by a semaphore; blocking I/O via
      asyncio.to_thread so the gateway loop is never stalled)
    → completion summary posted via the same live bot to the same channel
  Agent loop is never invoked → no 413.

Unknown messages return None → the original _handle_message runs (normal agent).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
WORKER_ROOT = Path(os.environ.get("JACK_WORKER_ROOT", "/home/hermes/.hermes/jack_worker"))
for _p in (str(HOOK_DIR), str(WORKER_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import jack_intent_router as router  # noqa: E402 - after sys.path setup above

_PATCHED = False
_MENTION_RE = re.compile(r"<@[!&]?\d+>")
_LEAD_COLUMNS = ["Clinic Name", "City", "Phone", "Website"]
_SHEET_TAB = "Jack_Leads"
_PITCH_COL = "Personalized Pitch"

# Strong refs so fire-and-forget tasks aren't GC-cancelled mid-run.
_LIVE_TASKS: set = set()
_SEM = None  # lazy, created on the running loop


def _log(msg: str) -> None:
    print(f"[jack_router] {msg}", flush=True)


def _sem() -> asyncio.Semaphore:
    global _SEM
    if _SEM is None:
        _SEM = asyncio.Semaphore(int(os.environ.get("JACK_ROUTER_MAX_CONCURRENT", "2")))
    return _SEM


def _fire(coro) -> None:
    """Schedule a background task and hold a strong reference until it finishes."""
    t = asyncio.create_task(coro)
    _LIVE_TASKS.add(t)
    t.add_done_callback(_LIVE_TASKS.discard)


def _strip_mentions(text: str) -> str:
    return _MENTION_RE.sub(" ", text or "").strip()


def _find_adapter_class():
    """Locate the live DiscordAdapter class from sys.modules (imported by the
    framework at plugin load) so we patch the same class the gateway uses."""
    import inspect

    for name, mod in list(sys.modules.items()):
        if name.endswith("discord_platform.adapter") and inspect.isclass(getattr(mod, "DiscordAdapter", None)):
            return mod.DiscordAdapter
    return None


async def handle(event_type, context=None):  # noqa: ARG001 - framework hook signature
    """Idempotently install the router wrapper. Safe to call on every event."""
    global _PATCHED
    if _PATCHED:
        return
    cls = _find_adapter_class()
    if cls is None:
        # Adapter not imported yet — retry shortly and also on the next event.
        _log(f"DiscordAdapter not loaded at {event_type}; scheduling retry")
        try:
            asyncio.get_event_loop().call_later(3.0, lambda: _fire(handle("retry")))
        except Exception:  # noqa: BLE001
            pass
        return
    if getattr(cls, "_jack_orig_handle_message", None) is not None:
        _PATCHED = True
        return

    orig = cls._handle_message
    cls._jack_orig_handle_message = orig

    async def _routed_handle_message(self, message, *args, **kwargs):
        # Signature-agnostic: pass through whatever the framework calls with, so a
        # future signature change can't break the fall-through (conversational) path.
        try:
            text = _strip_mentions(getattr(message, "content", "") or "")
            route = router.classify(text)
            if route is not None:
                await _dispatch(self, message, route)
                return  # operational → worker path → agent loop bypassed
        except Exception as e:  # noqa: BLE001 - any router failure must degrade to the agent
            _log(f"router error (falling through to agent): {e!r}")
        return await orig(self, message, *args, **kwargs)

    cls._handle_message = _routed_handle_message
    _PATCHED = True
    _log(f"installed router on {cls.__name__}._handle_message")


def _task_id(message) -> str:
    return hashlib.sha1(str(getattr(message, "id", "")).encode()).hexdigest()[:10]


_CONVO = None


def _convo():
    """Lazily build the singleton conversation brain (reads SOUL.md/USER.md once)."""
    global _CONVO
    if _CONVO is None:
        _prepare_worker_env()
        from conversation import JackConversationHandler

        _CONVO = JackConversationHandler()
    return _CONVO


async def _run_conversation(message, route) -> None:
    """Answer a conversational turn with Jack's lean brain — bypasses the agent
    loop entirely (no 413). Blocking LLM call runs off-thread inside respond()."""
    channel = message.channel
    try:
        author = getattr(message, "author", None)
        user_id = str(getattr(author, "id", "") or "anon")
        text = route.params.get("text") or _strip_mentions(getattr(message, "content", "") or "")
        async with _sem():
            reply = await _convo().respond(text, user_id)
        await channel.send(reply)
    except Exception as e:  # noqa: BLE001 - never crash the gateway on a chat turn
        await channel.send("⚠️ I glitched on that one — say it again?")
        _log(f"conversation failed: {e!r}")


async def _dispatch(adapter, message, route) -> None:
    channel = message.channel
    if route.intent == "status":
        await channel.send(_status_text())
        return
    if route.intent == "conversational":
        _fire(_run_conversation(message, route))
        return
    if route.intent == "reminder":
        await channel.send(
            "⏰ Got it — but full reminder scheduling isn't wired up yet (coming soon). "
            "I can't set it automatically right now."
        )
        return
    if route.intent == "lead":
        p = route.params
        tid = _task_id(message)
        loc = f" in {p['location']}" if p.get("location") else ""
        await channel.send(
            f"🚀 On it — task `{tid}`: finding {p['count']} {p['target']}{loc}. "
            f"Routing to the worker (no agent loop). I'll post the sheet here when done."
        )
        _fire(_run_lead(message, route, tid))
        return
    if route.intent == "outreach":
        row = route.params.get("row")
        tid = _task_id(message)
        if not row:
            await channel.send("Which row? e.g. `generate outreach for row 12`.")
            return
        await channel.send(f"✍️ On it — task `{tid}`: drafting outreach for row {row} (draft-only, no send).")
        _fire(_run_outreach(message, row, tid))


def _prepare_worker_env() -> None:
    os.environ.setdefault("HERMES_TASKS_ROOT", str(WORKER_ROOT / "tasks"))
    os.environ.setdefault("HERMES_OUT_DIR", str(WORKER_ROOT / "out"))
    if not os.environ.get("HERMES_GOOGLE_CREDS"):
        os.environ["HERMES_GOOGLE_CREDS"] = "/home/hermes/.hermes/credentials.json"
    os.environ.setdefault("HERMES_LLM_CONCURRENCY", "2")
    os.environ.setdefault("HERMES_CONCURRENCY", "4")
    if os.environ.get("VYTAL_SHEET_ID") and not os.environ.get("HERMES_SHEET_ID"):
        os.environ["HERMES_SHEET_ID"] = os.environ["VYTAL_SHEET_ID"]


def _status_text() -> str:
    try:
        _prepare_worker_env()
        from worker.queue import TaskQueue

        c = TaskQueue().counts()
        return (
            f"📊 Worker queue — pending **{c['pending']}** · running **{c['running']}** · "
            f"completed **{c['completed']}** · failed **{c['failed']}**"
        )
    except Exception as e:  # noqa: BLE001
        return f"📊 status unavailable: {type(e).__name__}"


async def _run_lead(message, route, task_id: str) -> None:
    channel = message.channel
    try:
        _prepare_worker_env()
        from worker.prime import HermesPrime
        from worker.specs import load_spec

        p = route.params
        spec = load_spec(
            {
                "target": p["target"],
                "location": p.get("location"),
                "columns": _LEAD_COLUMNS,
                "count": p["count"],
                "outreach": False,
                "sheet_tab": _SHEET_TAB,
            }
        )
        async with _sem():  # bound concurrent heavy runs in the shared gateway process
            result = await HermesPrime(notify=False).run_parallel(spec)
        c = result["counts"]
        sheet = result["sheet"]
        where = sheet.get("path") or f"{sheet.get('backend')}:{sheet.get('tab')}"
        await channel.send(
            f"✅ Task `{task_id}` complete — **{c['written']}** leads → tab `{sheet.get('tab')}` "
            f"({c['with_provenance']}/{c['written']} sourced), {result['runtime_s']:.1f}s · {where}"
        )
    except Exception as e:  # noqa: BLE001 - report failures honestly, never crash the gateway
        await channel.send(f"⚠️ Task `{task_id}` failed: {type(e).__name__}: {str(e)[:180]}")
        _log(f"lead task {task_id} failed: {e!r}")


# -- outreach: blocking gspread runs in threads; LLM draft runs on the loop ----
def _read_row(creds: str, sid: str, row: int) -> dict:
    import gspread

    ws = gspread.service_account(filename=creds).open_by_key(sid).worksheet(_SHEET_TAB)
    values = ws.get_all_values()
    # gspread and values[] share numbering: row 1 (values[0]) is the header,
    # row 2 (values[1]) is the first data row.
    if row < 2 or row > len(values):
        return {"error": f"row {row} not in `{_SHEET_TAB}` (it has {max(0, len(values) - 1)} data row(s))."}
    header = values[0]
    return {"header": header, "lead": dict(zip(header, values[row - 1]))}


def _write_pitch(creds: str, sid: str, row: int, header: list, pitch: str) -> None:
    import gspread

    ws = gspread.service_account(filename=creds).open_by_key(sid).worksheet(_SHEET_TAB)
    if _PITCH_COL in header:
        col = header.index(_PITCH_COL) + 1
    else:
        col = len(header) + 1
        ws.update_cell(1, col, _PITCH_COL, value_input_option="RAW")
    ws.update_cell(row, col, pitch, value_input_option="RAW")  # RAW: no formula injection


async def _run_outreach(message, row: int, task_id: str) -> None:
    """Draft a pitch for an existing sheet row (reuses worker draft_pitch). Draft-only."""
    channel = message.channel
    try:
        _prepare_worker_env()
        creds = os.environ.get("HERMES_GOOGLE_CREDS")
        sid = os.environ.get("HERMES_SHEET_ID") or os.environ.get("VYTAL_SHEET_ID")
        if not creds or not sid:
            await channel.send(f"⚠️ Task `{task_id}` — missing config (sheet id / creds not set).")
            return
        from worker import leadgen

        async with _sem():
            read = await asyncio.to_thread(_read_row, creds, sid, row)
            if read.get("error"):
                await channel.send(f"⚠️ Task `{task_id}` — {read['error']}")
                return
            pitch = await leadgen.draft_pitch(read["lead"])
            if not pitch:
                await channel.send(f"⚠️ Task `{task_id}` — couldn't draft a pitch for row {row}.")
                return
            await asyncio.to_thread(_write_pitch, creds, sid, row, read["header"], pitch)
        header = read["header"]
        who = read["lead"].get("Clinic Name") or (read["lead"].get(header[0]) if header else None) or "row"
        await channel.send(
            f"✅ Task `{task_id}` — drafted outreach for row {row} ({who}) → `{_PITCH_COL}` "
            f"(PENDING REVIEW, no send). Preview: {pitch[:140]}"
        )
    except Exception as e:  # noqa: BLE001
        await channel.send(f"⚠️ Outreach task `{task_id}` failed: {type(e).__name__}: {str(e)[:160]}")
        _log(f"outreach {task_id} failed: {e!r}")
