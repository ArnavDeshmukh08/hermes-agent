"""ProactiveEngine — gathers, deduplicates, and delivers proactive nudges.

Architecture mirrors briefing/morning.py:
- All integration imports are LAZY (inside methods) so no network or missing
  package crashes the module load.
- Every check_* source is individually try/excepted; one broken source never
  blocks the others.
- proactive_log.json uses flock LOCK_EX + tmp file + os.replace (same pattern
  as reminders/store.py _write_all) so concurrent writes never corrupt the log.
- send_fn returning False → do NOT log_sent (leaves the nudge retryable next
  cycle).
- _reload_proactive_flag() re-reads ONLY JACK_PROACTIVE_ENABLED from
  ~/.hermes/.env so a Discord toggle takes effect within one poll without a
  service restart.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from proactive.scorer import P1, P2, P3, SCORE_P1, SCORE_P2, SCORE_P3
from proactive.scorer import PriorityScorer, ProactiveItem

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_IST = timezone(timedelta(hours=5, minutes=30))

_DEFAULT_USER_PATH = Path.home() / ".hermes" / "USER.md"
_DEFAULT_LOG_PATH = Path.home() / ".hermes" / "proactive_log.json"
_DEFAULT_LOG_RETENTION_DAYS = 7

DEDUP_WINDOW_HOURS = int(os.environ.get("JACK_PROACTIVE_DEDUP_HOURS", "6"))


def _to_z(dt: datetime) -> str:
    """Format a datetime as ISO8601 UTC ending in 'Z'."""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _from_z(value: str) -> datetime:
    """Parse an ISO8601 UTC string (with trailing 'Z') to an aware datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _truthy(value: str | None) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# ProactiveEngine
# ---------------------------------------------------------------------------


