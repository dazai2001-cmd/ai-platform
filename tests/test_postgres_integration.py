"""Real PostgreSQL coverage, enabled by TEST_POSTGRES_URL in CI."""

from __future__ import annotations

import os
import time
import uuid

import pytest

from services.bi.dataset_repository import DatasetCapacityError, DatasetRepository
from services.storage.sqlite_service import PostgreSQLService


POSTGRES_URL = os.getenv("TEST_POSTGRES_URL", "").strip()
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not configured")


@pytest.fixture(scope="module")
def postgres_db():
    schema = os.getenv("TEST_POSTGRES_SCHEMA", "app_test_ci")
    service = PostgreSQLService(POSTGRES_URL, schema=schema)
    try:
        yield service
    finally:
        with service.connect() as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        service.close()


def test_real_postgres_schema_crud_upserts_and_foreign_keys(postgres_db):
    user_id = str(uuid.uuid4())
    conversation_id = uuid.uuid4().hex
    now = time.time()

    postgres_db.execute(
        """
        INSERT INTO auth_users
          (id, email, password_hash, email_verified, created_at, updated_at)
        VALUES (?, ?, ?, 1, ?, ?)
        """,
        (user_id, f"{user_id}@example.test", "test-hash", now, now),
    )
    upsert = """
        INSERT INTO user_model_settings (user_id, task, model) VALUES (?, ?, ?)
        ON CONFLICT (user_id, task) DO UPDATE SET model = excluded.model
    """
    postgres_db.execute(upsert, (user_id, "general", "first-model"))
    postgres_db.execute(upsert, (user_id, "general", "second-model"))
    postgres_db.execute_many([
        (
            """
            INSERT INTO chat_conversations (id, user_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (conversation_id, user_id, "Postgres chat", now, now),
        ),
        (
            """
            INSERT INTO chat_messages (conversation_id, role, content, sources_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (conversation_id, "assistant", "Connected", postgres_db.dumps([{"id": 1}]), now),
        ),
    ])

    assert postgres_db.query_one(
        "SELECT model FROM user_model_settings WHERE user_id = ? AND task = ?",
        (user_id, "general"),
    ) == {"model": "second-model"}
    message = postgres_db.query_one(
        "SELECT content, sources_json FROM chat_messages WHERE conversation_id = ?",
        (conversation_id,),
    )
    assert message["content"] == "Connected"
    assert postgres_db.loads(message["sources_json"]) == [{"id": 1}]

    postgres_db.execute("DELETE FROM chat_conversations WHERE id = ?", (conversation_id,))
    assert postgres_db.query_one(
        "SELECT id FROM chat_messages WHERE conversation_id = ?",
        (conversation_id,),
    ) is None


def test_real_postgres_migrations_are_idempotent(postgres_db):
    second_instance = PostgreSQLService(POSTGRES_URL, schema=postgres_db.schema)
    try:
        assert second_instance.query(
            "SELECT version, name FROM app_schema_migrations ORDER BY version"
        ) == [
            {"version": 1, "name": "initial_schema"},
            {"version": 2, "name": "user_scopes"},
            {"version": 3, "name": "foreign_key_indexes"},
            {"version": 4, "name": "durable_bi_datasets"},
        ]
    finally:
        second_instance.close()


def test_real_postgres_bi_payload_round_trip_and_global_quota(postgres_db, monkeypatch):
    import services.bi.dataset_repository as repository_module

    monkeypatch.setattr(repository_module, "db", postgres_db)
    repository = DatasetRepository()
    first_user = str(uuid.uuid4())
    second_user = str(uuid.uuid4())
    common = {
        "kind": "csv",
        "row_count": 1,
        "columns": ["revenue"],
        "max_datasets": 10,
        "max_storage_bytes": 100,
        "max_total_storage_bytes": 8,
    }

    repository.upsert(
        user_id=first_user,
        name="sales",
        payload=b"12345",
        **common,
    )
    assert repository.fetch(first_user, "sales")["payload"] == b"12345"
    assert repository.fetch(second_user, "sales") is None

    with pytest.raises(DatasetCapacityError, match="application dataset storage limit"):
        repository.upsert(
            user_id=second_user,
            name="sales",
            payload=b"6789",
            **common,
        )

    assert repository.delete(first_user, "sales") is True
    repository.upsert(
        user_id=second_user,
        name="sales",
        payload=b"6789",
        **common,
    )
    assert repository.fetch(second_user, "sales")["payload"] == b"6789"
