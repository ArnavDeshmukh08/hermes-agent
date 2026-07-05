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
import traceback
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


_GOAL_STORE = None


def _goal_store():
    """Lazy singleton GoalStore (shares ~/.hermes/goals.json with the reasoner)."""
    global _GOAL_STORE
    if _GOAL_STORE is None:
        from jack_goals.store import GoalStore  # lazy import

        _GOAL_STORE = GoalStore()
    return _GOAL_STORE


def _voice(intent: str, data: dict, user_message: str, fallback_plain: str) -> str:
    """Route a successful structured reply through Jack's personality layer.

    Imports jack_voice.compose lazily so handler tests can mock it easily.
    Returns fallback_plain if compose is unavailable or raises.

    In test mode (HERMES_LLM_MOCK=1) the layer is bypassed so existing handler
    tests continue to assert against deterministic fallback strings rather than
    mock LLM output.  The compose layer has its own test suite.
    """
    if str(os.environ.get("HERMES_LLM_MOCK", "")).strip().lower() in {"1", "true", "yes"}:
        return fallback_plain
    try:
        from jack_voice.compose import compose_reply  # noqa: PLC0415
        return compose_reply(intent, data, user_message, fallback_plain)
    except Exception as e:  # noqa: BLE001
        _log(f"_voice fallback ({intent}): {e!r}")
        return fallback_plain


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
            fallback_plain = "⏰ Your reminders:\n" + "\n".join(lines)
            data = {
                "reminders": [
                    {
                        "message": r["message"],
                        "fire_at_ist": _fmt_ist(r["fire_at"]),
                        "recurring": bool(r.get("recurring")),
                    }
                    for r in items
                ]
            }
            await channel.send(_voice("reminder_list", data, text, fallback_plain))
            return
        if action == "cancel":
            items = await asyncio.to_thread(_store().list_pending, user_id)
            target = _match_reminder(text, items)
            if not target:
                await channel.send("Which one? Try `what reminders do I have`, then `cancel the <name> reminder`.")
                return
            ok = await asyncio.to_thread(_store().cancel, target["id"], user_id)
            if ok:
                fallback_plain = f"🗑️ Cancelled: {target['message']}"
                data = {"cancelled_message": target["message"]}
                await channel.send(_voice("reminder_cancel", data, text, fallback_plain))
            else:
                await channel.send("Couldn't cancel that one.")
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
        fallback_plain = f"Got it ⏰ I'll remind you to {msg} at {_fmt_ist(parsed.fire_at)}{suffix}"
        data = {"message": msg, "fire_at_ist": _fmt_ist(parsed.fire_at), "recurring": bool(parsed.recurring)}
        await channel.send(_voice("reminder_set", data, text, fallback_plain))
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
            from integrations import google_provider  # lazy import

            client = google_provider.calendar_client()

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
                fallback = f"📅 Today's calendar:\n{summary}"
                await channel.send(_voice("calendar_list", {"events_summary": summary}, text, fallback))
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

            when_str = start_ist.strftime("%a %b %-d at %-I:%M %p IST")
            fallback = f"Added ✅ {result['summary']} — {when_str}"
            await channel.send(_voice("calendar_add", {"event_title": result["summary"], "when_ist": when_str}, text, fallback))
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
    t = re.sub(r"^(?:a|an|the|my)\s+", "", t, flags=re.IGNORECASE)  # drop leading article ("a gym session")
    return t or text.strip()


# Message shown whenever a Google-backed feature is asked for but OAuth isn't set up.
_GOOGLE_NOT_CONNECTED = (
    "📭 Google isn't connected yet. Run the one-time consent on your Mac:\n"
    "`python3 bin/jack_google_auth.py`\n"
    "After that I'll read it on every restart — no re-auth needed."
)

# Reply-to-sender extraction: "reply to the email from Sarah" → "Sarah".
_EMAIL_TARGET_RE = re.compile(
    r"\b(?:from|to|for)\s+([A-Z][\w'&.-]*(?:\s+[A-Z][\w'&.-]*)?)",
)


def _draft_reply_body(msg: dict) -> str:
    """Build a courteous, clearly-a-starting-point reply skeleton for *msg*.

    Deterministic (no LLM dependency) so the draft is predictable; Arnav edits it
    before sending. Never includes anything Jack didn't actually read from the thread.
    """
    sender = (msg.get("from") or "there").split()[0].strip(",") or "there"
    subject = msg.get("subject") or ""
    ref = f" about “{subject}”" if subject and subject != "(no subject)" else ""
    return (
        f"Hi {sender},\n\n"
        f"Thanks for your email{ref} — following up here.\n\n"
        "[Jack drafted this as a starting point — edit before sending.]\n\n"
        "Best,\nArnav"
    )


