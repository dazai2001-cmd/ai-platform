from infrastructure.llm.ollama_client import ollama

_CRITIC_PROMPT = """You are a quality critic for an AI assistant.

Review the following answer and score it on:
1. Accuracy (does it answer the question?)
2. Completeness (is anything missing?)
3. Grounding (is it based on the context, not hallucinated?)

Return ONLY JSON:
{{
  "score": 0.0-1.0,
  "accurate": true/false,
  "complete": true/false,
  "grounded": true/false,
  "feedback": "one sentence of feedback",
  "improved_answer": "improved version if score < 0.7, else null"
}}

QUESTION: {question}
CONTEXT: {context}
ANSWER: {answer}
"""


class CriticAgent:
    """
    Reviews agent outputs for quality using the same query-selected model as the answer.
    """

    def review(self, question: str, answer: str, context: str = "", model: str = "") -> dict:
        import json
        import re

        prompt = _CRITIC_PROMPT.format(
            question=question,
            context=context[:2000] if context else "No context provided",
            answer=answer,
        )

        response = ollama.generate(model, prompt, temperature=0.1)

        try:
            clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.strip(), flags=re.IGNORECASE)
            match = re.search(r"\{.*\}", clean, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception:
            pass

        return {
            "score": 0.5,
            "feedback": "Could not parse critic response",
            "improved_answer": None,
        }

    def review_rag_result(self, question: str, result: dict, model: str = "") -> dict:
        answer = result.get("answer", "")
        sources = result.get("sources", [])
        context = "\n".join([s.get("source", "") for s in sources])
        return self.review(question, answer, context, model=model or result.get("model", ""))


critic_agent = CriticAgent()
