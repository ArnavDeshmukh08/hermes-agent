"""Tests for integrations.orsa.OrsaClient — offline, against a temp SQLite DB.

Builds a throwaway DB mirroring the real Orsa `lead` schema, so these exercise the
actual SQL (read-only URI, date windows) end-to-end without touching the real CRM.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from integrations.orsa import OrsaClient  # noqa: E402


def _make_db(path: Path, rows: list[tuple]) -> None:
    """Create a minimal `lead` table and insert (name, email, city, score, created_at)."""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE lead (
            id INTEGER PRIMARY KEY,
            workspace_id INTEGER NOT NULL DEFAULT 1,
            name TEXT NOT NULL,
            niche TEXT DEFAULT 'med spa',
            city TEXT,
            phone TEXT,
            email TEXT,
            website TEXT,
            owner_name TEXT,
            rating REAL,
            review_count INTEGER,
            score INTEGER NOT NULL DEFAULT 0,
            qualified INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'new',
            source TEXT NOT NULL DEFAULT 'scrape',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.executemany(
        "INSERT INTO lead (name, email, city, score, created_at) VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def _utc(days_ago: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


@pytest.fixture()
def orsa_db(tmp_path) -> Path:
    db = tmp_path / "orsa.db"
    _make_db(
        db,
        [
            ("Fresh Clinic", "fresh@clinic.com", "Austin TX", 80, _utc(1)),
            ("Two Day Old", "two@day.com", "Austin TX", 60, _utc(2)),
            ("Old Lead", "old@lead.com", "Dallas TX", 40, _utc(30)),
        ],
    )
    return db


class TestConnection:
    def test_missing_db_returns_none_not_raise(self):
        client = OrsaClient(path="/nonexistent/orsa.db")
        assert client.is_connected() is False
        assert client.lead_count() is None
        assert client.new_leads() is None
        assert client.new_leads_summary_text() == ""

    def test_connected(self, orsa_db):
        assert OrsaClient(path=str(orsa_db)).is_connected() is True


class TestReads:
    def test_lead_count(self, orsa_db):
        assert OrsaClient(path=str(orsa_db)).lead_count() == 3

    def test_new_leads_this_week_excludes_old(self, orsa_db):
        leads = OrsaClient(path=str(orsa_db)).new_leads(since_days=7)
        assert leads is not None
        names = {lead["name"] for lead in leads}
        assert names == {"Fresh Clinic", "Two Day Old"}  # 30-day-old excluded

    def test_pipeline_stats_aggregates(self, orsa_db):
        stats = OrsaClient(path=str(orsa_db)).pipeline_stats()
        assert stats is not None
        assert stats["total"] >= 1
        assert set(stats["buckets"]) == {"hot", "warm", "cold"}
        assert isinstance(stats["top"], list)
        assert "avg_score" in stats

    def test_new_leads_count_is_unbounded(self, orsa_db):
        # Exact count must reflect the date window regardless of any row limit.
        assert OrsaClient(path=str(orsa_db)).new_leads_count(since_days=7) == 2

    def test_new_leads_ordered_newest_first(self, orsa_db):
        leads = OrsaClient(path=str(orsa_db)).new_leads(since_days=7)
        assert [lead["name"] for lead in leads] == ["Fresh Clinic", "Two Day Old"]

    def test_find_by_email_case_insensitive(self, orsa_db):
        lead = OrsaClient(path=str(orsa_db)).find_by_email("FRESH@CLINIC.COM")
        assert lead is not None
        assert lead["name"] == "Fresh Clinic"

    def test_find_by_email_absent(self, orsa_db):
        assert OrsaClient(path=str(orsa_db)).find_by_email("nobody@nowhere.com") is None

    def test_known_emails(self, orsa_db):
        emails = OrsaClient(path=str(orsa_db)).known_emails()
        assert emails == {"fresh@clinic.com", "two@day.com", "old@lead.com"}

    def test_summary_text_mentions_new_lead(self, orsa_db):
        text = OrsaClient(path=str(orsa_db)).new_leads_summary_text(since_days=7)
        assert "Fresh Clinic" in text
        assert "Old Lead" not in text


class TestReadOnly:
    def test_open_mode_is_read_only(self, orsa_db):
        """A write through Jack's read-only connection must fail — proving mode=ro."""
        client = OrsaClient(path=str(orsa_db))
        conn = client._connect()
        assert conn is not None
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO lead (name, created_at) VALUES ('x','y')")
        conn.close()
