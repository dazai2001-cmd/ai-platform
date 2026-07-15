import time
import threading
import uuid
from collections import Counter
from infrastructure.vectorstore.faiss_store import FAISSStore
from application.ingestion.ingestion_service import IngestionService
from application.retrieval.retriever import Retriever
from domain.rag.pipeline import QAPipeline
from services.memory.memory_service import memory
from services.analytics.analytics_service import analytics, QueryEvent
from core.config.settings import settings


class RAGAgent:
    """
    2nd Brain agent.
    Handles document Q&A using RAG over the user's knowledge base.
    Uses Qwen by default.
    """

    def __init__(self):
        self.embedder = None
        self.store = None
        self.retriever = None
        self.pipeline = None
        self.ingestion = None
        self._initialization_lock = threading.Lock()

    def ensure_ready(self):
        """Load the embedding model and vector index on first RAG use."""
        if self.pipeline is not None:
            return self
        with self._initialization_lock:
            if self.pipeline is not None:
                return self
            from infrastructure.embeddings.embedder import get_embedder

            embedder = get_embedder()
            store = FAISSStore(dim=embedder.dim)
            store.load()
            self.embedder = embedder
            self.store = store
            self.retriever = Retriever(embedder, store)
            self.ingestion = IngestionService(embedder, store)
            # Assign this completion sentinel last so concurrent callers never
            # observe a partially initialized agent.
            self.pipeline = QAPipeline(self.retriever)
        return self

    def ask(self, question: str, session_id: str = None, model: str = None, user_id: str = "local") -> dict:
        self.ensure_ready()
        session_id = session_id or str(uuid.uuid4())
        history = memory.to_llm_format(session_id, user_id=user_id)
        t0 = time.monotonic()
        model = model or settings.TASK_MODELS["rag"]

        try:
            result = self.pipeline.ask(question, history=history, model=model, user_id=user_id)
            result["session_id"] = session_id

            memory.add(session_id, "user", question, user_id=user_id)
            memory.add(session_id, "assistant", result["answer"], user_id=user_id)

            analytics.record(QueryEvent(
                user_id=user_id,
                session_id=session_id,
                query=question,
                agent="rag",
                model=model,
                latency_ms=(time.monotonic() - t0) * 1000
            ))
            return result

        except Exception as e:
            analytics.record(QueryEvent(
                user_id=user_id,
                session_id=session_id,
                query=question,
                agent="rag",
                model=model,
                latency_ms=(time.monotonic() - t0) * 1000,
                success=False,
                error=str(e),
                error_type=type(e).__name__,
            ))
            raise

    def stream_ask(self, question: str, session_id: str = None, model: str = None, user_id: str = "local"):
        self.ensure_ready()
        session_id = session_id or str(uuid.uuid4())
        model = model or settings.TASK_MODELS["rag"]
        return self.pipeline.stream_ask(question, model=model, user_id=user_id), session_id, model

    def ingest_pdf(self, path: str, filename: str, user_id: str = "local") -> int:
        self.ensure_ready()
        return self.ingestion.ingest_pdf(path, source=filename, extra={"user_id": user_id})

    def ingest_url(self, url: str, user_id: str = "local") -> int:
        self.ensure_ready()
        return self.ingestion.ingest_url(url, extra={"user_id": user_id})

    def ingest_text(self, text: str, source: str = "note", user_id: str = "local") -> int:
        self.ensure_ready()
        return self.ingestion.ingest_text(text, source=source, extra={"user_id": user_id})

    def stats(self, user_id: str = "local") -> dict:
        self.ensure_ready()
        return {
            "total_chunks": self.store.user_chunk_count(user_id),
            "model": settings.TASK_MODELS["rag"]
        }

    def documents(self, user_id: str = "local") -> list[dict]:
        self.ensure_ready()
        metadata = self.store.metadata_snapshot()
        owned = [meta for meta in metadata if meta.get("user_id", "local") == user_id]
        counts = Counter(meta.get("source", "unknown") for meta in owned)
        documents = []
        for source, count in counts.items():
            matching = [m for m in owned if m.get("source", "unknown") == source]
            first = matching[0] if matching else {}
            preview = first.get("text", "")[:280]
            documents.append({
                "source": source,
                "title": self._title_for_source(source),
                "chunks": count,
                "type": first.get("type", "pdf" if source.lower().endswith(".pdf") else "note"),
                "preview": preview,
            })
        return sorted(documents, key=lambda d: d["title"].lower())

    def document_preview(self, source: str, limit: int = 8, user_id: str = "local") -> dict:
        self.ensure_ready()
        metadata = self.store.metadata_snapshot()
        chunks = [
            meta.get("text", "")
            for meta in metadata
            if meta.get("source", "unknown") == source and meta.get("user_id", "local") == user_id
        ][:limit]
        return {
            "source": source,
            "title": self._title_for_source(source),
            "chunks": len(chunks),
            "text": "\n\n".join(chunks),
        }

    def delete_document(self, source: str, user_id: str = "local") -> int:
        self.ensure_ready()
        return self.store.delete_by_source(source, user_id=user_id)

    @staticmethod
    def _title_for_source(source: str) -> str:
        parts = source.split("_", 1)
        if len(parts) == 2 and len(parts[0]) == 32:
            return parts[1]
        return source


rag_agent = RAGAgent()
