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
        if self._agentic_enabled(model):
            prepared = self._prepare_agentic(question, history, model, user_id, retrieval_results)
            answer = prepared["direct_answer"]
            if answer is None:
                answer = ollama.generate(model, self._prompt(question, prepared["context"], history))
            return {
                "answer": answer, "sources": self._sources(prepared["results"]), "model": model,
                "needs_clarification": bool(prepared["clarification"]),
                "retrieved_contexts": [item["metadata"]["text"] for item in prepared["results"]],
                "agentic": prepared["metadata"],
            }
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
    def _agentic_enabled(model: str) -> bool:
        return (settings.RAG_AGENTIC_ENABLED and not settings.IS_CLOUD_RUNTIME
                and model in settings.RAG_AGENTIC_MODELS
                and not model.startswith(("gemini:", "openrouter:")))

    @staticmethod
    def _sources(results: list[dict]) -> list[dict]:
        return [{"source": item["metadata"].get("source", "unknown"), "score": item["score"]} for item in results]

    def _prepare_agentic(self, question, history, model, user_id, retrieval_results=None):
        from domain.rag.agentic_retrieval import INSUFFICIENT_EVIDENCE, retrieve_agentically

        prepared = retrieve_agentically(self.retriever, question, model=model, user_id=user_id,
                                       history=history, initial_results=retrieval_results)
        prepared["context"] = self.retriever.format_context(prepared["results"])
        prepared["direct_answer"] = prepared["clarification"] or (None if prepared["answerable"] else INSUFFICIENT_EVIDENCE)
        # A clarification/abstention does not cite chunks as supporting an answer.
        if prepared["direct_answer"] is not None:
            prepared["results"] = []
        prepared["metadata"] = {
            "enabled": True, "status": prepared["status"],
            "searches": len(prepared["search_queries"]), "search_queries": prepared["search_queries"],
            "planning_calls": prepared["model_calls"],
        }
        return prepared

    @staticmethod
    def _prompt(question: str, context: str, history: list[dict] | None = None) -> str:
        # Use the same bounded conversation context for both response modes.
        history_text = ""
        if history:
            for msg in history[-4:]:
                role = msg.get("role", "user").upper()
                history_text += f"{role}: {str(msg.get('content', ''))[:1000]}\n"

        return _PROMPT.format(
            context=context, history=history_text or "(no prior conversation)", question=question,
        )

    def stream_ask(self, question: str, model: str = None, user_id: str = "local", history: list[dict] | None = None):
        model = model or settings.TASK_MODELS["rag"]
        if self._agentic_enabled(model):
            prepared = self._prepare_agentic(question, history, model, user_id)
            if prepared["direct_answer"] is not None:
                return iter([prepared["direct_answer"]])
            return ollama.stream(model, self._prompt(question, prepared["context"], history))
        results = self.retriever.search(question, user_id=user_id)
        context = self.retriever.format_context(results)
        prompt = self._prompt(question, context, history)
        return ollama.stream(model, prompt)