async def _run_email(message, route) -> None:
    """List unread business mail, or draft a reply — Gmail is read + draft-only.

    There is NO send path: `draft` creates a Gmail draft in Arnav's Drafts folder and
    stops. Blocking Gmail calls run in threads. Never crashes the gateway.
    """
    channel = message.channel
    action = route.params.get("action", "list")
    text = route.params.get("text") or _strip_mentions(getattr(message, "content", "") or "")
    try:
        async with _sem():
            from integrations import google_provider  # lazy import

            client = google_provider.gmail_client()

            if action == "draft":
                unread = await asyncio.to_thread(client.list_unread, None, 15)
                if unread is None:
                    await channel.send(_GOOGLE_NOT_CONNECTED)
                    return
                if not unread:
                    await channel.send("📭 No unread email to reply to right now.")
                    return

                # Pick the target: a sender named in the message, else the newest unread.
                target = None
                m = _EMAIL_TARGET_RE.search(text)
                if m:
                    needle = m.group(1).lower()
                    target = next(
                        (u for u in unread if needle in (u.get("from", "").lower())
                         or needle in (u.get("from_email", "").lower())),
                        None,
                    )
                target = target or unread[0]

                full = await asyncio.to_thread(client.get_message, target["id"])
                context = full or target
                subject = context.get("subject", "")
                reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
                body = _draft_reply_body(context)

                draft = await asyncio.to_thread(
                    lambda: client.create_draft(
                        to=context.get("from_email", ""),
                        subject=reply_subject,
                        body=body,
                        thread_id=context.get("thread_id"),
                        in_reply_to=context.get("rfc_message_id") or None,
                    )
                )
                if draft is None:
                    await channel.send("✍️ Couldn't create that draft — try again shortly.")
                    return

                fallback = (
                    f"✍️ Drafted a reply to {context.get('from', 'them')} "
                    f"(“{subject}”). It's sitting in your Drafts — I did NOT send it. "
                    "Review and send when you're happy."
                )
                data = {"to": context.get("from", ""), "subject": subject}
                await channel.send(_voice("email_draft", data, text, fallback))
                return

            # action == "list"
            unread = await asyncio.to_thread(client.list_unread, None, 8)
            if unread is None:
                await channel.send(_GOOGLE_NOT_CONNECTED)
                return
            if not unread:
                await channel.send("📭 Inbox is clear — no unread business mail.")
                return

            summary = "\n".join(f"• {u['from']}: {u['subject']}" for u in unread)

            # Flag senders who aren't in Orsa yet (never silently ignore a new contact).
            # Runs Mac-side (Orsa DB + Apollo live there) via the provider.
            flag_line = ""
            try:
                flag_text = await asyncio.to_thread(google_provider.unknown_contact_flags_text)
                if flag_text:
                    flag_line = f"\n\n🔎 Not in Orsa yet:\n{flag_text}"
            except Exception as e:  # noqa: BLE001 — reconciliation is best-effort
                _log(f"email reconcile skipped: {e!r}")

            fallback = f"📬 Unread ({len(unread)}):\n{summary}{flag_line}"
            data = {"unread_count": len(unread), "unread_messages": summary}
            await channel.send(_voice("email_list", data, text, fallback))
    except Exception as e:  # noqa: BLE001 — never crash the gateway on an email error
        await channel.send("📭 Email glitch — try that again?")
        _log(f"email failed: {e!r}")


