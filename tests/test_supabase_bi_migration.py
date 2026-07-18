from pathlib import Path

from services.storage.sqlite_service import BI_DATASET_POSTGRES_COLUMNS


def test_checked_in_supabase_bi_migration_matches_runtime_table_contract():
    migration = (
        Path(__file__).resolve().parents[1]
        / "supabase"
        / "migrations"
        / "20260718174423_durable_bi_datasets.sql"
    ).read_text(encoding="utf-8").lower()

    sql_types = {
        "text": "text",
        "bytea": "bytea",
        "bigint": "bigint",
        "integer": "integer",
        "double precision": "double precision",
    }
    for column, data_type in BI_DATASET_POSTGRES_COLUMNS.items():
        assert f"{column} {sql_types[data_type]} not null" in migration

    assert "primary key (user_id, name)" in migration
    assert "check (kind in ('csv', 'excel'))" in migration
    assert "enable row level security" not in migration
    assert "revoke all on schema app_private from public, anon, authenticated" in migration
    assert "revoke all on table app_private.bi_datasets" in migration
    assert "values (4, 'durable_bi_datasets'" in migration
