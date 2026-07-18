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

    def ask(self, question: str, session_id: str = None, dataset_name: str = None, model: str = None, user_id: str = "local") -> dict:
        session_id = session_id or str(uuid.uuid4())
        t0 = time.monotonic()
        model = model or settings.TASK_MODELS["bi"]

        try:
            history = memory.to_llm_format(session_id, user_id=user_id)
            result = bi_pipeline.ask(
                question,
                dataset_name=dataset_name,
                model=model,
                user_id=user_id,
                history=history,
            )
            result["session_id"] = session_id

            memory.add(session_id, "user", question, user_id=user_id)
            memory.add(session_id, "assistant", result.get("answer", ""), user_id=user_id)

            analytics.record(QueryEvent(
                user_id=user_id,
                session_id=session_id,
                query=question,
                agent="bi",
                model=model,
                latency_ms=(time.monotonic() - t0) * 1000
            ))
            return result

        except Exception as e:
            analytics.record(QueryEvent(
                user_id=user_id,
                session_id=session_id,
                query=question,
                agent="bi",
                model=model,
                latency_ms=(time.monotonic() - t0) * 1000,
                success=False,
                error=str(e),
                error_type=type(e).__name__,
            ))
            raise

    def load_csv(self, path: str, name: str, user_id: str = "local") -> dict:
        return bi_pipeline.load_csv(path, name, user_id=user_id)

    def load_excel(self, path: str, name: str, user_id: str = "local") -> dict:
        return bi_pipeline.load_excel(path, name, user_id=user_id)

    def list_datasets(self, user_id: str = "local") -> list[dict]:
        return bi_pipeline.list_datasets(user_id=user_id)

    def get_sample(self, name: str, user_id: str = "local") -> dict | None:
        return bi_pipeline.get_sample(name, user_id=user_id)

    def delete_dataset(self, name: str, user_id: str = "local") -> bool:
        return bi_pipeline.delete_dataset(name, user_id=user_id)


bi_agent = BIAgent()