async def _run_crm(message, route) -> None:
    """Answer an Orsa CRM read query (this week's new leads / counts). Read-only."""
    channel = message.channel
    action = route.params.get("action", "query")
    text = route.params.get("text") or _strip_mentions(getattr(message, "content", "") or "")

    # Honest capability boundary: Jack reads Orsa, never writes it. Sent as a fixed
    # string (no LLM) so it can never be embellished into a promise Jack can't keep.
    if action == "readonly_notice":
        await channel.send(
            "🗂️ I can *read* Orsa but I can't create leads in it — that happens in Orsa "
            "itself (or your scrape pipeline). Want me to show what's already in there?"
        )
        return

    try:
        async with _sem():
            from integrations import google_provider  # lazy import

            client = google_provider.orsa_client()
            if not await asyncio.to_thread(client.is_connected):
                await channel.send(
                    "🗂️ Orsa isn't reachable — check the DB path "
                    "(set JACK_ORSA_DB_PATH if it moved)."
                )
                return

            wants_count = bool(re.search(r"\bhow\s+many\b", text, re.IGNORECASE))
            if wants_count:
                total = await asyncio.to_thread(client.lead_count)
                new_n = await asyncio.to_thread(client.new_leads_count, 7)
                fallback = f"🗂️ Orsa: {total} leads total · {new_n} added in the last 7 days."
                data = {"total_leads_in_orsa": total, "new_leads_in_last_7_days": new_n}
                await channel.send(_voice("crm_count", data, text, fallback))
                return

            new_n = await asyncio.to_thread(client.new_leads_count, 7)
            if not new_n:
                await channel.send("🗂️ No new leads in Orsa this week.")
                return
            summary = await asyncio.to_thread(client.new_leads_summary_text, 7)
            fallback = f"🗂️ {new_n} new lead(s) in Orsa this week:\n{summary}"
            # Self-describing keys so the personality layer can't reinvent the timeframe.
            data = {"new_leads_in_last_7_days": new_n, "sample_leads": summary}
            await channel.send(_voice("crm_new_leads", data, text, fallback))
    except Exception as e:  # noqa: BLE001 — never crash the gateway on a CRM error
        await channel.send("🗂️ Orsa glitch — try that again?")
        _log(f"crm failed: {e!r}")


def _match_goal(hint: str, goals: list):
    """Pick the goal a query/update refers to by word overlap on title/target."""
    h = (hint or "").lower()
    best, score = None, 0
    for g in goals:
        words = [w for w in re.findall(r"\w+", (g.title + " " + g.target).lower()) if len(w) > 2]
        s = sum(1 for w in words if w in h)
        if s > score:
            best, score = g, s
    if best:
        return best
    return goals[0] if len(goals) == 1 else None


def _garmin_progress_line(metric: str) -> str:
    """Best-effort 'actual data' line for a garmin-backed goal. '' on failure."""
    try:
        from integrations.garmin import GarminClient  # lazy import

        client = GarminClient()
        if metric == "garmin_steps":
            stats = client.get_stats()
            if stats and stats.get("steps") is not None:
                return f"{stats['steps']} steps today"
        elif metric == "garmin_runs":
            stats = client.get_stats()
            if stats and stats.get("calories") is not None:
                return f"{stats.get('calories')} cal today"
    except Exception:  # noqa: BLE001
        pass
    return ""


