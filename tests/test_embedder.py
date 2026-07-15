from types import SimpleNamespace

import numpy as np
import pytest
import requests

from core.config.settings import settings
from infrastructure.embeddings.embedder import Embedder


class _EmbeddingResponse:
    def __init__(self, embeddings, status_code=200, headers=None):
        self._embeddings = embeddings
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"HTTP {self.status_code}",
                response=self,
            )
        return None

    def json(self):
        return {"embeddings": self._embeddings}


def test_gemini_embeddings_batch_without_loading_local_model(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        count = len(kwargs["json"]["requests"])
        return _EmbeddingResponse([
            {"values": [float(i), float(i + 1), float(i + 2)]}
            for i in range(count)
        ])

    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "secret-key")
    monkeypatch.setattr(settings, "GEMINI_EMBED_MODEL", "gemini-embedding-2")
    monkeypatch.setattr(settings, "EMBED_DIM", 3)
    monkeypatch.setattr(settings, "EMBED_BATCH_SIZE", 2)
    monkeypatch.setattr("infrastructure.embeddings.embedder.requests.post", post)

    embedder = Embedder()
    vectors = embedder.embed_batch(["alpha", "beta", "gamma"])
    query = embedder.embed("question")

    assert vectors.shape == (3, 3)
    assert vectors.dtype == np.float32
    assert query.shape == (3,)
    assert len(calls) == 3
    assert all("secret-key" not in url for url, _ in calls)
    assert calls[0][1]["headers"] == {"x-goog-api-key": "secret-key"}
    first_request = calls[0][1]["json"]["requests"][0]
    assert first_request["content"]["parts"][0]["text"] == (
        "title: none | text: alpha"
    )
    assert first_request["embedContentConfig"] == {
        "outputDimensionality": 3,
        "autoTruncate": True,
    }
    query_request = calls[-1][1]["json"]["requests"][0]
    assert query_request["content"]["parts"][0]["text"] == (
        "task: search result | query: question"
    )


def test_gemini_embedding_errors_do_not_expose_api_key(monkeypatch):
    def post(*_args, **_kwargs):
        response = SimpleNamespace(status_code=429)
        raise requests.exceptions.HTTPError(
            "request failed at https://example.test?key=secret-key",
            response=response,
        )

    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "secret-key")
    monkeypatch.setattr(settings, "GEMINI_EMBED_MODEL", "gemini-embedding-2")
    monkeypatch.setattr(settings, "EMBED_DIM", 3)
    monkeypatch.setattr(settings, "EMBED_MAX_RETRIES", 0)
    monkeypatch.setattr("infrastructure.embeddings.embedder.requests.post", post)

    with pytest.raises(RuntimeError, match=r"Gemini embedding request failed \(HTTP 429\)") as error:
        Embedder().embed("question")

    assert "secret-key" not in str(error.value)


def test_gemini_embedding_rejects_incomplete_batch(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "secret-key")
    monkeypatch.setattr(settings, "GEMINI_EMBED_MODEL", "gemini-embedding-2")
    monkeypatch.setattr(settings, "EMBED_DIM", 3)
    monkeypatch.setattr(
        "infrastructure.embeddings.embedder.requests.post",
        lambda *_args, **_kwargs: _EmbeddingResponse([]),
    )

    with pytest.raises(RuntimeError, match="incomplete embedding batch"):
        Embedder().embed_batch(["alpha"])


def test_gemini_embedding_retries_transient_rate_limit(monkeypatch):
    responses = [
        _EmbeddingResponse([], status_code=429, headers={"Retry-After": "2"}),
        _EmbeddingResponse([{"values": [1.0, 2.0, 3.0]}]),
    ]
    sleeps = []

    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "secret-key")
    monkeypatch.setattr(settings, "GEMINI_EMBED_MODEL", "gemini-embedding-2")
    monkeypatch.setattr(settings, "EMBED_DIM", 3)
    monkeypatch.setattr(settings, "EMBED_MAX_RETRIES", 2)
    monkeypatch.setattr(settings, "EMBED_RETRY_BASE_SECONDS", 1)
    monkeypatch.setattr(settings, "EMBED_RETRY_MAX_SECONDS", 60)
    monkeypatch.setattr(
        "infrastructure.embeddings.embedder.requests.post",
        lambda *_args, **_kwargs: responses.pop(0),
    )
    monkeypatch.setattr("infrastructure.embeddings.embedder.random.uniform", lambda *_: 0)
    monkeypatch.setattr("infrastructure.embeddings.embedder.time.sleep", sleeps.append)

    vector = Embedder().embed("question")

    assert vector.tolist() == [1.0, 2.0, 3.0]
    assert sleeps == [2.0]


def test_hashing_embeddings_are_deterministic_normalized_and_relevant(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setattr(settings, "EMBED_DIM", 1024)
    embedder = Embedder()

    documents = embedder.embed_batch([
        "Megan Thee Stallion is a rapper from Houston, Texas.",
        "Quantum mechanics describes matter at microscopic scales.",
    ])
    query = embedder.embed("Megan Stallion Houston rapper")
    repeated = embedder.embed("Megan Stallion Houston rapper")

    assert documents.shape == (2, 1024)
    assert np.allclose(np.linalg.norm(documents, axis=1), [1.0, 1.0])
    assert np.allclose(query, repeated)
    assert np.linalg.norm(documents[0] - query) < np.linalg.norm(documents[1] - query)