class ProactiveEngine:
    """Gathers candidate nudges, deduplicates, scores, and delivers them."""

    def __init__(
        self,
        user_path: str | Path | None = None,
        log_path: str | Path | None = None,
        store: Any = None,
        calendar_client: Any = None,
        scorer: PriorityScorer | None = None,
        send_fn: Callable[..., bool] | None = None,
        complete_fn: Callable[..., str] | None = None,
    ) -> None:
        # Resolve user_path: arg → JACK_USER_PATH env → default
        env_user = os.environ.get("JACK_USER_PATH")
        if user_path is not None:
            self._user_path = Path(user_path).expanduser()
        elif env_user:
            self._user_path = Path(env_user).expanduser()
        else:
            self._user_path = _DEFAULT_USER_PATH

        # Resolve log_path: arg → JACK_PROACTIVE_LOG_PATH env → default
        env_log = os.environ.get("JACK_PROACTIVE_LOG_PATH")
        if log_path is not None:
            self._log_path = Path(log_path).expanduser()
        elif env_log:
            self._log_path = Path(env_log).expanduser()
        else:
            self._log_path = _DEFAULT_LOG_PATH

        # Store None deps for lazy init
        self._store = store
        self._calendar_client = calendar_client
        self._scorer = scorer
        self._send_fn = send_fn
        self._complete_fn = complete_fn

    # -- lazy wiring --------------------------------------------------------

    def _get_store(self) -> Any:
        if self._store is None:
            from reminders.store import ReminderStore  # lazy
            self._store = ReminderStore()
        return self._store

    def _get_calendar(self) -> Any:
        if self._calendar_client is None:
            if not _truthy(os.environ.get("JACK_CALENDAR_ENABLED")):
                return None
            try:
                from integrations.calendar import CalendarClient  # lazy
                self._calendar_client = CalendarClient()
            except Exception:  # noqa: BLE001
                return None
        return self._calendar_client

    def _get_scorer(self) -> PriorityScorer:
        if self._scorer is None:
            self._scorer = PriorityScorer()
        return self._scorer

    def _send(self, content: str, user_id: str | None) -> bool:
        fn = self._send_fn
        if fn is None:
            from reminders.notifier import send_message as fn  # type: ignore[no-redef] # lazy
        return fn(content, user_id=user_id)

    # -- log I/O (flock + atomic, mirrors ReminderStore._write_all) ---------

    def _read_log(self) -> list[dict]:
        """Read the proactive log; returns [] if missing or corrupt."""
        if not self._log_path.exists():
            return []
        try:
            with open(self._log_path, "r", encoding="utf-8") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
                try:
                    text = fh.read()
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            if not text.strip():
                return []
            data = json.loads(text)
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            return []

    def _write_log(self, entries: list[dict]) -> None:
        """Atomically write *entries* to the log, pruning old records."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=_DEFAULT_LOG_RETENTION_DAYS)
        pruned: list[dict] = []
        for entry in entries:
            try:
                sent_at = _from_z(entry["sent_at"])
                if sent_at >= cutoff:
                    pruned.append(entry)
            except Exception:  # noqa: BLE001 — corrupt entry, drop it
                pass

        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._log_path.with_suffix(self._log_path.suffix + ".lock")
        with open(lock_path, "a+", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                fd, tmp = tempfile.mkstemp(
                    dir=str(self._log_path.parent),
                    prefix=".tmp-proactive-",
                    suffix=".json",
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        json.dump(pruned, fh, ensure_ascii=False, indent=2)
                        fh.write("\n")
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp, str(self._log_path))
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
            finally:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    # -- USER.md ------------------------------------------------------------

    def _read_user_md(self) -> str:
        """Read USER.md fresh; returns '' on OSError."""
        try:
            return self._user_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    # -- dedup & counting ---------------------------------------------------

    def already_sent_recently(
        self,
        nudge_type: str,
        *,
        now: datetime | None = None,
        window_hours: int = DEDUP_WINDOW_HOURS,
    ) -> bool:
        """True if *nudge_type* appears in the log within *window_hours*."""
        if now is None:
            now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=window_hours)
        for entry in self._read_log():
            if entry.get("nudge_type") != nudge_type:
                continue
            try:
                sent_at = _from_z(entry["sent_at"])
                if sent_at >= cutoff:
                    return True
            except Exception:  # noqa: BLE001 — corrupt sent_at, skip
                continue
        return False

    def sent_today_count(self, *, now: datetime | None = None) -> int:
        """Count log entries sent on today's IST calendar day."""
        if now is None:
            now = datetime.now(timezone.utc)
        today_ist = now.astimezone(_IST).date()
        count = 0
        for entry in self._read_log():
            try:
                sent_at = _from_z(entry["sent_at"])
                if sent_at.astimezone(_IST).date() == today_ist:
                    count += 1
            except Exception:  # noqa: BLE001
                continue
        return count

    def log_sent(self, item: ProactiveItem, *, now: datetime | None = None) -> None:
        """Append a sent record to the proactive log."""
        if now is None:
            now = datetime.now(timezone.utc)
        entries = self._read_log()
        entries.append(
            {
                "nudge_type": item.nudge_type,
                "priority": item.priority,
                "sent_at": _to_z(now),
                "message": item.message,
            }
        )
        self._write_log(entries)

    # -- check_* sources (each individually try/excepted) ------------------

    def check_deadlines(
        self, user_id: str, *, now: datetime | None = None
    ) -> list[ProactiveItem]:
        """P1 items from upcoming deadlines (reminders, calendar, USER.md)."""
        if now is None:
            now = datetime.now(timezone.utc)
        results: list[ProactiveItem] = []

        # Source A: pending reminders within 24h
        try:
            store = self._get_store()
            window_end = now + timedelta(hours=24)
            for r in store.list_pending(user_id):
                fire_at_str = r.get("fire_at", "")
                if not fire_at_str:
                    continue
                try:
                    fire_dt = _from_z(str(fire_at_str))
                except ValueError:
                    continue
                if now <= fire_dt <= window_end:
                    results.append(
                        ProactiveItem(
                            nudge_type=f"deadline_24h_reminder_{r.get('id', '')}",
                            priority=P1,
                            score=SCORE_P1,
                            message=f"Reminder due soon: {r.get('message', '')}",
                            alone=True,
                            meta={"source": "reminder", "id": r.get("id")},
                        )
                    )
        except Exception:  # noqa: BLE001
            pass

        # Source B: calendar events starting within 24h
        try:
            cal = self._get_calendar()
            if cal is not None:
                day = now.astimezone(_IST)
                events = cal.list_events(day=day, max_results=10)
                if events:
                    window_end = now + timedelta(hours=24)
                    for evt in events:
                        start = evt.get("start", {})
                        start_str = start.get("dateTime") or start.get("date")
                        if not start_str:
                            continue
                        try:
                            # dateTime has timezone info; date is all-day
                            if "T" in start_str:
                                start_dt = datetime.fromisoformat(start_str)
                                if start_dt.tzinfo is None:
                                    start_dt = start_dt.replace(tzinfo=_IST)
                                start_dt = start_dt.astimezone(timezone.utc)
                            else:
                                # All-day event: interpret as start-of-day IST
                                from datetime import date as _date
                                d = _date.fromisoformat(start_str)
                                start_dt = datetime(
                                    d.year, d.month, d.day, 0, 0, tzinfo=_IST
                                ).astimezone(timezone.utc)
                        except (ValueError, TypeError):
                            continue
                        if now <= start_dt <= window_end:
                            summary = evt.get("summary", "Event")
                            results.append(
                                ProactiveItem(
                                    nudge_type=f"deadline_24h_cal_{summary[:30]}",
                                    priority=P1,
                                    score=SCORE_P1,
                                    message=f"Calendar event soon: {summary}",
                                    alone=True,
                                    meta={"source": "calendar"},
                                )
                            )
        except Exception:  # noqa: BLE001
            pass

        # Source C: USER.md [CURRENT PRIORITIES] for 'Vytal demo' within 48h
        try:
            user_md = self._read_user_md()
            if "vytal demo" in user_md.lower() or "Vytal demo" in user_md:
                results.append(
                    ProactiveItem(
                        nudge_type="deadline_vytal_demo",
                        priority=P1,
                        score=SCORE_P1,
                        message="Vytal demo is coming up — make sure you're prepared!",
                        alone=True,
                        meta={"source": "user_md"},
                    )
                )
        except Exception:  # noqa: BLE001
            pass

        # Source D: USER.md tasks flagged 'urgent'/'URGENT'
        try:
            user_md = self._read_user_md()
            lines = user_md.splitlines()
            for line in lines:
                if "urgent" in line.lower():
                    task_snip = line.strip()[:60]
                    results.append(
                        ProactiveItem(
                            nudge_type=f"urgent_task_{hash(task_snip) & 0xFFFF}",
                            priority=P1,
                            score=SCORE_P1,
                            message=f"Urgent task flagged: {task_snip}",
                            alone=True,
                            meta={"source": "user_md_urgent"},
                        )
                    )
                    break  # one urgent item per cycle is enough
        except Exception:  # noqa: BLE001
            pass

        return results

    def check_siddhi(
        self,
        *,
        now: datetime | None = None,
        history: list[dict] | None = None,
    ) -> list[ProactiveItem]:
        """P2 (or P1 on crisis) nudge when Siddhi hasn't been mentioned recently.

        Only active 10:00–22:00 IST.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        try:
            now_ist_hour = now.astimezone(_IST).hour
            if not (10 <= now_ist_hour < 22):
                return []

            if not history:
                return []

            # Look through last 6 turns for Siddhi mentions
            recent = history[-6:] if len(history) >= 6 else history
            siddhi_mentions: list[datetime] = []
            crisis_found = False

            crisis_keywords = {"fight", "broke up", "breakup", "crying", "crisis", "depressed"}

            for turn in recent:
                content = str(turn.get("content", "")).lower()
                if "siddhi" in content:
                    # Check for crisis keywords
                    if any(kw in content for kw in crisis_keywords):
                        crisis_found = True
                    ts_str = turn.get("timestamp")
                    if ts_str:
                        try:
                            siddhi_mentions.append(_from_z(str(ts_str)))
                        except Exception:  # noqa: BLE001
                            siddhi_mentions.append(now)
                    else:
                        siddhi_mentions.append(now)

            if crisis_found:
                return [
                    ProactiveItem(
                        nudge_type="siddhi_crisis",
                        priority=P1,
                        score=SCORE_P1,
                        message="Is everything okay with Siddhi? Sounds like things got heavy.",
                        alone=True,
                        meta={"source": "siddhi_crisis"},
                    )
                ]

            if not siddhi_mentions:
                return []

            last_mention = max(siddhi_mentions)
            hours_since = (now - last_mention).total_seconds() / 3600
            if hours_since > 4:
                return [
                    ProactiveItem(
                        nudge_type="siddhi_silence",
                        priority=P2,
                        score=SCORE_P2,
                        message="Haven't heard you mention Siddhi in a while — everything good?",
                        alone=False,
                        meta={"source": "siddhi", "hours_since": round(hours_since, 1)},
                    )
                ]
        except Exception:  # noqa: BLE001
            pass
        return []

    def check_gym(
        self, user_id: str, *, now: datetime | None = None
    ) -> list[ProactiveItem]:
        """P2 nudge if no gym/workout recorded and it's >= 19:00 IST."""
        if now is None:
            now = datetime.now(timezone.utc)
        try:
            now_ist_hour = now.astimezone(_IST).hour
            if now_ist_hour < 19:
                return []

            # Check today's calendar for gym/workout event
            cal = self._get_calendar()
            if cal is not None:
                day = now.astimezone(_IST)
                events = cal.list_events(day=day, max_results=20) or []
                for evt in events:
                    summary = str(evt.get("summary", "")).lower()
                    if any(kw in summary for kw in ("gym", "workout", "exercise", "training", "run")):
                        return []  # gym done — no nudge

            return [
                ProactiveItem(
                    nudge_type="gym_not_done",
                    priority=P2,
                    score=SCORE_P2,
                    message="You haven't logged a workout today — still planning to hit the gym?",
                    alone=False,
                    meta={"source": "gym"},
                )
            ]
        except Exception:  # noqa: BLE001
            pass
        return []

    def check_goals(self, *, now: datetime | None = None) -> list[ProactiveItem]:
        """P2 nudge for a stale goal (not nudged in > 2 days). Max 1 per cycle."""
        if now is None:
            now = datetime.now(timezone.utc)
        try:
            user_md = self._read_user_md()
            if not user_md:
                return []

            # Extract goals from [GOALS] or [CURRENT PRIORITIES] sections
            goals: list[str] = []
            in_section = False
            for line in user_md.splitlines():
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    section = stripped[1:-1].upper()
                    in_section = section in ("GOALS", "CURRENT PRIORITIES")
                    continue
                if in_section and stripped and not stripped.startswith("#"):
                    goals.append(stripped)

            if not goals:
                return []

            log = self._read_log()
            two_days_ago = now - timedelta(days=2)

            for goal in goals[:5]:  # cap scan to first 5 goals
                nudge_type = f"goal_stale_{hash(goal[:40]) & 0xFFFF}"
                # Check if we nudged this goal recently
                last_nudge: datetime | None = None
                for entry in log:
                    if entry.get("nudge_type") == nudge_type:
                        try:
                            sent_at = _from_z(entry["sent_at"])
                            if last_nudge is None or sent_at > last_nudge:
                                last_nudge = sent_at
                        except Exception:  # noqa: BLE001
                            continue

                if last_nudge is None or last_nudge < two_days_ago:
                    return [
                        ProactiveItem(
                            nudge_type=nudge_type,
                            priority=P2,
                            score=SCORE_P2,
                            message=f"How's progress on: {goal[:80]}?",
                            alone=False,
                            meta={"source": "goals", "goal": goal[:80]},
                        )
                    ]
        except Exception:  # noqa: BLE001
            pass
        return []

    def check_weekly_planning(self, *, now: datetime | None = None) -> list[ProactiveItem]:
        """P2 nudge on Sunday 17:00–21:00 IST for weekly planning."""
        if now is None:
            now = datetime.now(timezone.utc)
        try:
            now_ist = now.astimezone(_IST)
            # weekday() == 6 is Sunday
            if now_ist.weekday() != 6:
                return []
            if not (17 <= now_ist.hour < 21):
                return []
            return [
                ProactiveItem(
                    nudge_type="weekly_planning",
                    priority=P2,
                    score=SCORE_P2,
                    message="Sunday evening — good time to plan the week ahead. Want to do a quick review?",
                    alone=False,
                    meta={"source": "weekly_planning"},
                )
            ]
        except Exception:  # noqa: BLE001
            pass
        return []

    def check_p3_useful(self, *, now: datetime | None = None) -> list[ProactiveItem]:
        """P3 low-priority utility nudges: news, body battery, water break."""
        if now is None:
            now = datetime.now(timezone.utc)
        results: list[ProactiveItem] = []

        # ai_news
        try:
            if _truthy(os.environ.get("JACK_NEWS_ENABLED")):
                from briefing.news import NewsFetcher  # lazy
                summary = NewsFetcher().summary_text()
                if summary:
                    results.append(
                        ProactiveItem(
                            nudge_type="ai_news",
                            priority=P3,
                            score=SCORE_P3,
                            message=f"AI/tech news quick take: {summary[:120]}",
                            alone=False,
                            meta={"source": "news"},
                        )
                    )
        except Exception:  # noqa: BLE001
            pass

        # body_battery via Garmin
        try:
            if _truthy(os.environ.get("JACK_GARMIN_ENABLED")):
                from integrations.garmin import GarminClient  # lazy
                client = GarminClient()
                # Try to get body battery or stress level if available
                summary = client.sleep_summary_text()
                if summary and ("low" in summary.lower() or "tired" in summary.lower()):
                    results.append(
                        ProactiveItem(
                            nudge_type="body_battery_low",
                            priority=P3,
                            score=SCORE_P3,
                            message=f"Your Garmin shows you might be running low — {summary}",
                            alone=False,
                            meta={"source": "garmin"},
                        )
                    )
        except Exception:  # noqa: BLE001
            pass

        # water_break — generic P3 nudge during waking hours
        try:
            now_ist_hour = now.astimezone(_IST).hour
            if 9 <= now_ist_hour < 21:
                results.append(
                    ProactiveItem(
                        nudge_type="water_break",
                        priority=P3,
                        score=SCORE_P3,
                        message="Quick reminder to drink some water and stretch if you've been sitting a while.",
                        alone=False,
                        meta={"source": "generic"},
                    )
                )
        except Exception:  # noqa: BLE001
            pass

        return results

    def gather_all_items(
        self,
        user_id: str,
        *,
        now: datetime | None = None,
        history: list[dict] | None = None,
    ) -> list[ProactiveItem]:
        """Collect candidates from all check_* sources, isolating failures."""
        if now is None:
            now = datetime.now(timezone.utc)

        all_items: list[ProactiveItem] = []

        for check_fn, kwargs in [
            (self.check_deadlines, {"user_id": user_id, "now": now}),
            (self.check_siddhi, {"now": now, "history": history}),
            (self.check_gym, {"user_id": user_id, "now": now}),
            (self.check_goals, {"now": now}),
            (self.check_weekly_planning, {"now": now}),
            (self.check_p3_useful, {"now": now}),
        ]:
            try:
                result = check_fn(**kwargs)  # type: ignore[operator]
                all_items.extend(result)
            except Exception:  # noqa: BLE001
                pass

        return all_items

    # -- quiet hours --------------------------------------------------------

    @staticmethod
    def in_quiet_hours(
        now: datetime,
        *,
        start_ist: int | None = None,
        end_ist: int | None = None,
    ) -> bool:
        """Return True if *now* falls within the quiet window.

        Default: 01:00–08:00 IST (end is EXCLUSIVE — 08:00 is NOT quiet).
        Reads JACK_PROACTIVE_QUIET_START_IST and JACK_PROACTIVE_QUIET_END_IST.
        """
        if start_ist is None:
            start_ist = int(os.environ.get("JACK_PROACTIVE_QUIET_START_IST", "1"))
        if end_ist is None:
            end_ist = int(os.environ.get("JACK_PROACTIVE_QUIET_END_IST", "8"))

        h = now.astimezone(_IST).hour
        return start_ist <= h < end_ist

    # -- env hot-reload -----------------------------------------------------

    def _reload_proactive_flag(self) -> None:
        """Re-read ONLY JACK_PROACTIVE_ENABLED from ~/.hermes/.env.

        Does NOT clobber other env vars so a Discord toggle takes effect within
        one poll cycle without a full service restart.
        """
        env_path = Path.home() / ".hermes" / ".env"
        try:
            text = env_path.read_text(encoding="utf-8")
        except OSError:
            return
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key == "JACK_PROACTIVE_ENABLED":
                os.environ[key] = value.strip().strip('"').strip("'")
                return

    # -- decide & run_cycle -------------------------------------------------

    def decide(
        self,
        user_id: str,
        *,
        now: datetime | None = None,
        history: list[dict] | None = None,
    ) -> list[ProactiveItem]:
        """Return the items that *should* be sent this cycle (does not send).

        1. Hot-reload the proactive flag.
        2. Quiet hours check → [].
        3. Master switch check → [].
        4. gather_all_items.
        5. Filter out recently-sent nudges.
        6. scorer.select with today's sent count.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        self._reload_proactive_flag()

        if self.in_quiet_hours(now):
            return []

        if not _truthy(os.environ.get("JACK_PROACTIVE_ENABLED", "1")):
            return []

        candidates = self.gather_all_items(user_id, now=now, history=history)
        filtered = [
            item for item in candidates
            if not self.already_sent_recently(item.nudge_type, now=now)
        ]

        scorer = self._get_scorer()
        return scorer.select(filtered, sent_today=self.sent_today_count(now=now))

    def run_cycle(
        self,
        user_id: str,
        *,
        now: datetime | None = None,
        history: list[dict] | None = None,
    ) -> int:
        """Decide, render, send, and log. Returns count successfully sent.

        If send_fn returns False the item is NOT logged so it stays retryable.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        if self.in_quiet_hours(now):
            return 0

        selected = self.decide(user_id, now=now, history=history)
        sent_count = 0
        max_per_day = self._get_scorer()._max

        scorer = self._get_scorer()
        for item in selected:
            if self.sent_today_count(now=now) >= max_per_day:
                break
            rendered = scorer.render(item)
            ok = self._send(rendered, user_id)
            if ok:
                self.log_sent(item, now=now)
                sent_count += 1

        return sent_count
