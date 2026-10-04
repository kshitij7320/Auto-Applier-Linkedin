import sqlite3
import json
import os
from typing import Optional, Dict, Any

class DatabaseTracker:
    def __init__(self, db_path: str = "storage/tracker.db"):
        self.db_path = db_path
        parent_dir = os.path.dirname(self.db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS applied_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT UNIQUE NOT NULL,
                    job_title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT NOT NULL,
                    error_reason TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS screening_qa_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question_hash TEXT UNIQUE NOT NULL,
                    question_text TEXT NOT NULL,
                    input_type TEXT NOT NULL,
                    solution_json TEXT NOT NULL,
                    resolved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def is_already_applied(self, job_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM applied_jobs WHERE job_id = ? AND status = 'APPLIED'", (job_id,))
            return cursor.fetchone() is not None

    def record_application(
        self,
        job_id: str,
        title: str,
        company: str,
        location: str,
        status: str,
        error_reason: Optional[str] = None
    ) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO applied_jobs (job_id, job_title, company, location, status, error_reason)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status=excluded.status,
                    error_reason=excluded.error_reason,
                    applied_at=CURRENT_TIMESTAMP
            """, (job_id, title, company, location, status, error_reason))
            conn.commit()

    def get_cached_answer(self, question_hash: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT solution_json FROM screening_qa_cache WHERE question_hash = ?", (question_hash,))
            row = cursor.fetchone()
            if row:
                return json.loads(row["solution_json"])
            return None

    def cache_answer(self, question_hash: str, question_text: str, input_type: str, solution_json: str) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO screening_qa_cache (question_hash, question_text, input_type, solution_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(question_hash) DO UPDATE SET
                    solution_json=excluded.solution_json,
                    resolved_at=CURRENT_TIMESTAMP
            """, (question_hash, question_text, input_type, solution_json))
            conn.commit()

    def get_session_applied_count(self) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS cnt FROM applied_jobs WHERE status = 'APPLIED'")
            row = cursor.fetchone()
            return row["cnt"] if row else 0
