"""Tests for integrations.calendar.CalendarClient + calendar intent routing.

All tests are offline — zero real network calls. Google API calls are replaced
with lightweight FakeService stubs. The test suite passes whether or not
google-api-python-client is installed.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path setup — mirror test_memory_updater.py's pattern
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "hooks" / "jack_router"))

import jack_intent_router as router  # noqa: E402

from integrations.calendar import CalendarClient  # noqa: E402

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------
_IST = timezone(timedelta(hours=5, minutes=30))
_CAL_ID = "test_calendar_id@group.calendar.google.com"


# ---------------------------------------------------------------------------
# Helper: build a FakeService that captures or returns preset data
# ---------------------------------------------------------------------------

class _FakeExecute:
    """Mimics a resource that has an .execute() method."""

    def __init__(self, return_value=None, side_effect=None):
        self._return_value = return_value
        self._side_effect = side_effect

    def execute(self):
        if self._side_effect is not None:
            raise self._side_effect
        return self._return_value


class _FakeEvents:
    """Captures insert()/list() calls and returns preset executors."""

    def __init__(self, insert_return=None, insert_side_effect=None, list_return=None):
        self._insert_return = insert_return or {"id": "evt123", "htmlLink": "https://cal.example/evt123", "summary": "captured"}
        self._insert_side_effect = insert_side_effect
        self._list_return = list_return or {"items": []}
        self.last_insert_calendar_id: str | None = None
        self.last_insert_body: dict | None = None

    def insert(self, calendarId, body):
        self.last_insert_calendar_id = calendarId
        self.last_insert_body = body
        return _FakeExecute(self._insert_return, self._insert_side_effect)

    def list(self, **kwargs):  # noqa: A003
        return _FakeExecute(self._list_return)


class _FakeService:
    """Minimal googleapiclient service stub."""

    def __init__(self, **kwargs):
        self._events = _FakeEvents(**kwargs)

    def events(self):
        return self._events


# ---------------------------------------------------------------------------
# Test 1: missing creds file → _service() None; public methods return None/''
# ---------------------------------------------------------------------------

class TestNoCreds:
    def test_no_creds_file_returns_none(self, monkeypatch):
        monkeypatch.setenv("JACK_CALENDAR_ID", _CAL_ID)
        client = CalendarClient(creds_path="/nonexistent_creds_file.json")

        assert client._service() is None
        assert client.add_event("Dentist", datetime(2026, 6, 20, 14, 0, tzinfo=_IST)) is None
        assert client.list_events() is None
        assert client.events_summary_text() == ""


# ---------------------------------------------------------------------------
# Test 2: add_event builds a correct API body
# ---------------------------------------------------------------------------

class TestAddEvent:
    def test_add_event_builds_correct_body(self, monkeypatch):
        monkeypatch.setenv("JACK_CALENDAR_ID", _CAL_ID)
        fake_svc = _FakeService(
            insert_return={
                "id": "evt-dentist-001",
                "htmlLink": "https://calendar.google.com/dentist",
                "summary": "Dentist",
                "start": {"dateTime": "2026-06-20T14:00:00+05:30", "timeZone": "Asia/Kolkata"},
            }
        )
        client = CalendarClient(calendar_id=_CAL_ID, creds_path="/nonexistent.json")
        client._svc = fake_svc  # inject pre-built fake, bypass _service()

        start = datetime(2026, 6, 20, 14, 0, tzinfo=_IST)
        result = client.add_event("Dentist", start)

        ev = fake_svc.events()
        # calendarId passed correctly
        assert ev.last_insert_calendar_id == _CAL_ID

        body = ev.last_insert_body
        assert body is not None
        assert body["summary"] == "Dentist"

        # start.dateTime must be RFC 3339 with +05:30
        start_dt = body["start"]["dateTime"]
        assert "+05:30" in start_dt, f"Expected +05:30 in {start_dt!r}"
        assert "2026-06-20T14:00:00" in start_dt

        assert body["start"]["timeZone"] == "Asia/Kolkata"

        # default end = start + 1 hour
        end_dt = body["end"]["dateTime"]
        assert "2026-06-20T15:00:00" in end_dt

        # return dict has required keys
        assert result is not None
        assert "id" in result
        assert "htmlLink" in result


# ---------------------------------------------------------------------------
# Test 3: list_events parses items; events_summary_text mentions the title
# ---------------------------------------------------------------------------

class TestListEvents:
    def test_list_events_parses_items(self, monkeypatch):
        monkeypatch.setenv("JACK_CALENDAR_ID", _CAL_ID)
        fake_list_response = {
            "items": [
                {
                    "summary": "Standup",
                    "start": {"dateTime": "2026-06-20T09:00:00+05:30"},
                    "end": {"dateTime": "2026-06-20T09:30:00+05:30"},
                }
            ]
        }
        fake_svc = _FakeService(list_return=fake_list_response)
        client = CalendarClient(calendar_id=_CAL_ID, creds_path="/nonexistent.json")
        client._svc = fake_svc

        events = client.list_events()
        assert events is not None
        assert len(events) == 1
        assert events[0]["summary"] == "Standup"

        summary = client.events_summary_text()
        assert "Standup" in summary


# ---------------------------------------------------------------------------
# Test 4: API error (generic Exception stands in for HttpError) → None, no raise
# ---------------------------------------------------------------------------

class TestApiError:
    def test_api_error_returns_none(self, monkeypatch):
        monkeypatch.setenv("JACK_CALENDAR_ID", _CAL_ID)
        fake_svc = _FakeService(insert_side_effect=Exception("simulated 403 Forbidden"))
        client = CalendarClient(calendar_id=_CAL_ID, creds_path="/nonexistent.json")
        client._svc = fake_svc

        result = client.add_event("Meeting", datetime(2026, 6, 20, 10, 0, tzinfo=_IST))
        assert result is None  # must not raise


# ---------------------------------------------------------------------------
# Test 5: intent router — calendar add
# ---------------------------------------------------------------------------

class TestIntentCalendarAdd:
    def test_intent_classify_calendar_add(self):
        route = router.classify("add dentist appointment to my calendar tomorrow at 2pm")
        assert route is not None
        assert route.intent == "calendar"
        assert route.params.get("action") == "add"


# ---------------------------------------------------------------------------
# Test 6: intent router — calendar list
# ---------------------------------------------------------------------------

class TestIntentCalendarList:
    def test_intent_classify_calendar_list(self):
        route = router.classify("what's on my calendar today")
        assert route is not None
        assert route.intent == "calendar"
        assert route.params.get("action") == "list"


# ---------------------------------------------------------------------------
# Test 7: ordering guard — "schedule a meeting on my calendar" → calendar, NOT reminder
# ---------------------------------------------------------------------------

class TestIntentCalendarNotReminder:
    def test_intent_calendar_not_reminder(self):
        route = router.classify("schedule a meeting on my calendar")
        assert route is not None
        assert route.intent == "calendar", (
            f"Expected 'calendar' but got '{route.intent}' — "
            "calendar branch must precede reminder branch in classify()"
        )
