import numpy as np
from functools import lru_cache
from sentence_transformers import SentenceTransformer
from core.config.settings import settings


class Embedder:
    def __init__(self, model_name: str = None):
        self.model = SentenceTransformer(model_name or settings.EMBED_MODEL)
        self.dim = self.model.get_sentence_embedding_dimension()

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)

        vectors = self.model.encode(
            texts,
            convert_to_numpy=True,
            show_progress_bar=False
        )

        vectors = np.asarray(vectors, dtype=np.float32)

        if len(vectors.shape) == 1:
            vectors = vectors.reshape(1, -1)

        return vectors

    def embed(self, text: str) -> np.ndarray:
        return self.embed_batch([text])[0]


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return Embedder()
