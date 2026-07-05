"""Orsa CRM integration for Jack — READ-ONLY access to the local SQLite DB.

Orsa is Arnav's lead/CRM app (the "leadkiln" project). Its backend owns and writes
the SQLite file; Jack only ever reads it. The DB is opened with a `mode=ro` URI so
Jack physically cannot write or corrupt it, but NOT `immutable=1` — Jack must see the
backend's live writes so answers reflect current data, not a stale snapshot.

Because Jack never writes here, no flock is needed (the write-side convention lives in
the leadkiln backend).

Design rules (mirror integrations/__init__.py):
    - Read-only, live, never raises into the caller (returns None/[]/0 on failure).
    - DB path is env-overridable (JACK_ORSA_DB_PATH); no hardcoded absolute path leaks
      into logs beyond a single startup-time debug line.
"""

from __future__ import annotations

import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

# Discovered location on Arnav's Mac. Override with JACK_ORSA_DB_PATH.
_DEFAULT_DB_PATH = os.path.expanduser(
    "~/Documents/leadkiln/backend/orsa.db"
)

# Columns surfaced to Jack — a slim, stable projection of the `lead` table.
_LEAD_COLUMNS = (
    "id, name, niche, city, phone, email, website, owner_name, "
    "rating, review_count, score, status, source, created_at"
)


def db_path() -> str:
    """Resolved path to the Orsa SQLite DB (env-overridable)."""
    return os.environ.get("JACK_ORSA_DB_PATH", _DEFAULT_DB_PATH)


