import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from application.ingestion.ingestion_service import IngestionService
from core.config.settings import settings
from infrastructure.vectorstore.faiss_store import FAISSStore


def _metadata(source: str, user_id: str = "owner") -> dict:
    return {"source": source, "text": source, "user_id": user_id}


def test_add_and_save_is_not_visible_until_persistence_finishes(tmp_path, monkeypatch):
    store = FAISSStore(dim=2, index_path=str(tmp_path / "index.faiss"))
    save_entered = threading.Event()
    allow_save = threading.Event()
    search_started = threading.Event()
    search_finished = threading.Event()
    original_save = store._save_locked

    def blocking_save():
        save_entered.set()
        assert allow_save.wait(2)
        original_save()

    def search():
        search_started.set()
        result = store.search(np.asarray([1.0, 0.0], dtype=np.float32))
        search_finished.set()
        return result

    monkeypatch.setattr(store, "_save_locked", blocking_save)
    with ThreadPoolExecutor(max_workers=2) as executor:
        add_future = executor.submit(
            store.add_and_save,
            np.asarray([[1.0, 0.0]], dtype=np.float32),
            [_metadata("new.pdf")],
        )
        assert save_entered.wait(2)
        search_future = executor.submit(search)
        assert search_started.wait(2)
        try:
            assert not search_finished.wait(0.1)
        finally:
            allow_save.set()
        add_future.result(timeout=2)
        results = search_future.result(timeout=2)

    assert [result["metadata"]["source"] for result in results] == ["new.pdf"]
    reloaded = FAISSStore(dim=2, index_path=str(tmp_path / "index.faiss"))
    reloaded.load()
    assert reloaded.total == 1
    assert reloaded.metadata_snapshot() == [_metadata("new.pdf")]


def test_delete_holds_lock_through_rebuild_and_persistence(tmp_path, monkeypatch):
    store = FAISSStore(dim=2, index_path=str(tmp_path / "index.faiss"))
    store.add_and_save(
        np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
        [_metadata("remove.pdf"), _metadata("keep.pdf")],
    )
    save_entered = threading.Event()
    allow_save = threading.Event()
    search_started = threading.Event()
    search_finished = threading.Event()
    original_save = store._save_locked

    def blocking_save():
        save_entered.set()
        assert allow_save.wait(2)
        original_save()

    def search():
        search_started.set()
        result = store.search(np.asarray([1.0, 0.0], dtype=np.float32), k=2)
        search_finished.set()
        return result

    monkeypatch.setattr(store, "_save_locked", blocking_save)
    with ThreadPoolExecutor(max_workers=2) as executor:
        delete_future = executor.submit(store.delete_by_source, "remove.pdf", "owner")
        assert save_entered.wait(2)
        search_future = executor.submit(search)
        assert search_started.wait(2)
        try:
            assert not search_finished.wait(0.1)
        finally:
            allow_save.set()
        assert delete_future.result(timeout=2) == 1
        results = search_future.result(timeout=2)

    assert [result["metadata"]["source"] for result in results] == ["keep.pdf"]
    reloaded = FAISSStore(dim=2, index_path=str(tmp_path / "index.faiss"))
    reloaded.load()
    assert reloaded.metadata_snapshot() == [_metadata("keep.pdf")]


def test_metadata_snapshots_cannot_mutate_store_state(tmp_path):
    store = FAISSStore(dim=2, index_path=str(tmp_path / "index.faiss"))
    store.add(np.asarray([[1.0, 0.0]], dtype=np.float32), [_metadata("safe.pdf")])

    snapshot = store.metadata_snapshot()
    snapshot[0]["source"] = "mutated.pdf"
    snapshot.append(_metadata("injected.pdf"))

    assert store.total == 1
    assert store.metadata_snapshot() == [_metadata("safe.pdf")]


