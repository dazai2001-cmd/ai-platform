from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.config.settings import _database_auto_migrate_default, settings
from core.config.validation import configuration_issues
from services.storage.sqlite_service import (
    BI_DATASET_POSTGRES_COLUMNS,
    EXPECTED_MIGRATIONS,
    PostgreSQLService,
    SQLiteService,
    create_database_service,
)


class _FakeResult:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _FakeConnection:
    def __init__(self, migration_versions=None, migration_names=None, valid_bi_shape=True):
        self.calls: list[tuple[str, tuple]] = []
        self.migration_versions = list(migration_versions or [])
        self.migration_names = dict(migration_names or EXPECTED_MIGRATIONS)
        self.valid_bi_shape = valid_bi_shape
        self.next_rows: list[dict] = []

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("SELECT version, name FROM app_schema_migrations"):
            return _FakeResult([
                {"version": version, "name": self.migration_names[version]}
                for version in self.migration_versions
            ])
        if "FROM information_schema.columns" in normalized:
            if not self.valid_bi_shape:
                return _FakeResult([])
            return _FakeResult([
                {
                    "column_name": name,
                    "data_type": data_type,
                    "is_nullable": "NO",
                }
                for name, data_type in BI_DATASET_POSTGRES_COLUMNS.items()
            ])
        if "FROM information_schema.table_constraints" in normalized:
            return _FakeResult([
                {"constraint_type": "PRIMARY KEY"},
                {"constraint_type": "CHECK"},
            ])
        if "FROM pg_class AS table_class" in normalized:
            return _FakeResult([{"relrowsecurity": False}])
        if normalized.startswith("SET LOCAL"):
            return _FakeResult()
        rows, self.next_rows = self.next_rows, []
        return _FakeResult(rows)


class _FakePool:
    def __init__(self, connection=None):
        self.conn = connection or _FakeConnection()

    @contextmanager
    def connection(self):
        yield self.conn


def test_database_factory_keeps_sqlite_as_the_no_url_default(tmp_path):
    service = create_database_service("", sqlite_path=str(tmp_path / "local.db"))

    assert isinstance(service, SQLiteService)
    assert service.backend == "sqlite"
    assert service.query_one("SELECT 1 AS ok") == {"ok": 1}


@pytest.mark.parametrize(
    ("app_env", "ai_runtime", "database_url", "expected"),
    [
        ("development", "local", "", True),
        ("production", "local", "", False),
        ("development", "cloud", "", False),
        ("development", "local", "postgresql://db.example.test/app", False),
    ],
)
def test_database_auto_migration_defaults_are_safe(
    monkeypatch, app_env, ai_runtime, database_url, expected
):
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("AI_RUNTIME", ai_runtime)
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert _database_auto_migrate_default() is expected


def test_sqlite_initialization_tracks_migrations_and_current_scope_columns(tmp_path):
    service = SQLiteService(str(tmp_path / "schema.db"))

    assert service.query("SELECT version, name FROM app_schema_migrations ORDER BY version") == [
        {"version": 1, "name": "initial_schema"},
        {"version": 2, "name": "user_scopes"},
        {"version": 3, "name": "foreign_key_indexes"},
        {"version": 4, "name": "durable_bi_datasets"},
    ]
    memory_columns = {row["name"] for row in service.query("PRAGMA table_info(memory_messages)")}
    conversation_columns = {
        row["name"] for row in service.query("PRAGMA table_info(chat_conversations)")
    }
    assert "user_id" in memory_columns
    assert "user_id" in conversation_columns


