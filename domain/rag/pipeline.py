from pathlib import Path
from infrastructure.llm.ollama_client import ollama
from core.config.settings import settings

_PROMPT = (Path(__file__).parents[2] / "core/prompts/rag_prompt.txt").read_text()


class QAPipeline:
    def __init__(self, retriever):
        self.retriever = retriever

    def ask(
        self,
        question: str,
        history: list[dict] = None,
        model: str = None,
        user_id: str = "local",
        retrieval_results: list[dict] | None = None,
    ) -> dict:
        model = model or settings.TASK_MODELS["rag"]
        results = retrieval_results if retrieval_results is not None else self.retriever.search(question, user_id=user_id)
        context = self.retriever.format_context(results)

        prompt = self._prompt(question, context, history)
        answer = ollama.generate(model, prompt)

        return {
            "answer": answer,
            "sources": [
                {"source": r["metadata"].get("source", "unknown"), "score": r["score"]}
                for r in results
            ],
            "model": model
        }

    @staticmethod
    def _prompt(question: str, context: str, history: list[dict] | None = None) -> str:
        # Use the same bounded conversation context for both response modes.
        history_text = ""
        if history:
            for msg in history[-4:]:
                role = msg.get("role", "user").upper()
                history_text += f"{role}: {msg.get('content', '')}\n"

        full_question = f"{history_text}USER: {question}" if history_text else question

        return _PROMPT.format(context=context, question=full_question)

    def stream_ask(self, question: str, model: str = None, user_id: str = "local", history: list[dict] | None = None):
        model = model or settings.TASK_MODELS["rag"]
        results = self.retriever.search(question, user_id=user_id)
        context = self.retriever.format_context(results)
        prompt = self._prompt(question, context, history)
        return ollama.stream(model, prompt)
