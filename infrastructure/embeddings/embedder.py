from functools import lru_cache

import numpy as np
import requests

from core.config.settings import settings


class Embedder:
    def __init__(self, model_name: str = None):
        self.provider = settings.EMBEDDING_PROVIDER
        self.model_name = model_name or (
            settings.GEMINI_EMBED_MODEL
            if self.provider == "gemini"
            else settings.EMBED_MODEL
        )
        self.model = None

        if self.provider == "gemini":
            if not settings.GEMINI_API_KEY:
                raise RuntimeError("GEMINI_API_KEY is required for Gemini embeddings")
            self.dim = settings.EMBED_DIM
            return

        if self.provider != "local":
            raise ValueError(f"Unsupported embedding provider: {self.provider}")

        # Importing sentence-transformers also loads PyTorch. Keep that cost out
        # of cloud processes that use the remote Gemini embedding API.
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(self.model_name)
        self.dim = self.model.get_sentence_embedding_dimension()

    def embed_batch(
        self,
        texts: list[str],
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)

        if self.provider == "gemini":
            return self._embed_gemini(texts, task_type)

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
        return self.embed_batch([text], task_type="RETRIEVAL_QUERY")[0]

    def _embed_gemini(self, texts: list[str], task_type: str) -> np.ndarray:
        vectors: list[list[float]] = []
        endpoint = (
            f"{settings.GEMINI_BASE_URL}/models/"
            f"{self.model_name}:batchEmbedContents"
        )

        try:
            for offset in range(0, len(texts), settings.EMBED_BATCH_SIZE):
                batch = texts[offset:offset + settings.EMBED_BATCH_SIZE]
                payload = {
                    "requests": [
                        {
                            "model": f"models/{self.model_name}",
                            "content": {
                                "parts": [{"text": self._prepare_text(text, task_type)}]
                            },
                            "embedContentConfig": {
                                "outputDimensionality": self.dim,
                                "autoTruncate": True,
                            },
                        }
                        for text in batch
                    ]
                }
                response = requests.post(
                    endpoint,
                    headers={"x-goog-api-key": settings.GEMINI_API_KEY},
                    json=payload,
                    timeout=settings.OLLAMA_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                data = response.json()
                embeddings = data.get("embeddings", []) if isinstance(data, dict) else []
                if len(embeddings) != len(batch):
                    raise RuntimeError("Gemini returned an incomplete embedding batch")
                for embedding in embeddings:
                    values = embedding.get("values") if isinstance(embedding, dict) else None
                    if not isinstance(values, list) or len(values) != self.dim:
                        raise RuntimeError("Gemini returned an invalid embedding vector")
                    vectors.append(values)
        except requests.exceptions.RequestException as exc:
            status = getattr(exc.response, "status_code", None)
            detail = f" (HTTP {status})" if status else ""
            raise RuntimeError(f"Gemini embedding request failed{detail}") from exc

        return np.asarray(vectors, dtype=np.float32)

    def _prepare_text(self, text: str, task_type: str) -> str:
        if self.model_name != "gemini-embedding-2":
            return text
        if task_type == "RETRIEVAL_QUERY":
            return f"task: search result | query: {text}"
        if task_type == "RETRIEVAL_DOCUMENT":
            return f"title: none | text: {text}"
        return text


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return Embedder()
