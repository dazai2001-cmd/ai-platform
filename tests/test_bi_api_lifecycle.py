import io
import struct
from importlib.metadata import version
from pathlib import Path

import pytest


PASSWORD = "integration-password-123"


@pytest.fixture
def bi_client(tmp_path, monkeypatch):
    """Authenticated API client with BI storage isolated to this test."""
    from core.config.settings import settings
    from services.storage.sqlite_service import SQLiteService, db

    isolated_db = SQLiteService(str(tmp_path / "app.db"))
    monkeypatch.setattr(db, "path", isolated_db.path)
    import services.bi.dataset_repository as repository_module

    monkeypatch.setattr(repository_module, "db", isolated_db)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(settings, "API_AUTH_TOKEN", "")
    monkeypatch.setattr(settings, "SEND_VERIFICATION_EMAILS", False)
    monkeypatch.setattr(settings, "IS_CLOUD_RUNTIME", False)
    monkeypatch.setattr(settings, "IS_PRODUCTION", False)
    monkeypatch.setattr(settings, "AUTH_COOKIE_NAME", "bi_test_session")
    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", False)
    monkeypatch.setattr(settings, "AUTH_COOKIE_SAMESITE", "Lax")
    monkeypatch.setattr(settings, "APP_PUBLIC_URL", "http://127.0.0.1:3000")
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["http://127.0.0.1:3000"])

    upload_root = tmp_path / "uploads"
    manifest = tmp_path / "bi-manifest.json"

    from apps.api import deps
    import domain.bi.pipeline as bi_module

    monkeypatch.setattr(deps, "UPLOAD_PATH", upload_root)
    monkeypatch.setattr(settings, "UPLOAD_PATH", str(upload_root))
    monkeypatch.setattr(settings, "BI_MANIFEST_PATH", str(manifest))
    monkeypatch.setattr(bi_module, "_manifest", manifest)
    bi_module._datasets.clear()
    bi_module._dataset_versions.clear()

    from apps.api.main import create_app

    app = create_app({
        "TESTING": True,
        "RATELIMIT_ENABLED": False,
        "SKIP_CONFIG_VALIDATION": True,
    })
    with app.test_client() as client:
        yield client

    bi_module._datasets.clear()
    bi_module._dataset_versions.clear()


def _authenticated_headers(client) -> dict[str, str]:
    signup = client.post(
        "/api/auth/signup",
        json={"email": "bi-lifecycle@example.com", "password": PASSWORD},
    )
    assert signup.status_code == 201
    verification = client.get(
        "/api/auth/verify",
        query_string={"token": signup.get_json()["verification_token"]},
    )
    assert verification.status_code == 200
    login = client.post(
        "/api/auth/login",
        json={"email": "bi-lifecycle@example.com", "password": PASSWORD},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.get_json()['token']}"}


def _upload_csv(client, headers, name: str, csv_text: str):
    return client.post(
        "/api/bi/upload",
        data={
            "name": name,
            "file": (io.BytesIO(csv_text.encode("utf-8")), f"{name}.csv"),
        },
        content_type="multipart/form-data",
        headers=headers,
    )


