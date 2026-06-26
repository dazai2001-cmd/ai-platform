from pathlib import Path
from infrastructure.llm.ollama_client import ollama
from core.config.settings import settings

_PROMPT = (Path(__file__).parents[2] / "core/prompts/rag_prompt.txt").read_text()


class QAPipeline:
    def __init__(self, retriever):
        self.retriever = retriever

    def ask(self, question: str, history: list[dict] = None, model: str = None, user_id: str = "local") -> dict:
        model = model or settings.TASK_MODELS["rag"]
        results = self.retriever.search(question, user_id=user_id)
        context = self.retriever.format_context(results)

        # Include last 4 messages of history if available
        history_text = ""
        if history:
            for msg in history[-4:]:
                role = msg.get("role", "user").upper()
                history_text += f"{role}: {msg.get('content', '')}\n"

        full_question = f"{history_text}USER: {question}" if history_text else question

        prompt = _PROMPT.format(context=context, question=full_question)
        answer = ollama.generate(model, prompt)

        return {
            "answer": answer,
            "sources": [
                {"source": r["metadata"].get("source", "unknown"), "score": r["score"]}
                for r in results
            ],
            "model": model
        }

    def stream_ask(self, question: str, model: str = None, user_id: str = "local"):
        model = model or settings.TASK_MODELS["rag"]
        results = self.retriever.search(question, user_id=user_id)
        context = self.retriever.format_context(results)
        prompt = _PROMPT.format(context=context, question=question)
        return ollama.stream(model, prompt)