def test_existing_sqlite_data_is_preserved_by_versioned_scope_migration(tmp_path):
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE career_preferences "
            "(id INTEGER PRIMARY KEY, preferences_json TEXT NOT NULL, updated_at REAL NOT NULL)"
        )
        connection.execute(
            "INSERT INTO career_preferences (id, preferences_json, updated_at) VALUES (1, ?, ?)",
            ('{"roles":["engineer"]}', 123.0),
        )
        connection.execute(
            "CREATE TABLE memory_messages "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL, "
            "content TEXT NOT NULL, timestamp TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO memory_messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            ("legacy-session", "user", "keep me", "2026-01-01T00:00:00"),
        )

    service = SQLiteService(str(database_path))

    assert service.query_one(
        "SELECT user_id, preferences_json, updated_at FROM career_preferences"
    ) == {
        "user_id": "local",
        "preferences_json": '{"roles":["engineer"]}',
        "updated_at": 123.0,
    }
    assert service.query_one(
        "SELECT user_id, content FROM memory_messages WHERE session_id = ?",
        ("legacy-session",),
    ) == {"user_id": "local", "content": "keep me"}


def test_portable_upsert_syntax_works_on_sqlite(tmp_path):
    service = SQLiteService(str(tmp_path / "upsert.db"))
    sql = """
        INSERT INTO user_model_settings (user_id, task, model) VALUES (?, ?, ?)
        ON CONFLICT (user_id, task) DO UPDATE SET model = excluded.model
    """

    service.execute(sql, ("user-1", "general", "first"))
    service.execute(sql, ("user-1", "general", "second"))

    assert service.query_one(
        "SELECT model FROM user_model_settings WHERE user_id = ? AND task = ?",
        ("user-1", "general"),
    ) == {"model": "second"}


def test_application_sql_has_no_sqlite_only_write_syntax():
    repository_root = Path(__file__).resolve().parents[1]
    database_callers = [
        path
        for source_root in (repository_root / "services", repository_root / "apps")
        for path in source_root.rglob("*.py")
        if path.name != "sqlite_service.py"
        and "from services.storage.sqlite_service import db" in path.read_text(encoding="utf-8")
    ]
    forbidden = ("INSERT OR REPLACE", "INSERT OR IGNORE", "AUTOINCREMENT", "PRAGMA ")

    violations = {
        str(path.relative_to(repository_root)): token
        for path in database_callers
        for token in forbidden
        if token in path.read_text(encoding="utf-8").upper()
    }

    assert violations == {}


def test_postgres_placeholder_conversion_skips_literals_identifiers_and_comments():
    sql = """
        SELECT '?', "?", $$?$$, $body$?$body$, value
        FROM example
        WHERE first = ? AND second = ? -- leave ? here
        /* and ? here */
    """

    converted = PostgreSQLService._convert_placeholders(sql)

    assert "WHERE first = %s AND second = %s" in converted
    assert "SELECT '?', \"?\", $$?$$, $body$?$body$" in converted
    assert "-- leave ? here" in converted
    assert "/* and ? here */" in converted


def test_postgres_public_interface_uses_private_schema_and_psycopg_parameters():
    connection = _FakeConnection()
    pool = _FakePool(connection)
    service = PostgreSQLService(
        "postgresql://app:secret@db.example.test:5432/postgres",
        pool=pool,
        initialize=False,
        schema="app_private",
    )

    service.execute("UPDATE example SET value = ? WHERE id = ?", ("new", 7))
    connection.next_rows = [{"id": 7, "value": "new"}]
    row = service.query_one("SELECT id, value FROM example WHERE id = ?", (7,))
    service.execute_many([
        ("DELETE FROM example WHERE id = ?", (7,)),
        ("DELETE FROM example WHERE id = ?", (8,)),
    ])

    assert row == {"id": 7, "value": "new"}
    set_search_path_calls = [sql for sql, _ in connection.calls if sql.startswith("SET LOCAL")]
    assert len(set_search_path_calls) == 3
    assert all('search_path TO "app_private"' in sql for sql in set_search_path_calls)
    assert any(
        sql == "UPDATE example SET value = %s WHERE id = %s" and params == ("new", 7)
        for sql, params in connection.calls
    )


