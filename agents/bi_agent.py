import time
import uuid
from domain.bi.pipeline import bi_pipeline
from services.memory.memory_service import memory
from services.analytics.analytics_service import analytics, QueryEvent
from core.config.settings import settings


class BIAgent:
    """
    Business Intelligence agent.
    Handles data analysis, SQL generation, and chart creation.
    Uses Qwen for structured reasoning.
    """

    def ask(self, question: str, session_id: str = None, dataset_name: str = None, model: str = None) -> dict:
        session_id = session_id or str(uuid.uuid4())
        t0 = time.monotonic()
        model = model or settings.TASK_MODELS["bi"]

        try:
            result = bi_pipeline.ask(question, dataset_name=dataset_name, model=model)
            result["session_id"] = session_id

            memory.add(session_id, "user", question)
            memory.add(session_id, "assistant", result.get("answer", ""))

            analytics.record(QueryEvent(
                session_id=session_id,
                query=question,
                agent="bi",
                model=model,
                latency_ms=(time.monotonic() - t0) * 1000
            ))
            return result

        except Exception as e:
            analytics.record(QueryEvent(
                session_id=session_id,
                query=question,
                agent="bi",
                model=model,
                latency_ms=(time.monotonic() - t0) * 1000,
                success=False,
                error=str(e)
            ))
            raise

    def load_csv(self, path: str, name: str) -> dict:
        return bi_pipeline.load_csv(path, name)

    def load_excel(self, path: str, name: str) -> dict:
        return bi_pipeline.load_excel(path, name)

    def list_datasets(self) -> list[dict]:
        return bi_pipeline.list_datasets()

    def get_sample(self, name: str) -> dict | None:
        return bi_pipeline.get_sample(name)


bi_agent = BIAgent()