def test_csv_upload_list_sample_ask_rows_chart_explicit_selection_and_delete(
    bi_client, monkeypatch
):
    from apps.api.routes import bi as bi_routes
    import domain.bi.pipeline as bi_module

    headers = _authenticated_headers(bi_client)
    monkeypatch.setattr(
        bi_routes.model_settings,
        "model_for",
        lambda _task, user_id: "fixture-bi-model",
    )

    def generated_plan(_model, prompt, temperature=0.1):
        assert temperature == 0.1
        assert "DATASET SCHEMA (" in prompt
        assert "SAMPLE DATA:" not in prompt
        assert "North" not in prompt
        assert "South" not in prompt
        if "bar chart" in prompt:
            return """Aggregate revenue by region.
```sql
SELECT region, SUM(revenue) AS revenue FROM dataset GROUP BY region ORDER BY region
```
```json
{"chart_type":"bar","title":"Revenue by region","x_label":"Region","y_label":"Revenue"}
```"""
        return """Calculate total revenue.
```sql
SELECT SUM(revenue) AS total_revenue FROM dataset
```"""

    monkeypatch.setattr(bi_module.ollama, "generate", generated_plan)

    primary = _upload_csv(
        bi_client,
        headers,
        "primary_sales",
        "region,revenue\nNorth,100\nSouth,200\n",
    )
    comparison = _upload_csv(
        bi_client,
        headers,
        "comparison_sales",
        "region,revenue\nNorth,10\nSouth,20\n",
    )
    assert primary.status_code == 200
    assert primary.get_json() == {
        "name": "primary_sales",
        "rows": 2,
        "columns": ["region", "revenue"],
    }
    assert comparison.status_code == 200
    from apps.api import deps

    assert list(Path(deps.UPLOAD_PATH).iterdir()) == []

    listed = bi_client.get("/api/bi/datasets", headers=headers)
    assert listed.status_code == 200
    assert {item["name"] for item in listed.get_json()} == {
        "primary_sales",
        "comparison_sales",
    }

    unselected = bi_client.post(
        "/api/bi/ask",
        json={"question": "What is total revenue?", "session_id": "no-selection"},
        headers=headers,
    )
    assert unselected.status_code == 400
    assert "Select a dataset" in unselected.get_json()["error"]

    sample = bi_client.get("/api/bi/datasets/primary_sales/sample", headers=headers)
    assert sample.status_code == 200
    assert sample.get_json() == {
        "columns": ["region", "revenue"],
        "sample": [
            {"region": "North", "revenue": 100},
            {"region": "South", "revenue": 200},
        ],
    }

    primary_answer = bi_client.post(
        "/api/bi/ask",
        json={
            "question": "What is total revenue?",
            "dataset": "primary_sales",
            "session_id": "bi-lifecycle-session",
        },
        headers=headers,
    )
    comparison_answer = bi_client.post(
        "/api/bi/ask",
        json={
            "question": "What is total revenue?",
            "dataset": "comparison_sales",
            "session_id": "bi-lifecycle-session",
        },
        headers=headers,
    )
    assert primary_answer.status_code == 200
    assert primary_answer.get_json()["dataset"] == "primary_sales"
    assert primary_answer.get_json()["rows"] == [{"total_revenue": 300}]
    assert primary_answer.get_json()["answer"] == "total revenue is 300."
    assert comparison_answer.status_code == 200
    assert comparison_answer.get_json()["dataset"] == "comparison_sales"
    assert comparison_answer.get_json()["rows"] == [{"total_revenue": 30}]
    assert comparison_answer.get_json()["answer"] == "total revenue is 30."

    chart_answer = bi_client.post(
        "/api/bi/ask",
        json={
            "question": "Show revenue by region as a bar chart",
            "dataset": "primary_sales",
            "session_id": "bi-lifecycle-session",
        },
        headers=headers,
    )
    assert chart_answer.status_code == 200
    chart_body = chart_answer.get_json()
    assert chart_body["answer"].startswith("I generated the chart.")
    assert chart_body["rows"] == [
        {"region": "North", "revenue": 100},
        {"region": "South", "revenue": 200},
    ]
    assert chart_body["chart"] == {
        "chart_type": "bar",
        "title": "Revenue by region",
        "x_label": "Region",
        "y_label": "Revenue",
        "data": {
            "labels": ["North", "South"],
            "series": [{"name": "revenue", "values": [100.0, 200.0]}],
        },
    }

    deleted = bi_client.delete("/api/bi/datasets/primary_sales", headers=headers)
    assert deleted.status_code == 200
    assert deleted.get_json() == {"deleted": "primary_sales"}
    assert bi_client.get(
        "/api/bi/datasets/primary_sales/sample", headers=headers
    ).status_code == 404
    assert [
        item["name"]
        for item in bi_client.get("/api/bi/datasets", headers=headers).get_json()
    ] == ["comparison_sales"]

    assert bi_client.delete(
        "/api/bi/datasets/comparison_sales", headers=headers
    ).status_code == 200
    assert bi_client.get("/api/bi/datasets", headers=headers).get_json() == []


def _biff5_record(record_id: int, payload: bytes = b"") -> bytes:
    return struct.pack("<HH", record_id, len(payload)) + payload


