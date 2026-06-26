import json
import faiss
import numpy as np
import os
from core.config.settings import settings


class FAISSStore:
    def __init__(self, dim: int = 384, index_path: str = None):
        self.dim = dim
        self.index_path = index_path or settings.INDEX_PATH
        self.index = faiss.IndexFlatL2(dim)
        self.metadata = []
        os.makedirs(os.path.dirname(self.index_path), exist_ok=True)

    def add(self, vectors: np.ndarray, metadatas: list[dict]):
        vectors = np.asarray(vectors, dtype=np.float32)

        if vectors.size == 0:
            return

        if len(vectors.shape) == 1:
            vectors = vectors.reshape(1, -1)

        if vectors.shape[1] != self.dim:
            raise ValueError(f"Dim mismatch: got {vectors.shape[1]}, expected {self.dim}")

        if vectors.shape[0] != len(metadatas):
            raise ValueError(f"Vector/metadata mismatch: {vectors.shape[0]} vs {len(metadatas)}")

        self.index.add(vectors)
        self.metadata.extend(metadatas)

    def save(self):
        faiss.write_index(self.index, self.index_path)
        with open(self.index_path + ".meta.json", "w", encoding="utf-8") as f:
            json.dump(self.metadata, f)

    def load(self):
        meta_path = self.index_path + ".meta.json"
        if os.path.exists(self.index_path):
            self.index = faiss.read_index(self.index_path)
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                self.metadata = json.load(f)
        elif os.path.exists(self.index_path + ".meta"):
            self.index = faiss.IndexFlatL2(self.dim)
            self.metadata = []
            self.save()

    def search(self, query_vector: np.ndarray, k: int = 5, user_id: str = None) -> list[dict]:
        query_vector = np.asarray(query_vector, dtype=np.float32)

        if len(query_vector.shape) == 1:
            query_vector = query_vector.reshape(1, -1)

        search_k = self.index.ntotal if user_id else min(k, self.index.ntotal)
        if search_k == 0:
            return []

        distances, indices = self.index.search(query_vector, search_k)

        results = []
        for i, idx in enumerate(indices[0]):
            if idx < len(self.metadata):
                if user_id and self.metadata[idx].get("user_id", "local") != user_id:
                    continue
                results.append({
                    "metadata": self.metadata[idx],
                    "score": float(distances[0][i])
                })
                if len(results) >= k:
                    break
        return results

    def clear(self):
        self.index = faiss.IndexFlatL2(self.dim)
        self.metadata = []
        self.save()

    def delete_by_source(self, source: str, user_id: str = None) -> int:
        keep_vectors = []
        keep_metadata = []
        deleted = 0

        for idx, meta in enumerate(self.metadata):
            if meta.get("source") == source and (user_id is None or meta.get("user_id", "local") == user_id):
                deleted += 1
                continue
            if idx < self.index.ntotal:
                keep_vectors.append(self.index.reconstruct(idx))
                keep_metadata.append(meta)

        self.index = faiss.IndexFlatL2(self.dim)
        self.metadata = keep_metadata
        if keep_vectors:
            self.index.add(np.asarray(keep_vectors, dtype=np.float32))
        self.save()
        return deleted

    @property
    def total(self) -> int:
        return self.index.ntotal
