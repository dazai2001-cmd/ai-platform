import time
import uuid
from collections import Counter
from infrastructure.embeddings.embedder import get_embedder
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
        self.embedder = get_embedder()
        self.store = FAISSStore(dim=self.embedder.dim)
        self.store.load()
        self.retriever = Retriever(self.embedder, self.store)
        self.pipeline = QAPipeline(self.retriever)
        self.ingestion = IngestionService(self.embedder, self.store)

    def ask(self, question: str, session_id: str = None, model: str = None) -> dict:
        session_id = session_id or str(uuid.uuid4())
        history = memory.to_llm_format(session_id)
        t0 = time.monotonic()
        model = model or settings.TASK_MODELS["rag"]

        try:
            result = self.pipeline.ask(question, history=history, model=model)
            result["session_id"] = session_id

            memory.add(session_id, "user", question)
            memory.add(session_id, "assistant", result["answer"])

            analytics.record(QueryEvent(
                session_id=session_id,
                query=question,
                agent="rag",
                model=model,
                latency_ms=(time.monotonic() - t0) * 1000
            ))
            return result

        except Exception as e:
            analytics.record(QueryEvent(
                session_id=session_id,
                query=question,
                agent="rag",
                model=model,
                latency_ms=(time.monotonic() - t0) * 1000,
                success=False,
                error=str(e)
            ))
            raise

    def stream_ask(self, question: str, session_id: str = None, model: str = None):
        session_id = session_id or str(uuid.uuid4())
        model = model or settings.TASK_MODELS["rag"]
        return self.pipeline.stream_ask(question, model=model), session_id, model

    def ingest_pdf(self, path: str, filename: str) -> int:
        return self.ingestion.ingest_pdf(path, source=filename)

    def ingest_url(self, url: str) -> int:
        return self.ingestion.ingest_url(url)

    def ingest_text(self, text: str, source: str = "note") -> int:
        return self.ingestion.ingest_text(text, source=source)

    def stats(self) -> dict:
        return {
            "total_chunks": self.store.total,
            "model": settings.TASK_MODELS["rag"]
        }

    def documents(self) -> list[dict]:
        counts = Counter(meta.get("source", "unknown") for meta in self.store.metadata)
        documents = []
        for source, count in counts.items():
            matching = [m for m in self.store.metadata if m.get("source", "unknown") == source]
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

    def document_preview(self, source: str, limit: int = 8) -> dict:
        chunks = [
            meta.get("text", "")
            for meta in self.store.metadata
            if meta.get("source", "unknown") == source
        ][:limit]
        return {
            "source": source,
            "title": self._title_for_source(source),
            "chunks": len(chunks),
            "text": "\n\n".join(chunks),
        }

    def delete_document(self, source: str) -> int:
        return self.store.delete_by_source(source)

    @staticmethod
    def _title_for_source(source: str) -> str:
        parts = source.split("_", 1)
        if len(parts) == 2 and len(parts[0]) == 32:
            return parts[1]
        return source


rag_agent = RAGAgent()
