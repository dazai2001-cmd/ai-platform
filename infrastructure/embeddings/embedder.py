import hashlib
import random
import re
import time
import unicodedata
from functools import lru_cache

import numpy as np
import requests

from core.config.settings import settings


_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "for", "from", "had", "has", "have", "he", "her", "his", "i", "in",
    "is", "it", "its", "of", "on", "or", "she", "that", "the", "their",
    "they", "this", "to", "was", "were", "what", "when", "where", "which",
    "who", "will", "with", "you", "your",
})


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

        if self.provider == "hashing":
            self.model_name = "feature-hashing-v1"
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
        if self.provider == "hashing":
            return np.vstack([self._embed_hashing(text) for text in texts])

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
                response = self._post_gemini_with_retry(endpoint, payload)
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

    def _post_gemini_with_retry(self, endpoint: str, payload: dict):
        for attempt in range(settings.EMBED_MAX_RETRIES + 1):
            try:
                response = requests.post(
                    endpoint,
                    headers={"x-goog-api-key": settings.GEMINI_API_KEY},
                    json=payload,
                    timeout=settings.OLLAMA_TIMEOUT_SECONDS,
                )
                status = response.status_code
                retryable = status in {408, 429} or status >= 500
                if retryable and attempt < settings.EMBED_MAX_RETRIES:
                    retry_after = response.headers.get("Retry-After", "").strip()
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = settings.EMBED_RETRY_BASE_SECONDS * (2 ** attempt)
                    delay = min(delay, settings.EMBED_RETRY_MAX_SECONDS)
                    time.sleep(delay + random.uniform(0, min(0.5, delay / 4)))
                    continue
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as exc:
                status = getattr(exc.response, "status_code", None)
                retryable = status is None or status in {408, 429} or status >= 500
                if not retryable:
                    raise
                if attempt >= settings.EMBED_MAX_RETRIES:
                    raise
                delay = min(
                    settings.EMBED_RETRY_BASE_SECONDS * (2 ** attempt),
                    settings.EMBED_RETRY_MAX_SECONDS,
                )
                time.sleep(delay + random.uniform(0, min(0.5, delay / 4)))
        raise RuntimeError("Gemini embedding retry loop ended unexpectedly")

    def _embed_hashing(self, text: str) -> np.ndarray:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        tokens = [
            token
            for token in re.findall(r"[\w'-]+", normalized, flags=re.UNICODE)
            if len(token) > 1 and token not in _STOP_WORDS
        ]
        features: list[tuple[str, float]] = [(f"w:{token}", 1.0) for token in tokens]
        features.extend(
            (f"b:{left}_{right}", 1.25)
            for left, right in zip(tokens, tokens[1:])
        )
        features.extend(
            (f"c:{token[index:index + 3]}", 0.3)
            for token in tokens
            if len(token) >= 4
            for index in range(len(token) - 2)
        )

        vector = np.zeros(self.dim, dtype=np.float32)
        for feature, weight in features:
            digest = hashlib.blake2b(
                feature.encode("utf-8"),
                digest_size=8,
                person=b"aiplat-v1",
            ).digest()
            index = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign * weight

        norm = float(np.linalg.norm(vector))
        if norm:
            vector /= norm
        return vector

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