async def _run_goal(message, route) -> None:
    """Create / query / update goals against the durable GoalStore — no agent loop.

    Framing guardrail: Jack TRACKS Arnav's own plan. Confirmations reflect what
    HE said; we never inject a training/diet/medical prescription here.
    """
    channel = message.channel
    action = route.params.get("action", "query")
    text = route.params.get("text") or _strip_mentions(getattr(message, "content", "") or "")
    try:
        from jack_goals.intents import (  # lazy import
            confirm_create_message,
            parse_create_goal,
            parse_query_goal,
            parse_update_goal,
        )

        if action == "create":
            async with _sem():
                from lib.llm import complete  # lazy import

                fields = await asyncio.to_thread(parse_create_goal, text, complete)
            goal = await asyncio.to_thread(
                _goal_store().create_goal,
                fields["title"],
                fields["type"],
                fields["target"],
                fields["plan"],
                fields["metric"],
                fields.get("deadline"),
            )
            confirmation = confirm_create_message(goal.to_dict())
            voiced = _voice("goal_create", {"goal": goal.to_dict()}, text, confirmation)
            await channel.send(voiced)
            _log(f"goal created: {goal.id} — {goal.title}")
            return

        if action == "query":
            q = parse_query_goal(text)
            goals = await asyncio.to_thread(_goal_store().list_active_goals)
            if not goals:
                await channel.send(
                    "You've got no active goals right now. "
                    "Tell me one — e.g. \"my goal is to run a marathon in 10 weeks\"."
                )
                return
            target_goal = (
                _match_goal(q.get("goal_hint", ""), goals)
                if q.get("query_type") == "progress"
                else None
            )
            lines = []
            for g in goals if target_goal is None else [target_goal]:
                extra = ""
                if g.is_garmin:
                    extra = await asyncio.to_thread(_garmin_progress_line, g.metric)
                deadline = f" (by {g.deadline})" if g.deadline else ""
                lines.append(f"• {g.title}{deadline}{(' — ' + extra) if extra else ''}")
            count = len(goals)
            header = (
                f"You've got {count} active goal" + ("s" if count != 1 else "")
                + ". Reflecting back what you set:"
            )
            fallback = header + "\n" + "\n".join(lines)
            goals_data = [
                {"title": g.title, "deadline": g.deadline, "progress": extra}
                for g, extra in zip(
                    goals if target_goal is None else [target_goal],
                    [line.split(" — ", 1)[1] if " — " in line else "" for line in lines],
                )
            ]
            await channel.send(_voice("goal_query", {"goals": goals_data, "count": count}, text, fallback))
            return

        # action == "update"
        async with _sem():
            from lib.llm import complete  # lazy import

            upd = await asyncio.to_thread(parse_update_goal, text, complete)
        goals = await asyncio.to_thread(_goal_store().list_active_goals)
        if not goals:
            await channel.send("No active goals to update yet.")
            return
        target_goal = _match_goal(upd.get("goal_hint", ""), goals)
        if target_goal is None:
            await channel.send(
                "Which goal? Try \"what are my goals\" first, "
                "then \"mark the marathon goal done\"."
            )
            return
        updates = upd.get("updates", {})
        if "progress_note" in updates:
            await asyncio.to_thread(
                _goal_store().append_progress, target_goal.id, updates["progress_note"]
            )
            fallback = f"Logged against {target_goal.title}. Keeping count."
            await channel.send(
                _voice(
                    "goal_update_progress",
                    {"goal_title": target_goal.title, "note": updates["progress_note"]},
                    text,
                    fallback,
                )
            )
            return
        new_status = updates.get("status")
        if new_status in ("done", "paused", "active"):
            await asyncio.to_thread(_goal_store().set_status, target_goal.id, new_status)
            verb = {"done": "marked complete", "paused": "paused", "active": "back on"}[new_status]
            tail = " Nice work." if new_status == "done" else ""
            fallback = f"Done — {target_goal.title} {verb}.{tail}"
            await channel.send(
                _voice(
                    "goal_update_status",
                    {"goal_title": target_goal.title, "new_status": new_status, "verb": verb},
                    text,
                    fallback,
                )
            )
            return
        await channel.send(
            f"Got the goal ({target_goal.title}) but not sure what to change — "
            "done, pause, or a progress note?"
        )
    except Exception as e:  # noqa: BLE001 — never crash the gateway on a goal turn
        await channel.send("⚠️ Goal system hiccup — try that again?")
        _log(f"goal failed: {e!r}")


def _current_ist_time_reply() -> str:
    """Read the system clock and return a human-friendly IST time string.

    This NEVER touches the LLM — it reads datetime directly so Jack can
    never fabricate the time from Arnav's routine or memories.
    """
    import datetime as _dt
    from zoneinfo import ZoneInfo

    now = _dt.datetime.now(ZoneInfo("Asia/Kolkata"))
    h = now.hour % 12 or 12
    ampm = "AM" if now.hour < 12 else "PM"
    return (
        f"It's {h}:{now.minute:02d} {ampm} IST — "
        f"{now.strftime('%A, %d %B %Y')}."
    )


async def _run_time_date(message, route) -> None:  # noqa: ARG001
    """Reply with the real IST time. No LLM, no bridge, sub-millisecond."""
    channel = message.channel
    try:
        reply = _current_ist_time_reply()
        await channel.send(reply)
    except Exception as e:  # noqa: BLE001
        await channel.send("⚠️ Couldn't read the clock — try again?")
        _log(f"time_date failed: {e!r}")


