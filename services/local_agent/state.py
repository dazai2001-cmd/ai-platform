"""Small durable local store, independent of the configured application database."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from core.config.settings import settings


def enabled() -> bool:
    return settings.LOCAL_AGENT_ENABLED and not settings.IS_CLOUD_RUNTIME and not settings.IS_PRODUCTION


class LocalState:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or settings.LOCAL_AGENT_PATH).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "state.sqlite"
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS local_items (
                    kind TEXT NOT NULL, id TEXT NOT NULL, user_id TEXT NOT NULL,
                    payload TEXT NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY (kind, id)
                );
                CREATE INDEX IF NOT EXISTS local_items_owner ON local_items(kind, user_id, updated_at);
            """)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def put(self, kind: str, item: dict, user_id: str):
        with self.connect() as conn:
            conn.execute("""INSERT INTO local_items VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(kind, id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at
                WHERE local_items.user_id=excluded.user_id""",
                (kind, item["id"], user_id, json.dumps(item, ensure_ascii=False), time.time()))

    def get(self, kind: str, item_id: str, user_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM local_items WHERE kind=? AND id=? AND user_id=?",
                               (kind, item_id, user_id)).fetchone()
        return json.loads(row["payload"]) if row else None

    def list(self, kind: str, user_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT payload FROM local_items WHERE kind=? AND user_id=? ORDER BY updated_at DESC LIMIT 100",
                                (kind, user_id)).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def update(self, kind: str, item_id: str, user_id: str, **updates) -> dict | None:
        """Serialize state transitions, including cancellation, across API processes."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM local_items WHERE kind=? AND id=? AND user_id=?",
                               (kind, item_id, user_id)).fetchone()
            if not row:
                return None
            item = json.loads(row[0])
            if item.get("status") == "cancelled" and updates.get("status") != "cancelled":
                return item
            item.update(updates, updated_at=time.time())
            conn.execute("UPDATE local_items SET payload=?, updated_at=? WHERE kind=? AND id=? AND user_id=?",
                         (json.dumps(item), item["updated_at"], kind, item_id, user_id))
            return item

    def expire_active(self, kind: str, max_age: float):
        """Release stale queue leases without replaying potentially completed effects."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT id,user_id,payload FROM local_items WHERE kind=? AND (json_extract(payload,'$.status') IN ('queued','running') OR json_extract(payload,'$.worker_active')=1) AND updated_at < ?",
                                (kind, time.time() - max_age)).fetchall()
            for row in rows:
                item = json.loads(row["payload"])
                item.update(status="cancelled" if item.get("status") == "cancelled" else "interrupted",
                            worker_active=False, error="The worker stopped. Start a new task to retry.", updated_at=time.time())
                conn.execute("UPDATE local_items SET payload=?,updated_at=? WHERE kind=? AND id=? AND user_id=?",
                             (json.dumps(item), item["updated_at"], kind, row["id"], row["user_id"]))

    def delete(self, kind: str, item_id: str, user_id: str):
        with self.connect() as conn:
            conn.execute("DELETE FROM local_items WHERE kind=? AND id=? AND user_id=?", (kind, item_id, user_id))
