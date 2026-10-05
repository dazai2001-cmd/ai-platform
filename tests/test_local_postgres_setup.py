from argparse import Namespace
import os
import sqlite3
import uuid
from urllib.parse import unquote, urlsplit
from unittest.mock import Mock

import psycopg
from psycopg import sql
import pytest

from scripts import setup_local_postgres as setup
from services.storage.sqlite_service import PostgreSQLService, SQLiteService


def test_existing_server_setup_hides_and_encodes_the_password(monkeypatch, capsys):
    answers = iter(["", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    password = Mock(return_value="local:p@ss/word")
    monkeypatch.setattr(setup.getpass, "getpass", password)

    config = setup.existing_connection(Namespace(port=None))
    parsed = urlsplit(setup.connection_url(config))

    password.assert_called_once_with("PostgreSQL password: ")
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port == 5432
    assert parsed.username == "ai_platform"
    assert unquote(parsed.password) == "local:p@ss/word"
    assert "local:p@ss/word" not in capsys.readouterr().out


@pytest.fixture
def migration_database(monkeypatch, tmp_path):
    url = os.getenv("TEST_POSTGRES_URL", "")
    if not url:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    schema = "app_migration_test_" + uuid.uuid4().hex
    database = PostgreSQLService(url, schema=schema, auto_migrate=True)
    monkeypatch.setattr(setup, "SCHEMA", schema)
    monkeypatch.setattr(setup, "RUNTIME", tmp_path / "postgres-backups")
    try:
        yield url, database
    finally:
        with database.connect() as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        database.close()


def sqlite_source(path):
    database = SQLiteService(str(path))
    database.execute(
        "INSERT INTO auth_users (id, email, password_hash, email_verified, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("owner", "migration@example.test", "preserved-hash", 1, 100, 100),
    )
    database.execute(
        "INSERT INTO chat_conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("conversation", "owner", "Preserved chat", 100, 100),
    )
    database.execute(
        "INSERT INTO chat_messages (id, conversation_id, role, content, sources_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (100, "conversation", "assistant", "Preserved answer", '[{"source":"notes"}]', 100),
    )
    database.execute(
        "INSERT INTO memory_messages (id, user_id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
        (40, "owner", "conversation", "assistant", "Preserved memory", "2026-10-04"),
    )
    database.execute(
        "INSERT INTO bi_datasets (user_id, name, kind, payload, size_bytes, row_count, columns_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("owner", "sales", "csv", b"revenue\n300\n", 12, 1, '["revenue"]', 100, 100),
    )
    return database


def test_sqlite_copy_preserves_records_binary_payloads_and_identity_sequences(migration_database, tmp_path):
    url, target = migration_database
    source_path = tmp_path / "source.db"
    source = sqlite_source(source_path)

    counts = setup.copy_sqlite(url, source_path)

    assert counts["chat_messages"] == 1
    assert counts["bi_datasets"] == 1
    assert target.query_one("SELECT payload FROM bi_datasets")["payload"] == b"revenue\n300\n"
    assert target.query_one("SELECT password_hash FROM auth_users")["password_hash"] == "preserved-hash"
    target.execute(
        "INSERT INTO chat_messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        ("conversation", "assistant", "Next answer", 101),
    )
    assert target.query_one("SELECT id FROM chat_messages WHERE content = ?", ("Next answer",))["id"] == 101
    assert source.query_one("SELECT COUNT(*) AS rows FROM chat_messages")["rows"] == 1
    assert list(setup.RUNTIME.glob("sqlite-before-postgres-*.db"))

    assert setup.copy_sqlite(url, source_path) == {}
    assert target.query_one("SELECT COUNT(*) AS rows FROM chat_messages")["rows"] == 2


def test_failed_sqlite_copy_rolls_back_all_records(migration_database, tmp_path):
    url, target = migration_database
    source_path = tmp_path / "invalid-source.db"
    sqlite_source(source_path)
    with sqlite3.connect(source_path) as connection:
        connection.execute("UPDATE chat_messages SET conversation_id = 'missing-conversation'")

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        setup.copy_sqlite(url, source_path)

    assert target.query_one("SELECT COUNT(*) AS rows FROM auth_users")["rows"] == 0
    assert target.query_one("SELECT COUNT(*) AS rows FROM chat_conversations")["rows"] == 0
    assert target.query_one("SELECT COUNT(*) AS rows FROM chat_messages")["rows"] == 0


def test_legacy_scoring_tasks_are_archived_without_losing_originals(migration_database, tmp_path):
    url, target = migration_database
    source_path = tmp_path / "legacy-source.db"
    source = sqlite_source(source_path)
    source.execute(
        "INSERT INTO career_score_batches (id, user_id, status, cv_text, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("batch", "owner", "completed", "Saved CV", 100, 100),
    )
    with sqlite3.connect(source_path) as connection:
        connection.execute(
            "INSERT INTO career_score_tasks (batch_id, job_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("batch", "deleted-job", "completed", 100, 100),
        )

    counts = setup.copy_sqlite(url, source_path, archive_stale_tasks=True)

    assert counts["career_score_tasks"] == 0
    assert counts["chat_messages"] == 1
    assert target.query_one("SELECT COUNT(*) AS rows FROM career_score_batches")["rows"] == 1
    assert source.query_one("SELECT COUNT(*) AS rows FROM career_score_tasks")["rows"] == 1
    archive = next(setup.RUNTIME.glob("stale-score-tasks-*.json"))
    assert '"job_id": "deleted-job"' in archive.read_text(encoding="utf-8")
