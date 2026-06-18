"""Dynamic-column spreadsheet writer (LEAD-ENGINE §2c).

The columns are whatever the spec asked for — no per-field code. Provenance
columns (`source_url`, `fetched_at`) are ALWAYS appended, and when outreach is on
the `pitch`/`status` columns follow. Values are written verbatim
(`value_input_option="RAW"`) which neutralizes spreadsheet formula-injection
from scraped pages AND stops Sheets mangling `+91-...` phone numbers — replacing
the whole `_guard` layer with one setting.

Backend: Google Sheets via gspread when GOOGLE creds + HERMES_SHEET_ID are
present; otherwise a CSV fallback at out/<tab>.csv (openable in any spreadsheet),
so the pipeline is verifiable offline.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from .specs import column_role

PROV_COLS = ["source_url", "fetched_at"]
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _pitch_in_columns(columns) -> bool:
    return any(column_role(c) == "pitch" for c in columns)


def _csv_safe(value: str) -> str:
    """Neutralize spreadsheet formula-injection from scraped text. gspread RAW
    protects the Sheets path, but a CSV opened in Excel/LibreOffice/Sheets-import
    would still execute a leading =/+/-/@ — so guard every cell for both backends."""
    if value and value[0] in _FORMULA_PREFIXES:
        return "'" + value  # leading apostrophe -> forced text, not displayed
    return value


def header_for(columns, outreach: bool) -> list[str]:
    h = list(columns) + PROV_COLS
    # add a generic "pitch" column only if the user didn't already name one
    if outreach and not _pitch_in_columns(columns):
        h += ["pitch"]
    if outreach:
        h += ["status"]
    return h


def lead_to_row(lead: dict, header, outreach: bool = False) -> list[str]:
    """Emit a row in `header` order, reading values straight from the lead dict.
    The pitch already lives in the lead (under its column label or "pitch"); the
    only injected value is the outreach status. str() keeps phones as text."""
    row = []
    for col in header:
        if col == "status" and outreach:
            row.append("PENDING REVIEW")
        else:
            row.append(str(lead.get(col) or ""))
    return [_csv_safe(cell) for cell in row]


class SheetWriter:
    """Writes a header (once) + aligned rows. RAW input option throughout."""

    def __init__(self, tab: str, columns, outreach: bool):
        self.tab = tab
        self.columns = list(columns)
        self.outreach = outreach
        self.header = header_for(columns, outreach)
        self._sheet_id = os.environ.get("HERMES_SHEET_ID")
        self._use_gspread = bool(self._sheet_id) and self._gspread_available()

    @staticmethod
    def _gspread_available() -> bool:
        try:
            import gspread  # noqa: F401
        except ImportError:
            return False
        return bool(
            os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
            or os.environ.get("HERMES_GOOGLE_CREDS")
        )

    def write(self, leads: list[dict]) -> dict:
        """Write all rows in header order (pitch already lives in each lead).
        Returns a small manifest describing where the data landed."""
        rows = [lead_to_row(lead, self.header, self.outreach) for lead in leads]

        if self._use_gspread:
            return self._write_gspread(rows)
        return self._write_csv(rows)

    # -- backends -----------------------------------------------------------
    def _write_gspread(self, rows) -> dict:
        import gspread

        creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.environ.get("HERMES_GOOGLE_CREDS")
        gc = gspread.service_account(filename=creds)
        sh = gc.open_by_key(self._sheet_id)
        try:
            ws = sh.worksheet(self.tab)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=self.tab, rows=1000, cols=max(20, len(self.header)))
        first_write = not ws.row_values(1)
        if first_write:
            ws.append_row(self.header, value_input_option="RAW")
        if rows:
            ws.append_rows(rows, value_input_option="RAW")
        return {"backend": "gspread", "tab": self.tab, "rows": len(rows), "header": self.header}

    def _write_csv(self, rows) -> dict:
        out_dir = Path(os.environ.get("HERMES_OUT_DIR") or "out").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = (out_dir / f"{self.tab}.csv").resolve()
        if out_dir not in path.parents:  # defense-in-depth vs a crafted tab name
            raise ValueError(f"sheet_tab resolves outside out_dir: {path}")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(self.header)
            w.writerows(rows)
        return {"backend": "csv", "path": str(path), "tab": self.tab, "rows": len(rows), "header": self.header}
