import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from werkzeug.datastructures import FileStorage

import apps.api.deps as upload_deps
import domain.bi.pipeline as bi_module
from apps.api.deps import save_upload
from core.config.settings import settings
from core.config.validation import configuration_issues
from domain.bi.pipeline import BIPipeline


@pytest.fixture
def isolated_bi(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    manifest = tmp_path / "bi-manifest.json"
    monkeypatch.setattr(bi_module, "_manifest", manifest)
    monkeypatch.setattr(settings, "UPLOAD_PATH", str(upload_root))
    bi_module._datasets.clear()
    yield object.__new__(BIPipeline), upload_root, manifest
    bi_module._datasets.clear()


def _dataset_file(root: Path, name: str, content: str = "value\n1\n") -> Path:
    path = root / name
    path.write_text(content, encoding="utf-8")
    return path


def test_bi_resource_settings_require_positive_values():
    with patch.object(settings, "BI_SQL_TIMEOUT_MS", 0):
        assert any(issue.name == "BI_SQL_TIMEOUT_MS" for issue in configuration_issues(settings))


def test_safe_aggregate_query_runs_in_sqlite_sandbox():
    frame = pd.DataFrame({"category": ["a", "a", "b"], "value": [1, 2, 4]})
    sql = BIPipeline._validate_select_sql(
        "SELECT category, SUM(value) AS total FROM dataset "
        "GROUP BY category ORDER BY total DESC"
    )

    result = BIPipeline._execute_select(frame, sql)

    assert result.to_dict(orient="records") == [
        {"category": "b", "total": 4},
        {"category": "a", "total": 3},
    ]


def test_trailing_semicolon_is_normalized_and_query_executes():
    frame = pd.DataFrame({"value": [1, 2]})

    sql = BIPipeline._validate_select_sql("SELECT value FROM dataset ORDER BY value;")
    result = BIPipeline._execute_select(frame, sql)

    assert sql == "SELECT value FROM dataset ORDER BY value"
    assert result["value"].tolist() == [1, 2]


def test_normalized_extract_cast_and_in_predicate_execute():
    frame = pd.DataFrame({
        "Timestamp": ["2026-07-14 09:00:00", "2026-07-14 10:00:00"],
        "value": [1, 2],
    })
    generated = (
        "SELECT EXTRACT(HOUR FROM Timestamp) AS hour, COUNT(*) AS total "
        "FROM dataset WHERE value IN (1, 2) GROUP BY EXTRACT(HOUR FROM Timestamp);"
    )

    sql = BIPipeline._validate_select_sql(BIPipeline._normalize_sqlite_sql(generated))
    result = BIPipeline._execute_select(frame, sql)

    assert result.to_dict(orient="records") == [
        {"hour": 9, "total": 1},
        {"hour": 10, "total": 1},
    ]


@pytest.mark.parametrize(
    "function",
    ["randomblob", "zeroblob", "printf", "load_extension", "readfile"],
)
def test_allocation_and_file_functions_are_rejected_before_execution(function):
    with pytest.raises(ValueError, match="not allowed"):
        BIPipeline._validate_select_sql(f"SELECT {function}(100000000) FROM dataset")


def test_sqlite_authorizer_blocks_quoted_function_name_bypass():
    frame = pd.DataFrame({"value": [1]})

    with pytest.raises(ValueError, match="randomblob.*not allowed"):
        BIPipeline._execute_select(frame, 'SELECT "randomblob"(1000) FROM dataset')


def test_sql_execution_deadline_uses_progress_handler():
    frame = pd.DataFrame({"value": range(10_000)})
    # The first clock read establishes a deadline at t=1; the first progress
    # callback observes t=2 and interrupts deterministically.
    with (
        patch.object(settings, "BI_SQL_TIMEOUT_MS", 1000),
        patch.object(BIPipeline, "_monotonic", side_effect=[0.0, 2.0]),
    ):
        with pytest.raises(ValueError, match="execution limit"):
            BIPipeline._execute_select(frame, "SELECT SUM(value) FROM dataset")


def test_raw_dataset_upload_limit_removes_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_deps, "UPLOAD_PATH", tmp_path)
    upload = FileStorage(stream=io.BytesIO(b"123456"), filename="data.csv")

    with pytest.raises(ValueError, match="dataset limit"):
        save_upload(upload, {"csv"}, max_bytes=3)

    assert list(tmp_path.iterdir()) == []