def test_postgres_initializer_is_locked_versioned_and_uses_identity_columns():
    connection = _FakeConnection()
    PostgreSQLService(
        "postgres://app:secret@db.example.test:5432/postgres",
        pool=_FakePool(connection),
        schema="app_private",
    )
    statements = [sql for sql, _ in connection.calls]

    assert any(sql.startswith("SELECT pg_advisory_xact_lock") for sql in statements)
    assert 'CREATE SCHEMA IF NOT EXISTS "app_private"' in statements
    assert any("CREATE TABLE IF NOT EXISTS app_schema_migrations" in sql for sql in statements)
    assert any("GENERATED BY DEFAULT AS IDENTITY" in sql for sql in statements)
    assert sum(sql.startswith("INSERT INTO app_schema_migrations") for sql in statements) == 4
    assert any("ADD COLUMN IF NOT EXISTS user_id" in sql for sql in statements)
    assert any("idx_career_score_tasks_job" in sql for sql in statements)
    assert any("CREATE TABLE IF NOT EXISTS bi_datasets" in sql for sql in statements)


def test_postgres_can_verify_a_separately_migrated_schema_without_ddl():
    connection = _FakeConnection(migration_versions=[1, 2, 3, 4])
    PostgreSQLService(
        "postgres://app:secret@db.example.test:5432/postgres",
        pool=_FakePool(connection),
        schema="app_private",
        auto_migrate=False,
    )

    statements = [sql for sql, _ in connection.calls]
    assert any(sql.startswith("SELECT version, name FROM") for sql in statements)
    assert any("information_schema.columns" in sql for sql in statements)
    assert not any(sql.startswith(("CREATE ", "ALTER ", "INSERT ")) for sql in statements)


def test_postgres_schema_verifier_rejects_missing_or_conflicting_migrations():
    with pytest.raises(RuntimeError, match="missing application migration.*4"):
        PostgreSQLService(
            "postgres://app:secret@db.example.test:5432/postgres",
            pool=_FakePool(_FakeConnection(migration_versions=[1, 2, 3])),
            schema="app_private",
            auto_migrate=False,
        )

    wrong_names = {**EXPECTED_MIGRATIONS, 4: "unrelated_change"}
    with pytest.raises(RuntimeError, match="recorded as 'unrelated_change'"):
        PostgreSQLService(
            "postgres://app:secret@db.example.test:5432/postgres",
            pool=_FakePool(
                _FakeConnection(
                    migration_versions=[1, 2, 3, 4],
                    migration_names=wrong_names,
                )
            ),
            schema="app_private",
            auto_migrate=False,
        )


def test_postgres_schema_verifier_rejects_partial_bi_table_shape():
    with pytest.raises(RuntimeError, match="does not have the expected columns"):
        PostgreSQLService(
            "postgres://app:secret@db.example.test:5432/postgres",
            pool=_FakePool(
                _FakeConnection(
                    migration_versions=[1, 2, 3, 4],
                    valid_bi_shape=False,
                )
            ),
            schema="app_private",
            auto_migrate=False,
        )


@pytest.mark.parametrize(
    ("url", "schema"),
    [
        ("sqlite:///tmp/app.db", "app_private"),
        ("postgresql://app:secret@db.example.test/postgres", "not-valid;drop schema public"),
    ],
)
def test_postgres_rejects_invalid_connection_configuration(url, schema):
    with pytest.raises(ValueError):
        PostgreSQLService(url, pool=_FakePool(), initialize=False, schema=schema)


def test_production_configuration_requires_encrypted_postgres():
    values = {
        name: getattr(settings, name)
        for name in dir(settings)
        if name.isupper() and not name.startswith("_")
    }
    values.update(
        IS_PRODUCTION=True,
        DATABASE_URL="",
        DATABASE_SSLMODE="prefer",
    )

    issues = configuration_issues(SimpleNamespace(**values))

    database_errors = {
        issue.name for issue in issues if issue.severity == "error" and issue.name.startswith("DATABASE_")
    }
    assert database_errors == {"DATABASE_URL", "DATABASE_SSLMODE"}