def _minimal_biff5_stream() -> bytes:
    """Return a tiny legacy BIFF5 Workbook stream without depending on xlwt."""
    bof_globals = _biff5_record(0x0809, struct.pack("<HHHH", 0x0500, 0x0005, 0, 0))
    eof = _biff5_record(0x000A)
    sheet_name = b"Sheet1"
    boundsheet_size = 4 + 4 + 1 + 1 + 1 + len(sheet_name)
    sheet_offset = len(bof_globals) + boundsheet_size + len(eof)
    boundsheet = _biff5_record(
        0x0085,
        struct.pack("<IBBB", sheet_offset, 0, 0, len(sheet_name)) + sheet_name,
    )

    bof_sheet = _biff5_record(0x0809, struct.pack("<HHHH", 0x0500, 0x0010, 0, 0))
    dimensions = _biff5_record(0x0200, struct.pack("<HHHHH", 0, 2, 0, 1, 0))
    label = _biff5_record(
        0x0204,
        struct.pack("<HHHH", 0, 0, 0, len(b"revenue")) + b"revenue",
    )
    number = _biff5_record(0x0203, struct.pack("<HHHd", 1, 0, 0, 42.0))
    return bof_globals + boundsheet + eof + bof_sheet + dimensions + label + number + eof


def _directory_entry(name: str, entry_type: int, child: int, start: int, size: int) -> bytes:
    no_stream = 0xFFFFFFFF
    entry = bytearray(128)
    encoded_name = (name + "\0").encode("utf-16le")
    entry[: len(encoded_name)] = encoded_name
    struct.pack_into("<HBBIII", entry, 64, len(encoded_name), entry_type, 1, no_stream, no_stream, child)
    struct.pack_into("<I", entry, 116, start)
    struct.pack_into("<Q", entry, 120, size)
    return bytes(entry)


def _minimal_biff5_workbook() -> bytes:
    """Wrap the BIFF stream in the OLE container required by real .xls files."""
    free_sector = 0xFFFFFFFF
    end_of_chain = 0xFFFFFFFE
    fat_sector = 0xFFFFFFFD

    header = bytearray(512)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHH", header, 24, 0x003E, 0x0003, 0xFFFE, 9)
    struct.pack_into("<H", header, 32, 6)
    struct.pack_into("<I", header, 40, 0)
    struct.pack_into("<I", header, 44, 1)
    struct.pack_into("<I", header, 48, 1)
    struct.pack_into("<I", header, 56, 4096)
    struct.pack_into("<I", header, 60, end_of_chain)
    struct.pack_into("<I", header, 64, 0)
    struct.pack_into("<I", header, 68, end_of_chain)
    struct.pack_into("<I", header, 72, 0)
    struct.pack_into("<I", header, 76, 0)
    for offset in range(80, 512, 4):
        struct.pack_into("<I", header, offset, free_sector)

    fat = [free_sector] * 128
    fat[0] = fat_sector
    fat[1] = end_of_chain
    for sector in range(2, 9):
        fat[sector] = sector + 1
    fat[9] = end_of_chain
    fat_bytes = struct.pack("<128I", *fat)

    directory = (
        _directory_entry("Root Entry", 5, 1, end_of_chain, 0)
        + _directory_entry("Workbook", 2, free_sector, 2, 4096)
        + bytes(256)
    )
    workbook = _minimal_biff5_stream().ljust(4096, b"\0")
    return bytes(header) + fat_bytes + directory + workbook


def test_legacy_xls_dependency_and_actual_upload_pipeline_are_ready(bi_client):
    assert version("xlrd") == "2.0.2"
    headers = _authenticated_headers(bi_client)

    uploaded = bi_client.post(
        "/api/bi/upload",
        data={
            "name": "legacy_sales",
            "file": (io.BytesIO(_minimal_biff5_workbook()), "legacy_sales.xls"),
        },
        content_type="multipart/form-data",
        headers=headers,
    )

    assert uploaded.status_code == 200
    assert uploaded.get_json() == {
        "name": "legacy_sales",
        "rows": 1,
        "columns": ["revenue"],
    }
    sample = bi_client.get("/api/bi/datasets/legacy_sales/sample", headers=headers)
    assert sample.status_code == 200
    assert sample.get_json() == {
        "columns": ["revenue"],
        "sample": [{"revenue": 42.0}],
    }