def test_xlsx_archive_size_and_compression_ratio_are_checked_before_pandas(tmp_path):
    workbook = tmp_path / "compressed.xlsx"
    with zipfile.ZipFile(workbook, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", "A" * 20_000)

    with patch.object(settings, "MAX_EXCEL_COMPRESSION_RATIO", 2):
        with pytest.raises(ValueError, match="compression ratio"):
            BIPipeline._validate_excel_archive(str(workbook))

    with patch.object(settings, "MAX_EXCEL_UNCOMPRESSED_BYTES", 100):
        with pytest.raises(ValueError, match="uncompressed-size"):
            BIPipeline._validate_excel_archive(str(workbook))


def test_count_and_storage_quotas_include_existing_manifest_records(isolated_bi):
    pipeline, upload_root, manifest = isolated_bi
    existing = _dataset_file(upload_root, "existing.csv", "value\n12345\n")
    incoming = _dataset_file(upload_root, "incoming.csv", "value\n67890\n")
    manifest.write_text(json.dumps([{
        "name": "existing",
        "path": str(existing),
        "kind": "csv",
        "user_id": "owner",
    }]), encoding="utf-8")

    with patch.object(settings, "MAX_DATASETS_PER_USER", 1):
        with pytest.raises(ValueError, match="dataset limit"):
            pipeline._commit_dataset(
                "incoming", str(incoming), "csv", pd.DataFrame({"value": [67890]}), "owner"
            )

    with (
        patch.object(settings, "MAX_DATASETS_PER_USER", 10),
        patch.object(settings, "MAX_DATASET_STORAGE_BYTES_PER_USER", existing.stat().st_size),
    ):
        with pytest.raises(ValueError, match="storage limit"):
            pipeline._commit_dataset(
                "incoming", str(incoming), "csv", pd.DataFrame({"value": [67890]}), "owner"
            )

    assert json.loads(manifest.read_text(encoding="utf-8"))[0]["path"] == str(existing)
    assert "owner:incoming" not in bi_module._datasets


def test_per_dataset_and_cumulative_in_memory_limits(isolated_bi):
    pipeline, upload_root, _manifest = isolated_bi
    first_path = _dataset_file(upload_root, "first.csv")
    second_path = _dataset_file(upload_root, "second.csv")
    first = pd.DataFrame({"value": ["first payload"]})
    second = pd.DataFrame({"value": ["second payload"]})

    with patch.object(settings, "MAX_DATASET_MEMORY_BYTES", 1):
        with pytest.raises(ValueError, match="in-memory limit"):
            BIPipeline._validate_dataframe(first)

    first_bytes = BIPipeline._dataframe_memory_bytes(first)
    second_bytes = BIPipeline._dataframe_memory_bytes(second)
    pipeline._commit_dataset("first", str(first_path), "csv", first, "owner")
    with patch.object(
        settings,
        "MAX_DATASET_MEMORY_BYTES_PER_USER",
        first_bytes + second_bytes - 1,
    ):
        with pytest.raises(ValueError, match="cumulative in-memory"):
            pipeline._commit_dataset("second", str(second_path), "csv", second, "owner")

    assert "owner:first" in bi_module._datasets
    assert "owner:second" not in bi_module._datasets


def test_same_name_replacement_subtracts_old_quota_and_commits_coherently(isolated_bi):
    pipeline, upload_root, manifest = isolated_bi
    old_path = _dataset_file(upload_root, "old.csv", "value\n111111\n")
    new_path = _dataset_file(upload_root, "new.csv", "value\n2\n")
    manifest.write_text(json.dumps([{
        "name": "sales",
        "path": str(old_path),
        "kind": "csv",
        "user_id": "owner",
    }]), encoding="utf-8")
    frame = pd.DataFrame({"value": [2]})

    with (
        patch.object(settings, "MAX_DATASETS_PER_USER", 1),
        patch.object(settings, "MAX_DATASET_STORAGE_BYTES_PER_USER", new_path.stat().st_size),
    ):
        pipeline._commit_dataset("sales", str(new_path), "csv", frame, "owner")

    records = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["path"] == str(new_path)
    assert bi_module._datasets["owner:sales"] is frame
    assert not old_path.exists()
    assert new_path.exists()


def test_concurrent_different_names_cannot_race_past_user_count_quota(isolated_bi):
    pipeline, upload_root, manifest = isolated_bi
    paths = [
        _dataset_file(upload_root, "one.csv", "value\n1\n"),
        _dataset_file(upload_root, "two.csv", "value\n2\n"),
    ]

    def commit(index: int):
        try:
            pipeline._commit_dataset(
                f"dataset_{index}",
                str(paths[index]),
                "csv",
                pd.DataFrame({"value": [index]}),
                "owner",
            )
            return "committed"
        except ValueError:
            return "rejected"

    with patch.object(settings, "MAX_DATASETS_PER_USER", 1):
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(commit, range(2)))

    assert sorted(outcomes) == ["committed", "rejected"]
    records = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(records) == 1
    assert len([key for key in bi_module._datasets if key.startswith("owner:")]) == 1


def test_concurrent_same_name_manifest_and_memory_always_match(isolated_bi):
    pipeline, upload_root, manifest = isolated_bi
    paths = [
        _dataset_file(upload_root, "one.csv", "value\n1\n"),
        _dataset_file(upload_root, "two.csv", "value\n2\n"),
    ]
    values_by_path = {str(paths[0]): 1, str(paths[1]): 2}

    def replace(index: int):
        pipeline._commit_dataset(
            "sales", str(paths[index]), "csv", pd.DataFrame({"value": [index + 1]}), "owner"
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(replace, range(2)))

    record = json.loads(manifest.read_text(encoding="utf-8"))[0]
    stored_value = int(bi_module._datasets["owner:sales"].iloc[0]["value"])
    assert stored_value == values_by_path[record["path"]]
    assert Path(record["path"]).exists()


def test_delete_dataset_frees_manifest_memory_and_managed_upload(isolated_bi):
    pipeline, upload_root, manifest = isolated_bi
    path = _dataset_file(upload_root, "sales.csv")
    pipeline._commit_dataset("sales", str(path), "csv", pd.DataFrame({"value": [1]}), "owner")

    assert pipeline.delete_dataset("sales", "owner") is True

    assert json.loads(manifest.read_text(encoding="utf-8")) == []
    assert "owner:sales" not in bi_module._datasets
    assert not path.exists()
    assert pipeline.delete_dataset("sales", "owner") is False