class OrsaClient:
    """Read-only accessor for the Orsa CRM SQLite database.

    Every method degrades to None/[]/0 when the DB is missing or a query fails.
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = path or db_path()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection | None:
        """Open a read-only connection to the DB, or None if it doesn't exist/opens fail."""
        if not os.path.isfile(self._path):
            return None
        try:
            # Read-only URI: Jack can never mutate Orsa's data.
            conn = sqlite3.connect(
                f"file:{self._path}?mode=ro",
                uri=True,
                timeout=5.0,
            )
            conn.row_factory = sqlite3.Row
            return conn
        except Exception:  # noqa: BLE001
            logger.warning("orsa: could not open DB read-only")
            return None

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row] | None:
        conn = self._connect()
        if conn is None:
            return None
        try:
            cur = conn.execute(sql, params)
            return cur.fetchall()
        except Exception:  # noqa: BLE001
            logger.warning("orsa: query failed")
            return None
        finally:
            conn.close()

    def is_connected(self) -> bool:
        """True if the DB file exists and opens read-only."""
        conn = self._connect()
        if conn is None:
            return False
        conn.close()
        return True

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    def lead_count(self) -> int | None:
        """Total number of leads, or None on failure."""
        rows = self._query("SELECT COUNT(*) AS n FROM lead")
        if rows is None:
            return None
        return int(rows[0]["n"])

    def new_leads_count(self, since_days: int = 7) -> int | None:
        """Exact count of leads created within the last *since_days* days (unbounded).

        Use this for "how many" answers — new_leads() is row-limited and would
        undercount, and Jack must never state a number he can't stand behind.
        """
        rows = self._query(
            "SELECT COUNT(*) AS n FROM lead WHERE created_at >= datetime('now', ?)",
            (f"-{int(since_days)} days",),
        )
        if rows is None:
            return None
        return int(rows[0]["n"])

    def new_leads(self, since_days: int = 7, limit: int = 200) -> list[dict] | None:
        """Leads created within the last *since_days* days, newest first, or None.

        Orsa stores `created_at` as a UTC datetime string; the DB comparison is done
        in SQL against `datetime('now', ...)` (also UTC) so no tz math is needed here.
        """
        sql = (
            f"SELECT {_LEAD_COLUMNS} FROM lead "
            "WHERE created_at >= datetime('now', ?) "
            "ORDER BY created_at DESC LIMIT ?"
        )
        rows = self._query(sql, (f"-{int(since_days)} days", int(limit)))
        if rows is None:
            return None
        return [dict(r) for r in rows]

    def recent_leads(self, limit: int = 10) -> list[dict] | None:
        """The *limit* most recently created leads regardless of age, or None."""
        sql = f"SELECT {_LEAD_COLUMNS} FROM lead ORDER BY created_at DESC LIMIT ?"
        rows = self._query(sql, (int(limit),))
        if rows is None:
            return None
        return [dict(r) for r in rows]

    def find_by_email(self, email: str) -> dict | None:
        """Return the lead whose email matches *email* (case-insensitive), or None.

        None means "not found OR DB unavailable" — callers that must distinguish should
        check is_connected() first.
        """
        if not email:
            return None
        sql = f"SELECT {_LEAD_COLUMNS} FROM lead WHERE lower(email) = lower(?) LIMIT 1"
        rows = self._query(sql, (email.strip(),))
        if not rows:
            return None
        return dict(rows[0])

    def find_by_name(self, name: str) -> list[dict] | None:
        """Return leads whose name contains *name* (case-insensitive), or None."""
        if not name:
            return None
        sql = f"SELECT {_LEAD_COLUMNS} FROM lead WHERE name LIKE ? ORDER BY score DESC LIMIT 10"
        rows = self._query(sql, (f"%{name.strip()}%",))
        if rows is None:
            return None
        return [dict(r) for r in rows]

    def new_leads_summary_text(self, since_days: int = 7) -> str:
        """Human summary of this week's new leads, or '' on failure/empty.

        Shows the first 10 by recency and uses the EXACT total for the "…and N more"
        tail so the number is never a row-limit artefact.
        """
        try:
            leads = self.new_leads(since_days=since_days)
            if not leads:
                return ""
            total = self.new_leads_count(since_days=since_days)
            total = total if total is not None else len(leads)
            lines = []
            for lead in leads[:10]:
                city = f" ({lead['city']})" if lead.get("city") else ""
                score = f" · score {lead['score']}" if lead.get("score") else ""
                lines.append(f"• {lead['name']}{city}{score}")
            more = total - min(10, len(leads))
            if more > 0:
                lines.append(f"…and {more} more")
            return "\n".join(lines)
        except Exception:  # noqa: BLE001
            return ""

    def pipeline_stats(self) -> dict | None:
        """Aggregate CRM stats for the dashboard, or None on failure.

        Returns {total, new_7d, qualified, avg_score, buckets:{hot,warm,cold},
        cities:[(city,count)…], top:[lead…]}. Score buckets: hot>=70, warm 50–69, cold<50.
        """
        conn = self._connect()
        if conn is None:
            return None
        try:
            total = conn.execute("SELECT COUNT(*) FROM lead").fetchone()[0]
            new_7d = conn.execute(
                "SELECT COUNT(*) FROM lead WHERE created_at >= datetime('now','-7 days')"
            ).fetchone()[0]
            qualified = conn.execute("SELECT COUNT(*) FROM lead WHERE qualified = 1").fetchone()[0]
            avg_score = conn.execute("SELECT AVG(score) FROM lead").fetchone()[0] or 0
            hot = conn.execute("SELECT COUNT(*) FROM lead WHERE score >= 70").fetchone()[0]
            warm = conn.execute("SELECT COUNT(*) FROM lead WHERE score >= 50 AND score < 70").fetchone()[0]
            cold = conn.execute("SELECT COUNT(*) FROM lead WHERE score < 50").fetchone()[0]
            cities = conn.execute(
                "SELECT city, COUNT(*) AS n FROM lead GROUP BY city ORDER BY n DESC LIMIT 5"
            ).fetchall()
            top = conn.execute(
                f"SELECT {_LEAD_COLUMNS} FROM lead ORDER BY score DESC, created_at DESC LIMIT 6"
            ).fetchall()
            return {
                "total": int(total),
                "new_7d": int(new_7d),
                "qualified": int(qualified),
                "avg_score": round(float(avg_score), 1),
                "buckets": {"hot": int(hot), "warm": int(warm), "cold": int(cold)},
                "cities": [{"city": r["city"], "count": int(r["n"])} for r in cities],
                "top": [dict(r) for r in top],
            }
        except Exception:  # noqa: BLE001
            logger.warning("orsa: pipeline_stats query failed")
            return None
        finally:
            conn.close()

    def known_emails(self) -> set[str] | None:
        """Set of lowercased non-empty lead emails (for contact reconciliation), or None."""
        rows = self._query("SELECT email FROM lead WHERE email IS NOT NULL AND email != ''")
        if rows is None:
            return None
        return {str(r["email"]).strip().lower() for r in rows if r["email"]}
