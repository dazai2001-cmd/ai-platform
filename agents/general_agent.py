import time
import uuid

from infrastructure.llm.ollama_client import ollama
from services.analytics.analytics_service import analytics, QueryEvent
from services.memory.memory_service import memory
from core.config.settings import settings

_GENERAL_PROMPT = """You are a helpful local AI assistant.

Answer the user's request directly and concisely. Use the conversation history when it is relevant.

Conversation history:
{history}

User request:
{query}
"""


class GeneralAgent:
    def ask(self, query: str, session_id: str = None, model: str = None) -> dict:
        session_id = session_id or str(uuid.uuid4())
        model = model or settings.TASK_MODELS["general"]
        t0 = time.monotonic()

        history = memory.to_llm_format(session_id)[-8:]
        history_text = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in history)
        prompt = _GENERAL_PROMPT.format(history=history_text or "No prior history.", query=query)

        try:
            answer = ollama.generate(model, prompt, temperature=0.2)
            memory.add(session_id, "user", query)
            memory.add(session_id, "assistant", answer)

            analytics.record(QueryEvent(
                session_id=session_id,
                query=query,
                agent="general",
                model=model,
                latency_ms=(time.monotonic() - t0) * 1000,
            ))

            return {
                "answer": answer,
                "sources": [],
                "chart": None,
                "model": model,
                "session_id": session_id,
            }
        except Exception as e:
            analytics.record(QueryEvent(
                session_id=session_id,
                query=query,
                agent="general",
                model=model,
                latency_ms=(time.monotonic() - t0) * 1000,
                success=False,
                error=str(e),
            ))
            raise


general_agent = GeneralAgent()
