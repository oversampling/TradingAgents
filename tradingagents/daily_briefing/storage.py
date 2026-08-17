"""Durable run history and idempotency for the daily job."""

import json
import sqlite3
from pathlib import Path

from .models import SymbolResult


class DailyStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()

    def close(self):
        self.connection.close()

    def _create_schema(self):
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS daily_runs (
              id INTEGER PRIMARY KEY, session_key TEXT NOT NULL UNIQUE,
              started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL,
              us_session_date TEXT, my_session_date TEXT, watchlist_group TEXT NOT NULL,
              symbol_count INTEGER NOT NULL DEFAULT 0, success_count INTEGER NOT NULL DEFAULT 0,
              failure_count INTEGER NOT NULL DEFAULT 0, report_html_path TEXT,
              report_json_path TEXT, email_sent_at TEXT, error_summary TEXT
            );
            CREATE TABLE IF NOT EXISTS daily_symbol_results (
              id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES daily_runs(id),
              normalized_ticker TEXT NOT NULL, payload_json TEXT NOT NULL,
              UNIQUE(run_id, normalized_ticker)
            );
            """
        )
        self.connection.commit()

    def completed_run(self, session_key: str):
        return self.connection.execute(
            "SELECT * FROM daily_runs WHERE session_key=? AND status IN ('completed','partial','report_ready_email_failed')",
            (session_key,),
        ).fetchone()

    def market_session_processed(self, market: str, session_date: str | None, group: str) -> bool:
        if not session_date:
            return False
        column = "us_session_date" if market == "US" else "my_session_date"
        return self.connection.execute(
            f"SELECT 1 FROM daily_runs WHERE {column}=? AND watchlist_group=? AND status IN ('completed','partial','report_ready_email_failed') LIMIT 1",
            (session_date, group),
        ).fetchone() is not None

    def start_run(self, session_key: str, now: str, group: str, us_date: str | None, my_date: str | None) -> int:
        existing = self.connection.execute(
            "SELECT id FROM daily_runs WHERE session_key=?", (session_key,)
        ).fetchone()
        if existing:
            run_id = int(existing["id"])
            self.connection.execute("DELETE FROM daily_symbol_results WHERE run_id=?", (run_id,))
            self.connection.execute(
                "UPDATE daily_runs SET started_at=?, completed_at=NULL, status='running', us_session_date=?, my_session_date=?, watchlist_group=?, symbol_count=0, success_count=0, failure_count=0, report_html_path=NULL, report_json_path=NULL, email_sent_at=NULL, error_summary=NULL WHERE id=?",
                (now, us_date, my_date, group, run_id),
            )
            self.connection.commit()
            return run_id
        cursor = self.connection.execute(
            "INSERT INTO daily_runs (session_key, started_at, status, us_session_date, my_session_date, watchlist_group) VALUES (?, ?, 'running', ?, ?, ?)",
            (session_key, now, us_date, my_date, group),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def add_result(self, run_id: int, result: SymbolResult):
        self.connection.execute(
            "INSERT OR REPLACE INTO daily_symbol_results (run_id, normalized_ticker, payload_json) VALUES (?, ?, ?)",
            (run_id, result.symbol.ticker, json.dumps(result.to_dict(), ensure_ascii=False)),
        )
        self.connection.commit()

    def finish(self, run_id: int, *, now: str, status: str, results: list[SymbolResult], html_path: Path | None = None, json_path: Path | None = None, error: str = ""):
        successes = sum(item.status == "success" for item in results)
        failures = sum(item.status == "failed" for item in results)
        self.connection.execute(
            "UPDATE daily_runs SET completed_at=?, status=?, symbol_count=?, success_count=?, failure_count=?, report_html_path=?, report_json_path=?, error_summary=? WHERE id=?",
            (now, status, len(results), successes, failures, str(html_path or ""), str(json_path or ""), error, run_id),
        )
        self.connection.commit()

    def mark_email_sent(self, run_id: int, now: str):
        self.connection.execute(
            "UPDATE daily_runs SET email_sent_at=?, status=CASE WHEN status='failed' THEN 'completed' ELSE status END WHERE id=?",
            (now, run_id),
        )
        self.connection.commit()

    def run(self, run_id: int):
        return self.connection.execute("SELECT * FROM daily_runs WHERE id=?", (run_id,)).fetchone()