async def _run_health(message, route) -> None:
    """Fetch Garmin data and post a chat-friendly summary — no agent loop.

    Routes by query type:
      sleep  — last_night_sleep(), falls back to yesterday if today is empty
      stats  — get_stats() for steps/calories/stress/body battery
      full   — get_daily_summary() with sleep fallback

    Honest-failure policy: if the Mac service is unreachable or returns nothing,
    we say so clearly.  We NEVER fabricate data or invent a fictional setup.
    """
    import datetime as _dt

    channel = message.channel
    text = route.params.get("text", "")
    try:
        try:
            from integrations.garmin import GarminClient  # lazy import
            from integrations.garmin_chat import (  # lazy import
                classify_health_query,
                format_garmin_for_chat,
                format_sleep,
                format_stats,
            )
        except ImportError as e:
            await channel.send("⚠️ Couldn't fetch your Garmin data right now — try again in a moment.")
            _log(f"health import failed: {e!r}")
            return

        client = GarminClient()
        query_type = classify_health_query(text)

        # ── sleep path ────────────────────────────────────────────────────────
        if query_type == "sleep":
            date_label = "last night"
            async with _sem():
                sleep = await asyncio.to_thread(client.last_night_sleep)
                if sleep is None:
                    # Sleep data is filed under the wake-up date; at 1 am that's
                    # today, but if Garmin hasn't synced yet try yesterday's night.
                    _IST_TZ = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
                    yesterday = (
                        _dt.datetime.now(tz=_IST_TZ) - _dt.timedelta(days=1)
                    ).date().isoformat()
                    sleep = await asyncio.to_thread(client.last_night_sleep, yesterday)
                    if sleep is not None:
                        date_label = f"night of {yesterday}"

            if sleep is None:
                await channel.send(
                    "No sleep data synced yet — Garmin usually updates within "
                    "30 minutes of waking up."
                )
                return

            detail = format_sleep(sleep).removeprefix("Sleep: ")
            fallback = f"Sleep ({date_label}):\n{detail}"
            await channel.send(_voice("health_sleep", {"date_label": date_label, "sleep": sleep}, text, fallback))
            return

        # ── stats / activity path ─────────────────────────────────────────────
        if query_type == "stats":
            async with _sem():
                stats = await asyncio.to_thread(client.get_stats)

            if stats is None:
                await channel.send(
                    "I can't reach your Garmin data right now — the Mac service looks down. "
                    "Check if the Garmin service is running on your Mac (port 8765)."
                )
                return

            lines = format_stats(stats)
            if not lines:
                await channel.send("No activity data for today yet — check back after your first sync.")
                return

            fallback = "Here's your activity data for today:\n" + "\n".join(lines)
            await channel.send(_voice("health_stats", {"stats": stats}, text, fallback))
            return

        # ── full / general path ───────────────────────────────────────────────
        async with _sem():
            summary = await asyncio.to_thread(client.get_daily_summary)

        if summary is None:
            await channel.send(
                "I can't reach your Garmin data right now — the Mac service looks down. "
                "Check if the Garmin service is running on your Mac (port 8765)."
            )
            return

        # If sleep is missing from the summary, try yesterday's night.
        raw_sleep = summary.get("sleep")
        if raw_sleep is None or (isinstance(raw_sleep, dict) and raw_sleep.get("error") == "no_data"):
            _IST_TZ = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
            yesterday = (
                _dt.datetime.now(tz=_IST_TZ) - _dt.timedelta(days=1)
            ).date().isoformat()
            async with _sem():
                yesterday_sleep = await asyncio.to_thread(client.last_night_sleep, yesterday)
            if yesterday_sleep:
                summary = dict(summary)
                summary["sleep"] = yesterday_sleep

        formatted = format_garmin_for_chat(summary)
        if not formatted:
            await channel.send(
                "I pulled your Garmin data but nothing came through — might be a sync delay."
            )
            return

        fallback = f"Here's your Garmin data for today:\n{formatted}"
        await channel.send(_voice("health_full", {"summary": summary}, text, fallback))

    except Exception as e:  # noqa: BLE001 — never crash the gateway
        await channel.send("⚠️ Couldn't fetch your Garmin data right now — try again in a moment.")
        _log(f"health query failed: {e!r}")


