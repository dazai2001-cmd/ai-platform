import json
import os
import threading
from typing import TYPE_CHECKING

import faiss
import numpy as np

from core.config.settings import settings

if TYPE_CHECKING:
    from collections.abc import Sequence


class ChunkCapacityReservation:
    """Capacity held for one user while their document is being embedded."""

    def __init__(
        self,
        store: "FAISSStore",
        user_id: str,
        count: int,
        max_chunks: int,
    ):
        self._store = store
        self.user_id = user_id
        self.count = count
        self.max_chunks = max_chunks
        self._active = True

    def commit(self, vectors: np.ndarray, metadatas: "Sequence[dict]") -> int:
        """Atomically consume the reservation, add the chunks, and persist them."""
        return self._store._commit_reservation(self, vectors, list(metadatas))

    def release(self) -> None:
        """Release unused capacity. Calling this more than once is harmless."""
        self._store._release_reservation(self)


class FAISSStore:
    def __init__(self, dim: int = 384, index_path: str = None):
        self.dim = dim
        self.index_path = index_path or settings.INDEX_PATH
        self.index = faiss.IndexFlatL2(dim)
        self._metadata: list[dict] = []
        self._lock = threading.RLock()
        self._pending_chunks: dict[str, int] = {}
        parent = os.path.dirname(self.index_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    @staticmethod
    def _prepare_vectors(vectors: np.ndarray, dim: int, metadata_count: int) -> np.ndarray:
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.size == 0:
            if metadata_count:
                raise ValueError(f"Vector/metadata mismatch: 0 vs {metadata_count}")
            return vectors.reshape(0, dim)
        if len(vectors.shape) == 1:
            vectors = vectors.reshape(1, -1)
        if len(vectors.shape) != 2 or vectors.shape[1] != dim:
            actual_dim = vectors.shape[1] if len(vectors.shape) == 2 else "invalid"
            raise ValueError(f"Dim mismatch: got {actual_dim}, expected {dim}")
        if vectors.shape[0] != metadata_count:
            raise ValueError(f"Vector/metadata mismatch: {vectors.shape[0]} vs {metadata_count}")
        return vectors

    def _add_locked(self, vectors: np.ndarray, metadatas: list[dict]) -> None:
        if not metadatas:
            return
        self.index.add(vectors)
        self._metadata.extend(dict(metadata) for metadata in metadatas)

    def add(self, vectors: np.ndarray, metadatas: list[dict]):
        """Add chunks in memory. Prefer add_and_save for durable ingestion."""
        prepared = self._prepare_vectors(vectors, self.dim, len(metadatas))
        with self._lock:
            self._add_locked(prepared, metadatas)

    def add_and_save(self, vectors: np.ndarray, metadatas: list[dict]) -> None:
        """Add and persist as one store operation visible to other threads."""
        prepared = self._prepare_vectors(vectors, self.dim, len(metadatas))
        with self._lock:
            previous_index = faiss.clone_index(self.index)
            previous_metadata = self._metadata
            try:
                self._metadata = list(self._metadata)
                self._add_locked(prepared, metadatas)
                self._save_locked()
            except Exception:
                self.index = previous_index
                self._metadata = previous_metadata
                raise

    def _save_locked(self) -> None:
        faiss.write_index(self.index, self.index_path)
        with open(self.index_path + ".meta.json", "w", encoding="utf-8") as f:
            json.dump(self._metadata, f)

    def save(self):
        with self._lock:
            self._save_locked()

    def load(self):
        meta_path = self.index_path + ".meta.json"
        with self._lock:
            loaded_index = self.index
            loaded_metadata = self._metadata
            if os.path.exists(self.index_path):
                loaded_index = faiss.read_index(self.index_path)
            if os.path.exists(meta_path):
                with open(meta_path, encoding="utf-8") as f:
                    loaded_metadata = json.load(f)
            elif os.path.exists(self.index_path + ".meta"):
                loaded_index = faiss.IndexFlatL2(self.dim)
                loaded_metadata = []

            if loaded_index.ntotal != len(loaded_metadata):
                raise ValueError(
                    "FAISS index and metadata are inconsistent "
                    f"({loaded_index.ntotal} vectors, {len(loaded_metadata)} metadata records)"
                )
            self.index = loaded_index
            self._metadata = [dict(metadata) for metadata in loaded_metadata]
            if os.path.exists(self.index_path + ".meta") and not os.path.exists(meta_path):
                self._save_locked()

    def search(self, query_vector: np.ndarray, k: int = 5, user_id: str = None) -> list[dict]:
        query_vector = np.asarray(query_vector, dtype=np.float32)
        if len(query_vector.shape) == 1:
            query_vector = query_vector.reshape(1, -1)

        with self._lock:
            search_k = self.index.ntotal if user_id else min(k, self.index.ntotal)
            if search_k == 0:
                return []

            distances, indices = self.index.search(query_vector, search_k)
            results = []
            for i, idx in enumerate(indices[0]):
                if idx < len(self._metadata):
                    metadata = self._metadata[idx]
                    if user_id and metadata.get("user_id", "local") != user_id:
                        continue
                    results.append({
                        "metadata": dict(metadata),
                        "score": float(distances[0][i]),
                    })
                    if len(results) >= k:
                        break
            return results

    def clear(self):
        with self._lock:
            previous_index = self.index
            previous_metadata = self._metadata
            try:
                self.index = faiss.IndexFlatL2(self.dim)
                self._metadata = []
                self._save_locked()
            except Exception:
                self.index = previous_index
                self._metadata = previous_metadata
                raise

    def delete_by_source(self, source: str, user_id: str = None) -> int:
        with self._lock:
            keep_vectors = []
            keep_metadata = []
            deleted = 0

            for idx, metadata in enumerate(self._metadata):
                if metadata.get("source") == source and (
                    user_id is None or metadata.get("user_id", "local") == user_id
                ):
                    deleted += 1
                    continue
                if idx < self.index.ntotal:
                    keep_vectors.append(self.index.reconstruct(idx))
                    keep_metadata.append(metadata)

            if not deleted:
                return 0

            previous_index = self.index
            previous_metadata = self._metadata
            try:
                self.index = faiss.IndexFlatL2(self.dim)
                self._metadata = [dict(metadata) for metadata in keep_metadata]
                if keep_vectors:
                    self.index.add(np.asarray(keep_vectors, dtype=np.float32))
                self._save_locked()
            except Exception:
                self.index = previous_index
                self._metadata = previous_metadata
                raise
            return deleted

    def reserve_user_chunks(
        self,
        user_id: str,
        count: int,
        max_chunks: int,
    ) -> ChunkCapacityReservation:
        """Reserve user capacity before expensive embedding work starts."""
        if count < 0:
            raise ValueError("Chunk reservation count cannot be negative")
        with self._lock:
            owned = self._user_chunk_count_locked(user_id)
            pending = self._pending_chunks.get(user_id, 0)
            if owned + pending + count > max_chunks:
                raise ValueError(
                    f"Knowledge base exceeds the {max_chunks}-chunk per-user limit"
                )
            self._pending_chunks[user_id] = pending + count
            return ChunkCapacityReservation(self, user_id, count, max_chunks)

    def _release_reservation_locked(self, reservation: ChunkCapacityReservation) -> None:
        if not reservation._active:
            return
        remaining = self._pending_chunks.get(reservation.user_id, 0) - reservation.count
        if remaining > 0:
            self._pending_chunks[reservation.user_id] = remaining
        else:
            self._pending_chunks.pop(reservation.user_id, None)
        reservation._active = False

    def _release_reservation(self, reservation: ChunkCapacityReservation) -> None:
        with self._lock:
            self._release_reservation_locked(reservation)

    def _commit_reservation(
        self,
        reservation: ChunkCapacityReservation,
        vectors: np.ndarray,
        metadatas: list[dict],
    ) -> int:
        prepared = self._prepare_vectors(vectors, self.dim, len(metadatas))
        if len(metadatas) > reservation.count:
            raise ValueError("Cannot commit more chunks than were reserved")
        if any(
            metadata.get("user_id", "local") != reservation.user_id
            for metadata in metadatas
        ):
            raise ValueError("Reserved chunks must belong to the reserved user")

        with self._lock:
            if not reservation._active:
                raise RuntimeError("Chunk capacity reservation is no longer active")

            owned = self._user_chunk_count_locked(reservation.user_id)
            other_pending = (
                self._pending_chunks.get(reservation.user_id, 0) - reservation.count
            )
            if owned + other_pending + len(metadatas) > reservation.max_chunks:
                self._release_reservation_locked(reservation)
                raise ValueError(
                    f"Knowledge base exceeds the {reservation.max_chunks}-chunk per-user limit"
                )

            previous_index = faiss.clone_index(self.index)
            previous_metadata = self._metadata
            try:
                self._metadata = list(self._metadata)
                self._add_locked(prepared, metadatas)
                self._save_locked()
            except Exception:
                self.index = previous_index
                self._metadata = previous_metadata
                self._release_reservation_locked(reservation)
                raise

            self._release_reservation_locked(reservation)
            return len(metadatas)

    def _user_chunk_count_locked(self, user_id: str) -> int:
        return sum(
            1
            for metadata in self._metadata
            if metadata.get("user_id", "local") == user_id
        )

    def metadata_snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(metadata) for metadata in self._metadata]

    def user_chunk_count(self, user_id: str) -> int:
        with self._lock:
            return self._user_chunk_count_locked(user_id)

    @property
    def metadata(self) -> list[dict]:
        """Return a detached snapshot so callers cannot mutate store state."""
        return self.metadata_snapshot()

    @property
    def total(self) -> int:
        with self._lock:
            return self.index.ntotal
