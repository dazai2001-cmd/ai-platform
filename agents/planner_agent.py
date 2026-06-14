import json
import re
from infrastructure.llm.ollama_client import ollama
from core.config.settings import settings
from core.config.constants import TASK_RAG, TASK_BI, TASK_MEMORY, TASK_GENERAL

_PLAN_PROMPT = """You are a task planner for an AI platform.

Break the user's request into a sequence of steps.
Each step has a type (rag, bi, memory, general) and a sub-question.

Return ONLY JSON:
{{
  "steps": [
    {{"type": "rag", "question": "sub-question 1"}},
    {{"type": "bi", "question": "sub-question 2"}}
  ],
  "reasoning": "why these steps"
}}

User request: {query}
"""


class PlannerAgent:
    """
    Breaks complex multi-part queries into steps and
    routes each step to the right agent.
    """

    def __init__(self):
        self.model = settings.ROUTER_MODEL

    def plan(self, query: str) -> dict:
        prompt = _PLAN_PROMPT.format(query=query)
        response = ollama.generate(self.model, prompt, temperature=0.1)

        try:
            clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.strip(), flags=re.IGNORECASE)
            match = re.search(r"\{.*\}", clean, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception:
            pass

        # Fallback: single RAG step
        return {
            "steps": [{"type": TASK_RAG, "question": query}],
            "reasoning": "fallback single step"
        }

    def execute(self, query: str, session_id: str = None) -> dict:
        """Plan and execute all steps, returning combined results."""
        from agents.rag_agent import rag_agent
        from agents.bi_agent import bi_agent

        plan = self.plan(query)
        results = []

        for step in plan.get("steps", []):
            step_type = step.get("type", TASK_RAG)
            question = step.get("question", query)

            if step_type == TASK_BI:
                result = bi_agent.ask(question, session_id=session_id)
            else:
                result = rag_agent.ask(question, session_id=session_id)

            results.append({"type": step_type, "result": result})

        return {
            "plan": plan,
            "results": results,
            "combined_answer": "\n\n".join(
                r["result"].get("answer", "") for r in results
            )
        }


planner_agent = PlannerAgent()
