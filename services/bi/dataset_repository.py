"""Durable, user-scoped BI dataset storage.

The repository stores the original bounded upload in the application's
database. In cloud mode that database is the existing private Supabase
PostgreSQL schema; locally it is SQLite. Keeping the same contract for both
backends makes restarts and multi-worker deployments deterministic.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any

from services.storage.sqlite_service import db


class DatasetCapacityError(ValueError):
    """Raised when a per-user or application-wide dataset quota is exceeded."""


class DatasetRepository:
    @staticmethod
    @contextmanager
    def _connection():
        if db.backend == "sqlite":
            with db.connection() as connection:
                yield connection
            return
        with db.connect() as connection:
            yield connection

    @staticmethod
    def _sql(statement: str) -> str:
        if db.backend == "postgresql":
            return db._convert_placeholders(statement)
        return statement

    @staticmethod
    def _row(value: Any) -> dict[str, Any]:
        return dict(value)

    @staticmethod
    def _payload_bytes(value: Any) -> bytes:
        if isinstance(value, memoryview):
            return value.tobytes()
        if isinstance(value, bytes):
            return value
        if isinstance(value, bytearray):
            return bytes(value)
        raise ValueError("Stored dataset payload is invalid")

    @staticmethod
    def _columns(value: Any) -> list[str]:
        try:
            decoded = json.loads(str(value))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Stored dataset metadata is invalid") from exc
        if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
            raise ValueError("Stored dataset metadata is invalid")
        return decoded

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        rows = db.query(
            """
            SELECT name, kind, size_bytes, row_count, columns_json, updated_at
            FROM bi_datasets
            WHERE user_id = ?
            ORDER BY updated_at DESC, name ASC
            """,
            (user_id,),
        )
        return [
            {
                "name": str(row["name"]),
                "kind": str(row["kind"]),
                "size_bytes": int(row["size_bytes"]),
                "rows": int(row["row_count"]),
                "columns": self._columns(row["columns_json"]),
                "updated_at": float(row["updated_at"]),
            }
            for row in rows
        ]

    def fetch(self, user_id: str, name: str) -> dict[str, Any] | None:
        row = db.query_one(
            """
            SELECT name, kind, payload, size_bytes, row_count, columns_json, updated_at
            FROM bi_datasets
            WHERE user_id = ? AND name = ?
            """,
            (user_id, name),
        )
        if row is None:
            return None
        return {
            "name": str(row["name"]),
            "kind": str(row["kind"]),
            "payload": self._payload_bytes(row["payload"]),
            "size_bytes": int(row["size_bytes"]),
            "rows": int(row["row_count"]),
            "columns": self._columns(row["columns_json"]),
            "updated_at": float(row["updated_at"]),
        }

    def metadata(self, user_id: str, name: str) -> dict[str, Any] | None:
        row = db.query_one(
            """
            SELECT name, kind, size_bytes, row_count, columns_json, updated_at
            FROM bi_datasets
            WHERE user_id = ? AND name = ?
            """,
            (user_id, name),
        )
        if row is None:
            return None
        return {
            "name": str(row["name"]),
            "kind": str(row["kind"]),
            "size_bytes": int(row["size_bytes"]),
            "rows": int(row["row_count"]),
            "columns": self._columns(row["columns_json"]),
            "updated_at": float(row["updated_at"]),
        }

    def upsert(
        self,
        *,
        user_id: str,
        name: str,
        kind: str,
        payload: bytes,
        row_count: int,
        columns: list[str],
        max_datasets: int,
        max_storage_bytes: int,
        max_total_storage_bytes: int,
        unique: bool = False,
    ) -> str:
        """Store one upload while enforcing user and global quotas atomically."""
        if kind not in {"csv", "excel"}:
            raise ValueError("Unsupported dataset kind")
        if not isinstance(payload, bytes) or not payload:
            raise ValueError("Dataset payload is empty")

        now = time.time()
        size_bytes = len(payload)
        columns_json = json.dumps([str(column) for column in columns])

        with self._connection() as connection:
            if db.backend == "postgresql":
                # Always take the global lock before the user lock. This keeps
                # quota checks atomic across workers without deadlocking two
                # uploads for the same user.
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    ("bi-datasets:global-quota",),
                )
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (user_id,),
                )
            else:
                connection.execute("BEGIN IMMEDIATE")

            existing_rows = connection.execute(
                self._sql(
                    "SELECT name, size_bytes, created_at FROM bi_datasets WHERE user_id = ?"
                ),
                (user_id,),
            ).fetchall()
            existing = {str(row["name"]): self._row(row) for row in existing_rows}
            if unique and name in existing:
                base_name = name
                index = 2
                while name in existing:
                    suffix = f"_{index}"
                    name = f"{base_name[:64 - len(suffix)].rstrip('_')}{suffix}"
                    index += 1
            prior = existing.get(name)
            projected_count = len(existing) + (0 if prior else 1)
            projected_size = sum(int(row["size_bytes"]) for row in existing.values())
            if prior:
                projected_size -= int(prior["size_bytes"])
            projected_size += size_bytes

            if projected_count > max_datasets:
                raise DatasetCapacityError(
                    f"User exceeds the {max_datasets}-dataset limit"
                )
            if projected_size > max_storage_bytes:
                raise DatasetCapacityError(
                    "User exceeds the configured cumulative dataset storage limit"
                )

            total_row = connection.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) AS total_size FROM bi_datasets"
            ).fetchone()
            projected_total_size = int(total_row["total_size"])
            if prior:
                projected_total_size -= int(prior["size_bytes"])
            projected_total_size += size_bytes
            if projected_total_size > max_total_storage_bytes:
                raise DatasetCapacityError(
                    "The application dataset storage limit has been reached. "
                    "Delete an existing dataset or try again later."
                )

            created_at = float(prior["created_at"]) if prior else now
            connection.execute(
                self._sql(
                    """
                    INSERT INTO bi_datasets
                      (user_id, name, kind, payload, size_bytes, row_count,
                       columns_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (user_id, name) DO UPDATE SET
                      kind = excluded.kind,
                      payload = excluded.payload,
                      size_bytes = excluded.size_bytes,
                      row_count = excluded.row_count,
                      columns_json = excluded.columns_json,
                      updated_at = excluded.updated_at
                    """
                ),
                (
                    user_id,
                    name,
                    kind,
                    payload,
                    size_bytes,
                    int(row_count),
                    columns_json,
                    created_at,
                    now,
                ),
            )
        return name

    def delete(self, user_id: str, name: str) -> bool:
        with self._connection() as connection:
            if db.backend == "postgresql":
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (user_id,),
                )
            else:
                connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                self._sql("DELETE FROM bi_datasets WHERE user_id = ? AND name = ?"),
                (user_id, name),
            )
            return bool(cursor.rowcount)


dataset_repository = DatasetRepository()
