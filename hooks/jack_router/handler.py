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
HERMES_ROOT = HOOK_DIR.parents[1]  # ~/.hermes on the box (repo root locally) — for `import reminders`
for _p in (str(HOOK_DIR), str(WORKER_ROOT), str(HERMES_ROOT)):
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


_STORE = None


def _store():
    """Lazy singleton ReminderStore (shares the JSON file with the scheduler service)."""
    global _STORE
    if _STORE is None:
        from reminders.store import ReminderStore

        _STORE = ReminderStore()
    return _STORE


def _fmt_ist(value) -> str:
    """Render a stored UTC time as friendly IST, e.g. '9:00 AM IST, Thu Jun 19'."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%-I:%M %p IST, %a %b %-d")


# Leading conversational filler ("yeah ok so remind me ...") — repeatable, case-insensitive.
_FILLER_RE = re.compile(
    r"^(?:\s*(?:yeah|yea|ok|okay|sure|alright|hey|so|yo|please)\b[\s,]*)+",
    re.IGNORECASE,
)
# Command prefixes, most-specific first so the time-embedded forms ("remind me at 11am that")
# win before the bare "remind me " catch-all.
_REMINDER_TRIGGER_RE = re.compile(
    r"^\s*(?:"
    r"remind me\s+at\b.*?\b(?:that|to)\s+|"  # remind me at <time> that/to
    r"remind me\s+(?:to|about)\s+|remind me\s+|"
    r"reminder\s+(?:for|to|about)\s+|"
    r"set(?:\s+up)?\s+an?\s+(?:alarm|reminder)\s+(?:for|to|about)\s+|"
    r"don'?t let me forget\s+(?:(?:to|that|about)\s+)?|"
    r"alert me\s+(?:to|when|about|that|if)\s+"
    r")",
    re.IGNORECASE,
)
_TIME_TAIL_RE = re.compile(
    r"\s+(?:at|by|on|in|every|this|tonight|tomorrow|today|next)\b.*$",
    re.IGNORECASE,
)


def _reminder_message(text: str) -> str:
    """Extract the task: 'remind me to call mom at 9am tomorrow' -> 'call mom'.

    Strip order: leading filler -> command prefix (time-embedded form first) -> trailing
    time clause. Falls back to the original stripped text if everything got stripped.
    """
    original = text.strip()
    t = _FILLER_RE.sub("", original)
    t = _REMINDER_TRIGGER_RE.sub("", t)
    t = _TIME_TAIL_RE.sub("", t).strip(" .,!?")
    return t or original


def _match_reminder(text: str, items: list):
    """Pick the pending reminder a cancel request refers to (word overlap)."""
    t = text.lower()
    best, score = None, 0
    for r in items:
        words = [w for w in re.findall(r"\w+", r["message"].lower()) if len(w) > 2]
        s = sum(1 for w in words if w in t)
        if s > score:
            best, score = r, s
    if best:
        return best
    return items[0] if len(items) == 1 else None


async def _run_reminder(message, route) -> None:
    """Set / list / cancel reminders against the live store — no agent loop."""
    channel = message.channel
    action = route.params.get("action", "set")
    text = route.params.get("text") or _strip_mentions(getattr(message, "content", "") or "")
    user_id = str(getattr(getattr(message, "author", None), "id", "") or "anon")
    try:
        if action == "list":
            items = await asyncio.to_thread(_store().list_pending, user_id)
            if not items:
                await channel.send("📭 No reminders set.")
                return
            lines = [
                f"• {r['message']} — {_fmt_ist(r['fire_at'])}" + (" · recurring" if r.get("recurring") else "")
                for r in items
            ]
            await channel.send("⏰ Your reminders:\n" + "\n".join(lines))
            return
        if action == "cancel":
            items = await asyncio.to_thread(_store().list_pending, user_id)
            target = _match_reminder(text, items)
            if not target:
                await channel.send("Which one? Try `what reminders do I have`, then `cancel the <name> reminder`.")
                return
            ok = await asyncio.to_thread(_store().cancel, target["id"], user_id)
            await channel.send(f"🗑️ Cancelled: {target['message']}" if ok else "Couldn't cancel that one.")
            return
        # set
        from reminders import parser as rparser

        msg = _reminder_message(text)
        try:
            parsed = await asyncio.to_thread(rparser.parse_time, text)
        except ValueError as e:
            await channel.send(f"⏰ {e}")
            return
        await asyncio.to_thread(_store().add, user_id, msg, parsed.fire_at, parsed.recurring)
        suffix = " · recurring" if parsed.recurring else ""
        await channel.send(f"Got it ⏰ I'll remind you to {msg} at {_fmt_ist(parsed.fire_at)}{suffix}")
    except Exception as e:  # noqa: BLE001 - never crash the gateway on a reminder
        await channel.send("⚠️ Reminder system hiccup — try that again?")
        _log(f"reminder failed: {e!r}")


async def _run_calendar(message, route) -> None:
    """Add or list calendar events — no agent loop.

    CalendarClient is imported lazily so google-api-python-client is optional.
    Blocking calendar calls run in a thread via asyncio.to_thread.
    """
    channel = message.channel
    action = route.params.get("action", "add")
    text = route.params.get("text") or _strip_mentions(getattr(message, "content", "") or "")
    try:
        async with _sem():
            from integrations.calendar import CalendarClient  # lazy import

            client = CalendarClient()

            if action == "list":
                events = await asyncio.to_thread(client.list_events)
                if events is None:
                    await channel.send(
                        "📅 Calendar isn't connected yet — share the calendar with "
                        "the service account and set JACK_CALENDAR_ID to get started."
                    )
                    return
                if not events:
                    await channel.send("📅 Nothing on the calendar today.")
                    return
                summary = await asyncio.to_thread(client.events_summary_text)
                await channel.send(f"📅 Today's calendar:\n{summary}")
                return

            # action == "add"
            # Extract the start time from the free-text using reminders.parser.parse_time
            # (it returns fire_at in UTC; we convert to IST for the event datetime).
            from datetime import timedelta, timezone

            from reminders import parser as rparser  # lazy import

            try:
                parsed = await asyncio.to_thread(rparser.parse_time, text)
            except ValueError:
                parsed = None

            _IST = timezone(timedelta(hours=5, minutes=30))

            if parsed is not None:
                start_ist = parsed.fire_at.astimezone(_IST)
            else:
                # No recognisable time — default to top of the next hour in IST.
                import datetime as _dt

                now_ist = _dt.datetime.now(_IST)
                start_ist = now_ist.replace(minute=0, second=0, microsecond=0) + _dt.timedelta(hours=1)

            # Derive a short event summary by stripping time/calendar phrases.
            summary_text = _extract_event_title(text)

            result = await asyncio.to_thread(
                client.add_event,
                summary_text,
                start_ist,
            )
            if result is None:
                await channel.send(
                    "📅 Calendar isn't connected yet — share the calendar with "
                    "the service account and set JACK_CALENDAR_ID to get started."
                )
                return

            when_str = start_ist.strftime("%-I:%M %p IST, %a %b %-d")
            await channel.send(f"Added ✅ {result['summary']} — {when_str}")
    except Exception as e:  # noqa: BLE001 — never crash the gateway on a calendar error
        await channel.send("📅 Calendar glitch — try that again?")
        _log(f"calendar failed: {e!r}")


# Phrases to strip when extracting a clean event title from the raw text.
_CAL_STRIP_RE = re.compile(
    r"\b(?:add|put|create|book|insert|log|schedule|to\s+(?:my\s+)?calendar|"
    r"on\s+(?:my\s+)?calendar|in\s+(?:my\s+)?calendar|"
    r"appointment|event|meeting)\b",
    re.IGNORECASE,
)
_TIME_STRIP_RE = re.compile(
    r"\b(?:at|on|by|this|tonight|tomorrow|today|next)\b.*$",
    re.IGNORECASE,
)


def _extract_event_title(text: str) -> str:
    """Best-effort extraction of a clean event summary from the raw message."""
    t = _CAL_STRIP_RE.sub(" ", text)
    t = _TIME_STRIP_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" .,!?")
    return t or text.strip()


async def _dispatch(adapter, message, route) -> None:
    channel = message.channel
    if route.intent == "status":
        await channel.send(_status_text())
        return
    if route.intent == "complaint":
        # A complaint about a missed/forgotten reminder ("you didn't remind me ...").
        # Apologize + offer to reschedule. Set NO reminder, run NO agent loop.
        await channel.send(
            "You're right — sorry about that. Want me to set it again? "
            "Just tell me the task and time."
        )
        return
    if route.intent == "conversational":
        _fire(_run_conversation(message, route))
        return
    if route.intent == "reminder":
        _fire(_run_reminder(message, route))
        return
    if route.intent == "calendar":
        _fire(_run_calendar(message, route))
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