def _load_health_today() -> dict | None:
    """Read ~/.hermes/health_today.json with LOCK_SH. Returns None if missing or unreadable.

    This file is written by the Garmin poller and may not exist yet — we handle
    that gracefully rather than raising. Uses fcntl.LOCK_SH for safe concurrent reads.
    """
    import fcntl
    import json

    health_path = Path(os.environ.get("JACK_HEALTH_TODAY", str(Path.home() / ".hermes" / "health_today.json")))
    try:
        with open(health_path, "r", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _run_marathon_training(user_message: str, route) -> str:  # type: ignore[return]
    """Build a framing-compliant marathon check-in reply.

    Framing rule: reflect / track ONLY. Never prescribe training, diet, or medical advice.
    Returns a plain string (personality layer is applied by the caller via _voice()).
    """
    import datetime as _dt

    _IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))

    # ── deadline math ─────────────────────────────────────────────────────────
    today_ist = _dt.datetime.now(_IST).date()
    deadline_date = _dt.date(2026, 8, 2)
    days_to_deadline = (deadline_date - today_ist).days
    weeks_to_deadline = days_to_deadline // 7

    # ── goal data ─────────────────────────────────────────────────────────────
    goal_title = "Run a marathon"
    progress_notes_count = 0
    try:
        goals = _goal_store().list_active_goals()
        for g in goals:
            if "marathon" in g.title.lower() or g.metric == "garmin_runs":
                goal_title = g.title
                progress_notes_count = len(g.progress_notes)
                if g.deadline:
                    try:
                        dl = _dt.date.fromisoformat(g.deadline)
                        days_to_deadline = (dl - today_ist).days
                        weeks_to_deadline = days_to_deadline // 7
                        deadline_date = dl
                    except ValueError:
                        pass
                break
    except Exception:  # noqa: BLE001
        pass

    # ── health data — prefer health_today.json, fall back to GarminClient ────
    steps: int | None = None
    runs_today = None
    runs_available = False
    sleep_hours: float | None = None
    body_battery: int | None = None
    health_data_fresh = False

    health_today = _load_health_today()
    if health_today and not health_today.get("is_stale", True):
        health_data_fresh = True
        stats = health_today.get("stats") or {}
        steps = stats.get("steps")
        body_battery = stats.get("body_battery_high")
        sleep_info = health_today.get("sleep") or {}
        sleep_hours = sleep_info.get("total_sleep_h")
        runs_today = health_today.get("runs_today")
        runs_available = bool(health_today.get("runs_available", False))
    else:
        # Fall back to live GarminClient if file is absent or stale.
        try:
            from integrations.garmin import GarminClient  # lazy import

            client = GarminClient()
            stats = client.get_stats()
            if stats:
                health_data_fresh = True
                steps = stats.get("steps")
                body_battery = stats.get("body_battery_high")
            sleep_info = client.last_night_sleep()
            if sleep_info:
                sleep_hours = sleep_info.get("total_sleep_h")
        except Exception:  # noqa: BLE001
            pass

    # ── load multi-day run history (health_history.json) ─────────────────────
    history_context: str = ""
    runs_this_week_count: int | None = None
    days_since_run: int | None = None
    try:
        import json as _json
        hist_path = Path.home() / ".hermes" / "health_history.json"
        if hist_path.exists():
            import fcntl as _fcntl
            with hist_path.open("r", encoding="utf-8") as _hf:
                _fcntl.flock(_hf.fileno(), _fcntl.LOCK_SH)
                try:
                    _raw_hist = _hf.read()
                finally:
                    _fcntl.flock(_hf.fileno(), _fcntl.LOCK_UN)
            _history = _json.loads(_raw_hist)
            if isinstance(_history, list) and _history:
                from integrations.garmin_poller import days_since_last_run, runs_this_week
                days_since_run = days_since_last_run(_history)
                runs_this_week_count = runs_this_week(_history)
                if days_since_run is None:
                    streak_text = "no run found in recent history"
                elif days_since_run == 0:
                    streak_text = "ran today"
                else:
                    streak_text = f"last run {days_since_run} day{'s' if days_since_run != 1 else ''} ago"
                rtw = runs_this_week_count if runs_this_week_count is not None else 0
                history_context = f"Run history: {rtw} run day(s) this week, {streak_text}."
            else:
                history_context = "I don't have run history yet."
        else:
            history_context = "I don't have run history yet."
    except Exception:  # noqa: BLE001
        history_context = ""

    # ── assemble data dict ────────────────────────────────────────────────────
    data = {
        "goal_title": goal_title,
        "deadline": deadline_date.isoformat(),
        "weeks_to_deadline": weeks_to_deadline,
        "days_to_deadline": days_to_deadline,
        "steps_today": steps,
        "runs_today": runs_today,
        "runs_available": runs_available,
        "sleep_hours": sleep_hours,
        "progress_notes_count": progress_notes_count,
        "health_data_fresh": health_data_fresh,
        "body_battery": body_battery,
        "history_context": history_context,
        "runs_this_week": runs_this_week_count,
        "days_since_last_run": days_since_run,
    }

    # ── fallback plain — honest, no prescriptions ─────────────────────────────
    deadline_str = deadline_date.strftime("%b %-d")
    if not health_data_fresh:
        data_note = "No Garmin data available right now."
    else:
        step_str = f"{steps:,}" if steps is not None else "no step data"
        run_str = "yes" if runs_today else "none logged"
        data_note = f"Steps today: {step_str}. Run data in Garmin: {run_str}."

    history_note = f" {history_context}" if history_context else ""

    fallback = (
        f"Marathon check-in: {weeks_to_deadline} week{'s' if weeks_to_deadline != 1 else ''} to {deadline_str}. "
        f"{data_note}{history_note}"
    )
    return _voice("marathon_training", data, user_message, fallback)


