import json
import re
from pathlib import Path
from infrastructure.llm.ollama_client import ollama
from core.config.settings import settings
from core.config.constants import TASK_RAG, TASK_BI, TASK_MEMORY, TASK_GENERAL

_PROMPT = (Path(__file__).parents[2] / "core/prompts/router_prompt.txt").read_text()
_VALID = {TASK_RAG, TASK_BI, TASK_MEMORY, TASK_GENERAL}


class QueryRouter:
    def __init__(self):
        self.model = settings.ROUTER_MODEL

    def route(self, query: str) -> dict:
        prompt = _PROMPT.replace("{query}", query)
        response = ollama.generate(self.model, prompt, temperature=0.0)

        try:
            # Strip markdown fences if model adds them
            clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.strip(), flags=re.IGNORECASE)
            # Find first JSON object
            match = re.search(r"\{.*?\}", clean, re.DOTALL)
            if match:
                result = json.loads(match.group())
                if result.get("type") in _VALID:
                    return self._with_model(result)
        except Exception:
            pass

        return self._with_model({"type": TASK_RAG, "confidence": 0.5, "reasoning": "fallback"})

    @staticmethod
    def model_for_type(task_type: str) -> str:
        return settings.TASK_MODELS.get(task_type, settings.TASK_MODELS[TASK_RAG])

    def _with_model(self, route: dict) -> dict:
        task_type = route.get("type", TASK_RAG)
        route["model"] = self.model_for_type(task_type)
        return route
