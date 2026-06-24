import json
import re
import time
from typing import Any

from core.config.constants import TASK_CAREER
from core.config.settings import settings
from infrastructure.llm.ollama_client import ollama


class CareerService:
    """
    CV and job-application assistant powered by the platform's local Ollama client.

    Inspired by MIT-licensed AIHawk-style workflows, but implemented natively here:
    parse/score/tailor first, then let browser automation be a later approval step.
    """

    def __init__(self):
        self._cloud_cooldown_until = 0.0

    def analyze_fit(self, cv_text: str, job_description: str, model: str | None = None) -> dict[str, Any]:
        prompt = self._analysis_prompt(cv_text, job_description)
        selected_model = self._select_model(model)
        raw, used_model = self._generate_with_fallback(
            selected_model,
            prompt,
            temperature=0.1,
            max_tokens=900,
            json_format=True,
        )
        result = self._normalize_analysis(self._json_or_fallback(raw, "analysis"))
        result["model"] = used_model
        if used_model != selected_model:
            result["requested_model"] = selected_model
        return result

    def tailor_cv(self, cv_text: str, job_description: str, model: str | None = None) -> dict[str, Any]:
        prompt = self._tailor_prompt(cv_text, job_description)
        selected_model = self._select_model(model)
        raw, used_model = self._generate_with_fallback(selected_model, prompt, temperature=0.2, max_tokens=1100)
        result = self._json_or_fallback(raw, "tailored_cv")
        result["model"] = used_model
        if used_model != selected_model:
            result["requested_model"] = selected_model
        return result

    def draft_cover_letter(self, cv_text: str, job_description: str, model: str | None = None) -> dict[str, Any]:
        prompt = self._cover_letter_prompt(cv_text, job_description)
        selected_model = self._select_model(model)
        raw, used_model = self._generate_with_fallback(selected_model, prompt, temperature=0.35, max_tokens=700)
        result = self._json_or_fallback(raw, "cover_letter")
        result["model"] = used_model
        if used_model != selected_model:
            result["requested_model"] = selected_model
        return result

    def application_pack(self, cv_text: str, job_description: str, model: str | None = None) -> dict[str, Any]:
        selected_model = self._select_model(model)
        raw, used_model = self._generate_with_fallback(
            selected_model,
            self._pack_prompt(cv_text, job_description),
            temperature=0.2,
            max_tokens=1400,
            json_format=True,
        )
        pack = self._normalize_pack(self._json_or_fallback(raw, "application_pack"))
        pack["model"] = used_model
        if used_model != selected_model:
            pack["requested_model"] = selected_model
        pack["automation_note"] = (
            "This pack is ready for human review. Automatic submission should be "
            "added only with explicit approval per application."
        )
        return pack

    def application_pack_for_match(
        self,
        cv_text: str,
        job_description: str,
        analysis: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        selected_model = self._select_model(model)
        raw, used_model = self._generate_with_fallback(
            selected_model,
            self._match_pack_prompt(cv_text, job_description, analysis),
            temperature=0.2,
            max_tokens=650,
            json_format=True,
        )
        generated = self._json_or_fallback(raw, "application_pack")
        pack = {
            "analysis": analysis,
            "tailored_cv": generated.get("tailored_cv"),
            "cover_letter": generated.get("cover_letter"),
            "model": used_model,
        }
        if used_model != selected_model:
            pack["requested_model"] = selected_model
        if generated.get("warning"):
            pack["warning"] = generated["warning"]
        return pack

    def _select_model(self, override: str | None = None) -> str:
        return override or settings.TASK_MODELS.get(TASK_CAREER, settings.ROUTER_MODEL)

    def _generate_with_fallback(
        self,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
        json_format: bool = False,
    ) -> tuple[str, str]:
        fallback_model = settings.OPENAI_COMPAT_FALLBACK_MODEL
        can_fallback = (
            model in settings.OPENAI_COMPAT_MODELS
            and fallback_model
            and fallback_model != model
        )
        if can_fallback and time.time() < self._cloud_cooldown_until:
            return (
                ollama.generate(fallback_model, prompt, temperature=temperature, max_tokens=max_tokens, json_format=json_format),
                fallback_model,
            )

        try:
            return (
                ollama.generate(model, prompt, temperature=temperature, max_tokens=max_tokens, json_format=json_format),
                model,
            )
        except RuntimeError as e:
            if not (can_fallback and self._is_rate_limit_error(e)):
                raise
            self._cloud_cooldown_until = time.time() + settings.OPENAI_COMPAT_COOLDOWN_SECONDS
            return (
                ollama.generate(fallback_model, prompt, temperature=temperature, max_tokens=max_tokens, json_format=json_format),
                fallback_model,
            )

    @staticmethod
    def _is_rate_limit_error(error: Exception) -> bool:
        message = str(error).lower()
        return "429" in message or "too many requests" in message or "rate limit" in message

    def _analysis_prompt(self, cv_text: str, job_description: str) -> str:
        return f"""
You are a careful career-fit analyst. Use only facts present in the CV/profile.
Return strict JSON with this schema:
{{
  "fit_score": 0-100,
  "summary": "one short paragraph",
  "matched_skills": ["..."],
  "missing_or_weak_signals": ["..."],
  "risks": ["..."],
  "recommended_cv_emphasis": ["..."],
  "application_decision": "apply|maybe|skip",
  "truth_check": ["claims the candidate can safely make"]
}}

CV/profile:
\"\"\"{cv_text}\"\"\"

Job description:
\"\"\"{job_description}\"\"\"
""".strip()

    def _tailor_prompt(self, cv_text: str, job_description: str) -> str:
        return f"""
You tailor CVs without inventing anything. Use only verified facts from the CV/profile.
Mirror important job-description keywords only when they are honestly supported.
Return strict JSON with this schema:
{{
  "headline": "targeted CV headline",
  "professional_summary": "3-4 lines",
  "priority_keywords": ["..."],
  "tailored_bullets": ["achievement-style bullets"],
  "suggested_section_order": ["..."],
  "changes_made": ["..."],
  "do_not_claim": ["unsupported claims to avoid"]
}}

CV/profile:
\"\"\"{cv_text}\"\"\"

Job description:
\"\"\"{job_description}\"\"\"
""".strip()

    def _cover_letter_prompt(self, cv_text: str, job_description: str) -> str:
        return f"""
Draft a concise cover letter using only the CV/profile facts. Keep it natural,
specific, and under 300 words. Do not invent company research.
Return strict JSON with this schema:
{{
  "cover_letter": "letter text",
  "customization_notes": ["..."],
  "questions_for_user": ["missing details that would improve it"]
}}

CV/profile:
\"\"\"{cv_text}\"\"\"

Job description:
\"\"\"{job_description}\"\"\"
""".strip()

    def _pack_prompt(self, cv_text: str, job_description: str) -> str:
        return f"""
/no_think
You are a careful career assistant. Produce a concise application pack using
only facts present in the CV/profile. Do not invent experience, metrics,
education, companies, authorization status, or tools.

Return strict JSON with exactly these top-level keys:
{{
  "analysis": {{
    "fit_score": 0-100,
    "summary": "one short paragraph",
    "matched_skills": ["max 6 items"],
    "missing_or_weak_signals": ["max 5 items"],
    "risks": ["max 4 items"],
    "application_decision": "apply|maybe|skip"
  }},
  "tailored_cv": {{
    "headline": "targeted CV headline",
    "professional_summary": "2-3 lines",
    "priority_keywords": ["max 8 items"],
    "tailored_bullets": ["max 6 achievement-style bullets"],
    "do_not_claim": ["unsupported claims to avoid"]
  }},
  "cover_letter": {{
    "cover_letter": "80-120 words",
    "customization_notes": ["max 3 items"],
    "questions_for_user": ["max 3 items"]
  }}
}}

CV/profile:
\"\"\"{cv_text}\"\"\"

Job description:
\"\"\"{job_description}\"\"\"
""".strip()

    def _match_pack_prompt(
        self,
        cv_text: str,
        job_description: str,
        analysis: dict[str, Any],
    ) -> str:
        return f"""
/no_think
Create a concise application pack using only facts in the CV/profile. The job
has already been scored, so do not repeat the fit analysis. Do not invent
experience, metrics, employers, education, tools, or authorization status.

Return strict JSON with exactly these top-level keys:
{{
  "tailored_cv": {{
    "headline": "maximum 12 words",
    "professional_summary": "2 concise sentences",
    "priority_keywords": ["maximum 6 items"],
    "tailored_bullets": ["maximum 4 concise bullets"],
    "do_not_claim": ["maximum 3 items"]
  }},
  "cover_letter": {{
    "cover_letter": "90-120 words",
    "customization_notes": ["maximum 2 items"],
    "questions_for_user": ["maximum 2 items"]
  }}
}}

Existing fit analysis for context:
{json.dumps(analysis, ensure_ascii=True)}

CV/profile:
<cv>{cv_text}</cv>

Job description:
<job>{job_description}</job>
""".strip()

    def _json_or_fallback(self, raw: str, key: str) -> dict[str, Any]:
        text = raw.strip()
        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()

        parsed = self._parse_json_object(text)
        if parsed is not None:
            return parsed if isinstance(parsed, dict) else {key: parsed}
        return {key: raw, "warning": "Model returned non-JSON output."}

    @staticmethod
    def _parse_json_object(text: str) -> Any | None:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        decoder = json.JSONDecoder()
        for match in re.finditer(r"[\[{]", text):
            try:
                parsed, _ = decoder.raw_decode(text[match.start():])
                return parsed
            except json.JSONDecodeError:
                continue
        return None

    @staticmethod
    def _normalize_analysis(result: dict[str, Any]) -> dict[str, Any]:
        nested = result.get("analysis")
        if isinstance(nested, dict):
            result = {**nested, **{key: value for key, value in result.items() if key != "analysis"}}

        score = result.get("fit_score")
        if isinstance(score, str):
            match = re.search(r"\d+(?:\.\d+)?", score)
            if match:
                result["fit_score"] = float(match.group()) if "." in match.group() else int(match.group())

        decision = result.get("application_decision")
        if isinstance(decision, str):
            normalized = decision.strip().lower()
            if normalized in {"apply", "maybe", "skip"}:
                result["application_decision"] = normalized
        return result

    def _normalize_pack(self, pack: dict[str, Any]) -> dict[str, Any]:
        nested = pack.get("application_pack")
        if isinstance(nested, dict):
            return {**nested, **{key: value for key, value in pack.items() if key != "application_pack"}}

        if isinstance(nested, str):
            parsed = self._json_or_fallback(nested, "application_pack")
            if any(key in parsed for key in ("analysis", "tailored_cv", "cover_letter")):
                extras = {key: value for key, value in pack.items() if key != "application_pack"}
                return {**parsed, **extras}

        return pack


career_service = CareerService()