async def _run_marathon_training_async(message, route) -> None:
    """Async wrapper: loads data in a thread and sends the personality-voiced reply."""
    channel = message.channel
    text = route.params.get("text") or _strip_mentions(getattr(message, "content", "") or "")
    try:
        async with _sem():
            reply = await asyncio.to_thread(_run_marathon_training, text, route)
        await channel.send(reply)
    except Exception as e:  # noqa: BLE001 — never crash the gateway on a marathon check-in
        await channel.send("⚠️ Couldn't pull your marathon data right now — try again in a moment.")
        _log(f"marathon_training failed: {e!r}")


def _parse_config_request(text: str) -> dict | None:
    """Extract {key, value} from a config-change request using the LLM.

    Returns a dict with JACK_* key and string value (ready for c.set()), or
    None if parsing fails or the request is not a config change.
    Module-level so tests can monkeypatch it directly.
    """
    try:
        from jack_tools.self_config import FRIENDLY_TO_KEY  # lazy import
        from lib.llm import complete  # lazy import

        friendly_keys = ", ".join(FRIENDLY_TO_KEY.keys())
        system = (
            "You extract configuration-change requests into JSON.\n"
            f"The configurable settings (friendly names) are: {friendly_keys}.\n"
            "Return ONLY valid JSON with exactly two fields:\n"
            '  "key": one of the friendly names above (string)\n'
            '  "value": the new value — times as "HH:MM" 24h, booleans as true/false, ints as number\n'
            "If the message is NOT a config change request, return: null\n\n"
            "Examples:\n"
            '  "change briefing to 8am" -> {"key": "briefing_time", "value": "08:00"}\n'
            '  "turn off weather" -> {"key": "weather_enabled", "value": false}\n'
            '  "enable news" -> {"key": "news_enabled", "value": true}\n'
            '  "set reminder frequency to 60 seconds" -> {"key": "reminder_poll_seconds", "value": 60}\n'
            '  "disable memory" -> {"key": "memory_enabled", "value": false}\n'
        )
        raw = complete(system, text, max_tokens=200, prefer="groq", json_only=True, timeout=20)

        import json  # lazy import

        parsed = json.loads(raw)
        if parsed is None:
            return None
        if not isinstance(parsed, dict):
            return None
        friendly = parsed.get("key")
        value = parsed.get("value")
        if not friendly or value is None:
            return None
        jack_key = FRIENDLY_TO_KEY.get(str(friendly))
        if jack_key is None:
            return None
        return {"key": jack_key, "value": str(value) if not isinstance(value, bool) else ("true" if value else "false")}
    except Exception as e:  # noqa: BLE001
        _log(f"_parse_config_request failed: {e!r}")
        return None


async def _run_self_config(message, route) -> None:
    """Handle self_config intent: status / list / set."""
    channel = message.channel
    action = route.params.get("action", "status")
    text = route.params.get("text", "")
    try:
        from jack_tools.self_config import JackSelfConfig  # lazy import

        c = JackSelfConfig()

        if action == "status":
            try:
                st = await asyncio.to_thread(c.get_status)
                all_active = all(v == "active" for v in st.values())
                if all_active:
                    fallback = "All systems running ✅"
                    await channel.send(_voice("self_config_status", {"all_active": True, "services": {}}, text, fallback))
                else:
                    lines = [f"• {svc}: {state}" for svc, state in sorted(st.items())]
                    fallback = "System status:\n" + "\n".join(lines)
                    await channel.send(_voice("self_config_status", {"all_active": False, "services": dict(sorted(st.items()))}, text, fallback))
            except Exception as e:  # noqa: BLE001
                await channel.send("⚠️ Couldn't check system status right now — try again shortly.")
                _log(f"self_config status failed: {e!r}")
            return

        if action == "list":
            try:
                items = await asyncio.to_thread(c.list_configurable)
                parts = []
                settings_data = []
                for item in items:
                    friendly = item["friendly"] or item["key"]
                    val = item["value"] if item["value"] is not None else "not set"
                    desc = item["description"]
                    parts.append(f"• {friendly} (currently {val}) — {desc}")
                    settings_data.append({"friendly": friendly, "value": val, "description": desc})
                fallback = "Here's what I can configure:\n" + "\n".join(parts)
                await channel.send(_voice("self_config_list", {"settings": settings_data}, text, fallback))
            except Exception as e:  # noqa: BLE001
                await channel.send("⚠️ Couldn't fetch settings — try again shortly.")
                _log(f"self_config list failed: {e!r}")
            return

        # action == "set"
        parsed = _parse_config_request(text)
        if parsed is None:
            try:
                items = await asyncio.to_thread(c.list_configurable)
                friendly_list = ", ".join(item["friendly"] or item["key"] for item in items)
            except Exception:  # noqa: BLE001
                friendly_list = "briefing_time, briefing_enabled, weather_enabled, news_enabled, memory_enabled, reminder_poll_seconds"
            await channel.send(
                f"I couldn't tell which setting you meant. Here's what I can change: {friendly_list}."
            )
            return

        try:
            result = await asyncio.to_thread(c.set, parsed["key"], parsed["value"])
        except Exception as e:  # noqa: BLE001
            await channel.send(f"❌ Something went wrong trying to update that setting: {type(e).__name__}")
            _log(f"self_config set exception: {e!r}")
            return

        if result["success"]:
            fallback = "✅ Done — " + result["message"]
            await channel.send(_voice("self_config_set", {"key": parsed["key"], "success": True, "message": result["message"]}, text, fallback))
        else:
            fallback = "❌ Couldn't update that — " + result["message"]
            await channel.send(_voice("self_config_set", {"key": parsed["key"], "success": False, "message": result["message"]}, text, fallback))

    except Exception:  # noqa: BLE001
        await channel.send("⚠️ Self-config glitch — try that again?")
        _log(f"self_config failed: {traceback.format_exc()}")


