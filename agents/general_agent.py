import time
import uuid

from infrastructure.llm.ollama_client import ollama
from services.analytics.analytics_service import analytics, QueryEvent
from services.memory.memory_service import memory
from core.config.settings import settings

_GENERAL_PROMPT = """You are a helpful local AI assistant with a relaxed, friendly tone.

Runtime model: {model}
Current agent: General Chat

Answer the user's request directly and concisely. Be lenient with messy wording,
typos, half-formed ideas, and casual phrasing. Meet the user where they are,
ask a quick clarifying question only when needed, and otherwise make a sensible
assumption and help.

Keep the vibe calm, modern, and conversational. A little casual Gen Z energy is
fine, but do not overdo slang or become unserious.

For software, AI, and this app's context, use the technical meaning of terms.
For example, RAG means Retrieval-Augmented Generation. If an acronym or request
is ambiguous, briefly say the assumption you are making.

You are not the RAG/document-search agent in this chat. If the user asks what
model you are running, answer with the Runtime model above. Do not claim to be
a RAG model unless retrieved document context and sources are actually provided.
Previous chat history may include stale or incorrect assistant self-descriptions
from older versions of the app. Never use history to answer what model or agent
you are. For model/agent identity questions, use only Runtime model and Current
agent above.

Use the conversation history when it is relevant.

Conversation history:
{history}

Things the user asked me to remember:
{facts}

User request:
{query}
"""


class GeneralAgent:
    def _prompt(self, query: str, session_id: str, model: str) -> str:
        history = memory.to_llm_format(session_id)[-8:]
        history_text = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in history)
        facts_text = memory.facts_text()
        return _GENERAL_PROMPT.format(
            model=model,
            history=history_text or "No prior history.",
            facts=facts_text or "No saved facts.",
            query=query,
        )

    def ask(self, query: str, session_id: str = None, model: str = None) -> dict:
        session_id = session_id or str(uuid.uuid4())
        model = model or settings.TASK_MODELS["general"]
        t0 = time.monotonic()

        prompt = self._prompt(query, session_id, model)

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

    def stream_ask(self, query: str, session_id: str = None, model: str = None):
        session_id = session_id or str(uuid.uuid4())
        model = model or settings.TASK_MODELS["general"]
        prompt = self._prompt(query, session_id, model)
        t0 = time.monotonic()

        def generate():
            parts = []
            success = True
            error = None
            for token in ollama.stream(model, prompt, temperature=0.2):
                parts.append(token)
                if token.startswith("[STREAM ERROR]:"):
                    success = False
                    error = token
                yield token

            answer = "".join(parts).strip()
            if answer:
                memory.add(session_id, "user", query)
                memory.add(session_id, "assistant", answer)

            analytics.record(QueryEvent(
                session_id=session_id,
                query=query,
                agent="general",
                model=model,
                latency_ms=(time.monotonic() - t0) * 1000,
                success=success,
                error=error,
            ))

        return generate(), session_id, model


general_agent = GeneralAgent()
