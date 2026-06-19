"""Google Calendar integration for Jack.

Auth model: service account + calendar sharing (NOT OAuth, NOT domain delegation).
The calendar must be shared with the service account email; until that's done,
insert/list calls return a 403 which _service() catches and treats as "not connected".

All Google libs are imported lazily inside methods so the test suite and lint
run without google-api-python-client installed, and every public method degrades
gracefully (returns None/[] and never raises into the caller).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

# IST as a fixed-offset timezone — no external tz lib required.
_IST = timezone(timedelta(hours=5, minutes=30))
_IST_NAME = "Asia/Kolkata"
_DEFAULT_CREDS_PATH = os.path.expanduser("~/.hermes/credentials.json")
_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _now_ist() -> datetime:
    """Current time as a timezone-aware IST datetime."""
    return datetime.now(_IST)


def _to_rfc3339(dt: datetime) -> str:
    """Render *dt* as an RFC 3339 string with +05:30 suffix.

    If *dt* is naive it is assumed to already be in IST.
    If it carries a different tzinfo it is converted to IST first.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_IST)
    elif dt.tzinfo != _IST:
        dt = dt.astimezone(_IST)
    # strftime %z gives +0530; we need +05:30
    offset = "+05:30"
    return dt.strftime(f"%Y-%m-%dT%H:%M:%S{offset}")


def _day_bounds_ist(day: datetime | None = None) -> tuple[str, str]:
    """Return (timeMin, timeMax) RFC 3339 strings bracketing *day* in IST."""
    if day is None:
        day = _now_ist()
    elif day.tzinfo is None:
        day = day.replace(tzinfo=_IST)
    else:
        day = day.astimezone(_IST)

    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return _to_rfc3339(start), _to_rfc3339(end)


class CalendarClient:
    """Thin wrapper around the Google Calendar v3 API for Jack.

    Public methods return None (or []) on ANY failure and never raise into
    the caller — missing creds, ImportError, HttpError, network error, etc.
    """

    def __init__(
        self,
        calendar_id: str | None = None,
        creds_path: str | None = None,
    ) -> None:
        self.calendar_id: str | None = calendar_id or os.environ.get("JACK_CALENDAR_ID")
        self.creds_path: str = creds_path or _DEFAULT_CREDS_PATH
        self._svc = None  # populated lazily by _service()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _service(self):
        """Return a built googleapiclient service or None on any failure.

        Cached on self after first successful build so multiple calls are cheap.
        """
        if self._svc is not None:
            return self._svc

        # Fail fast if the creds file doesn't exist — avoids a confusing
        # FileNotFoundError deep inside google-auth.
        if not os.path.isfile(self.creds_path):
            return None

        try:
            # Lazy imports — google libs are NOT required to be installed.
            from google.oauth2.service_account import Credentials  # type: ignore
            from googleapiclient.discovery import build  # type: ignore

            creds = Credentials.from_service_account_file(self.creds_path, scopes=_SCOPES)
            svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
            self._svc = svc
            return svc
        except ImportError:
            return None
        except Exception:  # noqa: BLE001 — any build failure → not connected
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_event(
        self,
        summary: str,
        start: datetime,
        end: datetime | None = None,
        *,
        description: str | None = None,
    ) -> dict | None:
        """Insert a new event and return a slim result dict, or None on failure.

        *start* / *end* are expected to be IST-aware datetimes (or naive,
        treated as IST). Default duration when *end* is omitted is 1 hour.
        """
        svc = self._service()
        if svc is None or not self.calendar_id:
            return None

        # Normalise to IST-aware datetimes.
        start = start.replace(tzinfo=_IST) if start.tzinfo is None else start.astimezone(_IST)

        if end is None:
            end = start + timedelta(hours=1)
        elif end.tzinfo is None:
            end = end.replace(tzinfo=_IST)
        else:
            end = end.astimezone(_IST)

        body: dict = {
            "summary": summary,
            "start": {"dateTime": _to_rfc3339(start), "timeZone": _IST_NAME},
            "end": {"dateTime": _to_rfc3339(end), "timeZone": _IST_NAME},
        }
        if description:
            body["description"] = description

        try:
            result = svc.events().insert(calendarId=self.calendar_id, body=body).execute()
            return {
                "id": result.get("id"),
                "htmlLink": result.get("htmlLink"),
                "summary": result.get("summary"),
                "start": result.get("start"),
            }
        except Exception:  # noqa: BLE001 — HttpError, network error, 403, etc.
            return None

    def list_events(
        self,
        day: datetime | None = None,
        max_results: int = 10,
    ) -> list[dict] | None:
        """Return events for *day* (default today IST), or None on failure.

        An empty calendar returns [] (not None).
        """
        svc = self._service()
        if svc is None or not self.calendar_id:
            return None

        time_min, time_max = _day_bounds_ist(day)

        try:
            response = (
                svc.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            items = response.get("items", [])
            return [
                {
                    "summary": ev.get("summary", "(no title)"),
                    "start": ev.get("start", {}),
                    "end": ev.get("end", {}),
                }
                for ev in items
            ]
        except Exception:  # noqa: BLE001
            return None

    def events_summary_text(self, day: datetime | None = None) -> str:
        """Return a short multi-line string of today's events, or '' on failure/empty.

        Never raises.
        """
        try:
            events = self.list_events(day=day)
            if not events:
                return ""
            lines = []
            for ev in events:
                title = ev.get("summary", "(no title)")
                start_info = ev.get("start", {})
                time_str = start_info.get("dateTime") or start_info.get("date") or ""
                # Trim to HH:MM portion of the RFC 3339 string for readability.
                if "T" in time_str:
                    time_str = time_str.split("T")[1][:5]
                lines.append(f"{time_str} {title}" if time_str else title)
            return "\n".join(lines)
        except Exception:  # noqa: BLE001
            return ""
