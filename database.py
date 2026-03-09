"""
database.py — SQLite database for tracking analyses and caching results.
"""

import sqlite3
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import config

logger = logging.getLogger(__name__)

DB_PATH = str(config.DB_PATH)


class Database:
    """Manages analysis records and cached results."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")   # better concurrency
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_tables(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analyses (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_name    TEXT NOT NULL,
                    company_query   TEXT NOT NULL,
                    pdf_path        TEXT,
                    report_title    TEXT,
                    result_json     TEXT,
                    status          TEXT DEFAULT 'pending',
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_company ON analyses(company_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status  ON analyses(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON analyses(created_at)")
            conn.commit()

    # ── Writes ────────────────────────────────────────────────────────────────

    def save_analysis(
        self,
        company_name: str,
        company_query: str,
        pdf_path: str,
        report_title: str,
        result: dict,
        status: str = "complete",
    ) -> int:
        """Save a completed analysis. Returns the new row id."""
        now = datetime.now(timezone.utc).isoformat()
        result_json = json.dumps(result, ensure_ascii=False) if result else None
        with self._get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO analyses
                    (company_name, company_query, pdf_path, report_title,
                     result_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (company_name, company_query, pdf_path, report_title,
                 result_json, status, now, now),
            )
            conn.commit()
        row_id = cur.lastrowid
        logger.info("Saved analysis id=%s for %s", row_id, company_name)
        return row_id

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get_history(self, limit: int = 20) -> list[dict]:
        """Get recent analyses (summary only, no result JSON)."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, company_name, report_title, status, created_at
                FROM analyses
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_analysis(self, analysis_id: int) -> dict | None:
        """Get a specific analysis by id, inflating the result JSON."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM analyses WHERE id = ?",
                (analysis_id,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        if data.get("result_json"):
            data["result"] = json.loads(data["result_json"])
        return data

    def get_latest_for_company(self, company_name: str) -> dict | None:
        """Get the most recent completed analysis for a given company."""
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM analyses
                WHERE company_name = ? AND status = 'complete'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (company_name,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        if data.get("result_json"):
            data["result"] = json.loads(data["result_json"])
        return data

    def get_stats(self) -> dict:
        """Return aggregate statistics for the dashboard."""
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
            complete = conn.execute(
                "SELECT COUNT(*) FROM analyses WHERE status='complete'"
            ).fetchone()[0]
            unique = conn.execute(
                "SELECT COUNT(DISTINCT company_name) FROM analyses"
            ).fetchone()[0]
        return {"total": total, "complete": complete, "unique_companies": unique}
