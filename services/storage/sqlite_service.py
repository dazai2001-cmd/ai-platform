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
            CREATE TABLE IF NOT EXISTS user_model_settings (
              user_id TEXT NOT NULL,
              task TEXT NOT NULL,
              model TEXT NOT NULL,
              PRIMARY KEY (user_id, task)
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
            CREATE TABLE IF NOT EXISTS auth_users (
              id TEXT PRIMARY KEY,
              email TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL,
              email_verified INTEGER NOT NULL DEFAULT 0,
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL
            )
            """, ()),
            ("""
            CREATE TABLE IF NOT EXISTS auth_email_tokens (
              token_hash TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              expires_at REAL NOT NULL,
              used_at REAL,
              created_at REAL NOT NULL,
              FOREIGN KEY(user_id) REFERENCES auth_users(id) ON DELETE CASCADE
            )
            """, ()),
            ("CREATE INDEX IF NOT EXISTS idx_auth_email_tokens_user ON auth_email_tokens(user_id)", ()),
            ("""
            CREATE TABLE IF NOT EXISTS auth_sessions (
              token_hash TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              created_at REAL NOT NULL,
              expires_at REAL NOT NULL,
              last_seen_at REAL NOT NULL,
              FOREIGN KEY(user_id) REFERENCES auth_users(id) ON DELETE CASCADE
            )
            """, ()),
            ("CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id)", ()),
            ("""
            CREATE TABLE IF NOT EXISTS career_preferences (
              user_id TEXT PRIMARY KEY,
              preferences_json TEXT NOT NULL,
              updated_at REAL NOT NULL
            )
            """, ()),
            ("""
            CREATE TABLE IF NOT EXISTS career_profile (
              user_id TEXT PRIMARY KEY,
              cv_text TEXT NOT NULL,
              updated_at REAL NOT NULL
            )
            """, ()),
            ("""
            CREATE TABLE IF NOT EXISTS career_jobs (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL DEFAULT 'local',
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
            ("""
            CREATE TABLE IF NOT EXISTS career_score_batches (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL DEFAULT 'local',
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
            ("""
            CREATE TABLE IF NOT EXISTS usage_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id TEXT NOT NULL,
              action TEXT NOT NULL,
              created_at REAL NOT NULL
            )
            """, ()),
            ("CREATE INDEX IF NOT EXISTS idx_usage_events_user_action_time ON usage_events(user_id, action, created_at)", ()),
        ])
        self._migrate_user_scopes()

    def _has_column(self, table: str, column: str) -> bool:
        rows = self.query(f"PRAGMA table_info({table})")
        return any(row["name"] == column for row in rows)

    def _add_column_if_missing(self, table: str, column: str, ddl: str):
        if not self._has_column(table, column):
            self.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _migrate_user_scopes(self):
        self._migrate_career_singleton_table("career_preferences", "preferences_json TEXT NOT NULL", "preferences_json", "{}")
        self._migrate_career_singleton_table("career_profile", "cv_text TEXT NOT NULL", "cv_text", "")
        self._add_column_if_missing("memory_messages", "user_id", "TEXT NOT NULL DEFAULT 'local'")
        self._add_column_if_missing("memory_facts", "user_id", "TEXT NOT NULL DEFAULT 'local'")
        self._add_column_if_missing("chat_conversations", "user_id", "TEXT NOT NULL DEFAULT 'local'")
        self._add_column_if_missing("career_jobs", "user_id", "TEXT NOT NULL DEFAULT 'local'")
        self._add_column_if_missing("career_jobs", "applied_at", "REAL")
        self._add_column_if_missing("career_score_batches", "user_id", "TEXT NOT NULL DEFAULT 'local'")
        self.execute("CREATE INDEX IF NOT EXISTS idx_memory_user_session ON memory_messages(user_id, session_id, id)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_memory_facts_user ON memory_facts(user_id, timestamp)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_chat_conversations_user ON chat_conversations(user_id, updated_at)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_career_jobs_user_updated ON career_jobs(user_id, updated_at DESC)")

    def _migrate_career_singleton_table(self, table: str, value_ddl: str, value_column: str, fallback: str):
        columns = [row["name"] for row in self.query(f"PRAGMA table_info({table})")]
        if not columns or "id" not in columns:
            return

        row = self.query_one(f"SELECT {value_column}, updated_at FROM {table} WHERE id = 1")
        value = row[value_column] if row else fallback
        updated_at = row["updated_at"] if row else 0
        self.execute(f"DROP TABLE {table}")
        self.execute(
            f"""
            CREATE TABLE {table} (
              user_id TEXT PRIMARY KEY,
              {value_ddl},
              updated_at REAL NOT NULL
            )
            """
        )
        self.execute(
            f"INSERT INTO {table} (user_id, {value_column}, updated_at) VALUES (?, ?, ?)",
            ("local", value, updated_at),
        )


db = SQLiteService()
