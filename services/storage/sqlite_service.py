import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from core.config.settings import settings


class SQLiteService:
    def __init__(self, path: str = None):
        self.path = Path(path or settings.SQLITE_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def connect(self):
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def execute(self, sql: str, params: tuple = ()):
        with self._lock, self.connect() as conn:
            conn.execute(sql, params)
            conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def execute_many(self, statements: list[tuple[str, tuple]]):
        with self._lock, self.connect() as conn:
            for sql, params in statements:
                conn.execute(sql, params)
            conn.commit()

    @staticmethod
    def dumps(value: Any) -> str:
        return json.dumps(value)

    @staticmethod
    def loads(value: str | None, fallback: Any = None) -> Any:
        if not value:
            return fallback
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback

    def _init(self):
        self.execute_many([
            ("""
            CREATE TABLE IF NOT EXISTS memory_messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              timestamp TEXT NOT NULL
            )
            """, ()),
            ("CREATE INDEX IF NOT EXISTS idx_memory_session ON memory_messages(session_id, id)", ()),
            ("""
            CREATE TABLE IF NOT EXISTS memory_facts (
              id TEXT PRIMARY KEY,
              content TEXT NOT NULL,
              timestamp TEXT NOT NULL
            )
            """, ()),
            ("""
            CREATE TABLE IF NOT EXISTS model_settings (
              task TEXT PRIMARY KEY,
              model TEXT NOT NULL
            )
            """, ()),
            ("""
            CREATE TABLE IF NOT EXISTS chat_conversations (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL
            )
            """, ()),
            ("""
            CREATE TABLE IF NOT EXISTS chat_messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              conversation_id TEXT NOT NULL,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              route TEXT,
              model TEXT,
              sources_json TEXT,
              chart_json TEXT,
              sql_text TEXT,
              rows_json TEXT,
              created_at REAL NOT NULL,
              FOREIGN KEY(conversation_id) REFERENCES chat_conversations(id) ON DELETE CASCADE
            )
            """, ()),
            ("CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation ON chat_messages(conversation_id, id)", ()),
            ("""
            CREATE TABLE IF NOT EXISTS career_preferences (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              preferences_json TEXT NOT NULL,
              updated_at REAL NOT NULL
            )
            """, ()),
            ("""
            CREATE TABLE IF NOT EXISTS career_profile (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              cv_text TEXT NOT NULL,
              updated_at REAL NOT NULL
            )
            """, ()),
            ("""
            CREATE TABLE IF NOT EXISTS career_jobs (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              company TEXT,
              location TEXT,
              url TEXT,
              description TEXT NOT NULL,
              source TEXT NOT NULL,
              status TEXT NOT NULL,
              fit_score INTEGER,
              decision TEXT,
              analysis_json TEXT,
              applied_at REAL,
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL
            )
            """, ()),
            ("CREATE INDEX IF NOT EXISTS idx_career_jobs_updated ON career_jobs(updated_at DESC)", ()),
            ("""
            CREATE TABLE IF NOT EXISTS career_score_batches (
              id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              cv_text TEXT NOT NULL,
              total INTEGER NOT NULL DEFAULT 0,
              completed INTEGER NOT NULL DEFAULT 0,
              failed INTEGER NOT NULL DEFAULT 0,
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL
            )
            """, ()),
            ("""
            CREATE TABLE IF NOT EXISTS career_score_tasks (
              batch_id TEXT NOT NULL,
              job_id TEXT NOT NULL,
              status TEXT NOT NULL,
              error TEXT,
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL,
              PRIMARY KEY (batch_id, job_id),
              FOREIGN KEY(batch_id) REFERENCES career_score_batches(id) ON DELETE CASCADE,
              FOREIGN KEY(job_id) REFERENCES career_jobs(id) ON DELETE CASCADE
            )
            """, ()),
            ("CREATE INDEX IF NOT EXISTS idx_career_score_tasks_status ON career_score_tasks(status, created_at)", ()),
        ])
        self._ensure_column("career_jobs", "applied_at", "REAL")

    def _ensure_column(self, table: str, column: str, definition: str):
        existing = {row["name"] for row in self.query(f"PRAGMA table_info({table})")}
        if column not in existing:
            self.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


db = SQLiteService()