async def _run_proactive_control(message, route) -> None:
    """Toggle proactive nudges on/off. Persists JACK_PROACTIVE_ENABLED via JackSelfConfig."""
    channel = message.channel
    control = route.params.get("control", "off")
    try:
        from jack_tools.self_config import JackSelfConfig  # lazy import

        c = JackSelfConfig()
        value = "true" if control == "on" else "false"
        result = await asyncio.to_thread(c.set, "JACK_PROACTIVE_ENABLED", value)
        if result.get("success"):
            if control == "on":
                await channel.send("🔔 Proactive nudges back on — I'll keep an eye out.")
            else:
                await channel.send("🔕 Proactive nudges paused — I'll stay quiet until you turn them back on.")
        else:
            await channel.send("❌ Couldn't change that — " + result.get("message", "unknown error"))
    except Exception as e:  # noqa: BLE001
        await channel.send("⚠️ Couldn't change that just now — try again?")
        _log(f"proactive_control failed: {e!r}")


async def _run_proactive_status(message, route) -> None:  # noqa: ARG001
    """Report proactive nudge status: on/off, sent today, quiet hours."""
    channel = message.channel
    try:
        from proactive.engine import ProactiveEngine  # lazy import

        async with _sem():
            engine = ProactiveEngine()
            sent_today = await asyncio.to_thread(engine.sent_today_count)
        enabled = _truthy(os.environ.get("JACK_PROACTIVE_ENABLED", "1"))
        max_per_day = int(os.environ.get("JACK_PROACTIVE_MAX_PER_DAY", "3"))
        quiet_start = int(os.environ.get("JACK_PROACTIVE_QUIET_START_IST", "1"))
        quiet_end = int(os.environ.get("JACK_PROACTIVE_QUIET_END_IST", "8"))
        status_str = "ON" if enabled else "OFF"
        await channel.send(
            f"🔔 Proactive nudges: **{status_str}** · sent today: {sent_today}/{max_per_day} · "
            f"quiet hours {quiet_start}am–{quiet_end}am IST\n"
            "Watching for: deadlines, Siddhi check-in, gym, stale goals, Sunday planning, useful news"
        )
    except Exception as e:  # noqa: BLE001
        await channel.send("⚠️ Couldn't fetch proactive status right now — try again shortly.")
        _log(f"proactive_status failed: {e!r}")  # noqa: BLE001


def _truthy(value: str | None) -> bool:
    """Check if a value represents a truthy state."""
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


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
    if route.intent == "email":
        _fire(_run_email(message, route))
        return
    if route.intent == "crm":
        _fire(_run_crm(message, route))
        return
    if route.intent == "self_config":
        _fire(_run_self_config(message, route))
        return
    if route.intent == "time_date":
        _fire(_run_time_date(message, route))
        return
    if route.intent == "goal":
        _fire(_run_goal(message, route))
        return
    if route.intent == "health_query":
        _fire(_run_health(message, route))
        return
    if route.intent == "marathon_training":
        _fire(_run_marathon_training_async(message, route))
        return
    if route.intent == "proactive":
        if route.params.get("action") == "status":
            _fire(_run_proactive_status(message, route))
        else:
            _fire(_run_proactive_control(message, route))
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