def test_pending_capacity_rejects_second_ingest_before_embedding(tmp_path):
    store = FAISSStore(dim=2, index_path=str(tmp_path / "index.faiss"))
    embed_entered = threading.Event()
    allow_embed = threading.Event()

    class BlockingEmbedder:
        def __init__(self):
            self.calls = 0
            self._lock = threading.Lock()

        def embed_batch(self, chunks):
            with self._lock:
                self.calls += 1
            embed_entered.set()
            assert allow_embed.wait(2)
            return np.zeros((len(chunks), 2), dtype=np.float32)

    embedder = BlockingEmbedder()
    service = IngestionService(embedder, store)
    with (
        patch.object(settings, "MAX_CHUNKS_PER_USER", 2),
        patch(
            "application.ingestion.ingestion_service.chunk_text",
            return_value=["first", "second"],
        ),
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        first = executor.submit(
            service.ingest_text,
            "first document",
            "first.pdf",
            {"user_id": "owner"},
        )
        assert embed_entered.wait(2)
        try:
            with pytest.raises(ValueError, match="per-user limit"):
                service.ingest_text(
                    "second document",
                    source="second.pdf",
                    extra={"user_id": "owner"},
                )
            assert embedder.calls == 1
        finally:
            allow_embed.set()
        assert first.result(timeout=2) == 2

    assert store.user_chunk_count("owner") == 2


def test_embedding_failure_releases_pending_capacity(tmp_path):
    store = FAISSStore(dim=2, index_path=str(tmp_path / "index.faiss"))

    class FlakyEmbedder:
        def __init__(self):
            self.calls = 0

        def embed_batch(self, chunks):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("embedding failed")
            return np.zeros((len(chunks), 2), dtype=np.float32)

    service = IngestionService(FlakyEmbedder(), store)
    with (
        patch.object(settings, "MAX_CHUNKS_PER_USER", 1),
        patch(
            "application.ingestion.ingestion_service.chunk_text",
            return_value=["one"],
        ),
    ):
        with pytest.raises(RuntimeError, match="embedding failed"):
            service.ingest_text("first", extra={"user_id": "owner"})
        assert service.ingest_text("second", extra={"user_id": "owner"}) == 1

    assert store.user_chunk_count("owner") == 1


def test_persistence_failure_rolls_back_chunks_and_releases_capacity(tmp_path, monkeypatch):
    store = FAISSStore(dim=2, index_path=str(tmp_path / "index.faiss"))

    class Embedder:
        def embed_batch(self, chunks):
            return np.zeros((len(chunks), 2), dtype=np.float32)

    service = IngestionService(Embedder(), store)
    original_save = store._save_locked
    with (
        patch.object(settings, "MAX_CHUNKS_PER_USER", 1),
        patch(
            "application.ingestion.ingestion_service.chunk_text",
            return_value=["one"],
        ),
    ):
        monkeypatch.setattr(
            store,
            "_save_locked",
            lambda: (_ for _ in ()).throw(OSError("disk unavailable")),
        )
        with pytest.raises(OSError, match="disk unavailable"):
            service.ingest_text("first", extra={"user_id": "owner"})
        assert store.total == 0
        assert store.metadata_snapshot() == []

        monkeypatch.setattr(store, "_save_locked", original_save)
        assert service.ingest_text("second", extra={"user_id": "owner"}) == 1

    assert store.user_chunk_count("owner") == 1


def test_document_quota_counts_existing_pending_union():
    from apps.api.routes import rag as rag_routes

    user_id = "overlap-user"
    with rag_routes._DOCUMENT_QUOTA_LOCK:
        previous = rag_routes._PENDING_DOCUMENTS.get(user_id)
        rag_routes._PENDING_DOCUMENTS[user_id] = {"committed.pdf"}

    try:
        with (
            patch.object(settings, "MAX_DOCUMENTS_PER_USER", 2),
            patch.object(
                rag_routes.rag_agent,
                "documents",
                return_value=[{"source": "committed.pdf"}],
            ),
        ):
            reservation = rag_routes._reserve_document(user_id, "new.pdf")
            reservation.release()
    finally:
        with rag_routes._DOCUMENT_QUOTA_LOCK:
            if previous is None:
                rag_routes._PENDING_DOCUMENTS.pop(user_id, None)
            else:
                rag_routes._PENDING_DOCUMENTS[user_id] = previous
