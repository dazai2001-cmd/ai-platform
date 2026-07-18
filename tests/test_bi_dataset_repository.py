from concurrent.futures import ThreadPoolExecutor

import pytest

import services.bi.dataset_repository as repository_module
from services.bi.dataset_repository import DatasetCapacityError, DatasetRepository
from services.storage.sqlite_service import SQLiteService


@pytest.fixture()
def repository(tmp_path, monkeypatch):
    database = SQLiteService(str(tmp_path / "datasets.db"))
    monkeypatch.setattr(repository_module, "db", database)
    return DatasetRepository()


def test_dataset_repository_round_trip_and_delete(repository):
    repository.upsert(
        user_id="owner",
        name="sales",
        kind="csv",
        payload=b"region,revenue\nNorth,100\n",
        row_count=1,
        columns=["region", "revenue"],
        max_datasets=10,
        max_storage_bytes=1024,
        max_total_storage_bytes=4096,
    )

    assert repository.list_for_user("other") == []
    assert repository.list_for_user("owner")[0] | {"updated_at": 0} == {
        "name": "sales",
        "kind": "csv",
        "size_bytes": 25,
        "rows": 1,
        "columns": ["region", "revenue"],
        "updated_at": 0,
    }
    fetched = repository.fetch("owner", "sales")
    assert fetched is not None
    assert fetched["payload"] == b"region,revenue\nNorth,100\n"
    assert repository.delete("other", "sales") is False
    assert repository.delete("owner", "sales") is True
    assert repository.fetch("owner", "sales") is None


def test_dataset_repository_enforces_count_and_storage_quotas(repository):
    repository.upsert(
        user_id="owner",
        name="first",
        kind="csv",
        payload=b"12345",
        row_count=1,
        columns=["value"],
        max_datasets=1,
        max_storage_bytes=8,
        max_total_storage_bytes=4096,
    )

    with pytest.raises(DatasetCapacityError, match="1-dataset"):
        repository.upsert(
            user_id="owner",
            name="second",
            kind="csv",
            payload=b"1",
            row_count=1,
            columns=["value"],
            max_datasets=1,
            max_storage_bytes=8,
            max_total_storage_bytes=4096,
        )

    with pytest.raises(DatasetCapacityError, match="storage limit"):
        repository.upsert(
            user_id="owner",
            name="first",
            kind="csv",
            payload=b"123456789",
            row_count=1,
            columns=["value"],
            max_datasets=1,
            max_storage_bytes=8,
            max_total_storage_bytes=4096,
        )

    assert repository.fetch("owner", "first")["payload"] == b"12345"


def test_replacing_a_dataset_preserves_created_slot_and_updates_metadata(repository):
    for payload, rows in ((b"a\n1\n", 1), (b"a\n1\n2\n", 2)):
        repository.upsert(
            user_id="owner",
            name="sales",
            kind="csv",
            payload=payload,
            row_count=rows,
            columns=["a"],
            max_datasets=1,
            max_storage_bytes=100,
            max_total_storage_bytes=4096,
        )

    assert repository.list_for_user("owner")[0]["rows"] == 2
    assert repository.fetch("owner", "sales")["payload"] == b"a\n1\n2\n"


def test_unique_upload_names_are_reserved_atomically(repository):
    def store(value: int) -> str:
        return repository.upsert(
            user_id="owner",
            name="sales",
            kind="csv",
            payload=f"value\n{value}\n".encode(),
            row_count=1,
            columns=["value"],
            max_datasets=10,
            max_storage_bytes=1024,
            max_total_storage_bytes=4096,
            unique=True,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        names = list(executor.map(store, (1, 2)))

    assert sorted(names) == ["sales", "sales_2"]
    assert {item["name"] for item in repository.list_for_user("owner")} == {
        "sales",
        "sales_2",
    }


def test_dataset_repository_enforces_application_wide_storage_quota(repository):
    repository.upsert(
        user_id="first-user",
        name="sales",
        kind="csv",
        payload=b"12345",
        row_count=1,
        columns=["value"],
        max_datasets=10,
        max_storage_bytes=100,
        max_total_storage_bytes=8,
    )

    with pytest.raises(DatasetCapacityError, match="application dataset storage limit"):
        repository.upsert(
            user_id="second-user",
            name="sales",
            kind="csv",
            payload=b"6789",
            row_count=1,
            columns=["value"],
            max_datasets=10,
            max_storage_bytes=100,
            max_total_storage_bytes=8,
        )

    assert repository.list_for_user("second-user") == []

    assert repository.delete("first-user", "sales") is True
    repository.upsert(
        user_id="second-user",
        name="sales",
        kind="csv",
        payload=b"6789",
        row_count=1,
        columns=["value"],
        max_datasets=10,
        max_storage_bytes=100,
        max_total_storage_bytes=8,
    )
    assert repository.fetch("second-user", "sales")["payload"] == b"6789"


def test_replacement_releases_space_within_the_global_quota(repository):
    common = {
        "kind": "csv",
        "row_count": 1,
        "columns": ["value"],
        "max_datasets": 10,
        "max_storage_bytes": 100,
        "max_total_storage_bytes": 8,
    }
    repository.upsert(user_id="first-user", name="sales", payload=b"12345", **common)
    repository.upsert(user_id="first-user", name="sales", payload=b"12", **common)
    repository.upsert(user_id="second-user", name="sales", payload=b"67890", **common)

    assert repository.fetch("first-user", "sales")["payload"] == b"12"
    assert repository.fetch("second-user", "sales")["payload"] == b"67890"


def test_concurrent_users_cannot_race_past_global_storage_quota(repository):
    def store(user_id: str) -> str:
        try:
            repository.upsert(
                user_id=user_id,
                name="sales",
                kind="csv",
                payload=b"12345",
                row_count=1,
                columns=["value"],
                max_datasets=10,
                max_storage_bytes=100,
                max_total_storage_bytes=8,
            )
            return "stored"
        except DatasetCapacityError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(store, ("first-user", "second-user")))

    assert sorted(outcomes) == ["rejected", "stored"]
    assert sum(item["size_bytes"] for user_id in ("first-user", "second-user")
               for item in repository.list_for_user(user_id)) == 5
