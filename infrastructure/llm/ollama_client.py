import json
import re
import requests
from typing import Optional, Iterator
from core.config.settings import settings


class OllamaClient:
    """
    Unified interface for all local LLM calls via Ollama.
    """

    def __init__(self):
        self.base_url = settings.OLLAMA_BASE_URL.rstrip("/")

    @staticmethod
    def _configured_cloud_models() -> set[str]:
        models: set[str] = set()
        if settings.GEMINI_API_KEY:
            models.update(f"gemini:{model}" for model in settings.GEMINI_MODELS)
        if settings.OPENROUTER_API_KEY:
            models.update(f"openrouter:{model}" for model in settings.OPENROUTER_MODELS)
        return models

    def _provider_for(self, model: str) -> tuple[str, str]:
        model = (model or "").strip()
        if not model:
            raise ValueError("model is required")

        configured_cloud_models = self._configured_cloud_models()
        if model.startswith("gemini:"):
            if model not in configured_cloud_models:
                raise ValueError("model is not in the configured allow-list")
            return "gemini", model.split(":", 1)[1]
        if model.startswith("openrouter:"):
            if model not in configured_cloud_models:
                raise ValueError("model is not in the configured allow-list")
            return "openrouter", model.split(":", 1)[1]
        if settings.IS_CLOUD_RUNTIME:
            raise ValueError("cloud models must use a configured provider model")
        if settings.IS_PRODUCTION and model not in set(settings.LOCAL_ALLOWED_MODELS):
            raise ValueError("model is not in the configured allow-list")
        return "ollama", model

    def generate(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        json_format: bool = False
    ) -> str:
        provider, provider_model = self._provider_for(model)
        if provider == "gemini":
            try:
                return self._generate_gemini(provider_model, prompt, temperature, max_tokens, json_format)
            except RuntimeError as e:
                if self._can_fallback_to_openrouter(e):
                    return self._generate_openrouter(
                        settings.OPENROUTER_MODELS[0],
                        prompt,
                        temperature,
                        max_tokens,
                        json_format,
                    )
                raise
        if provider == "openrouter":
            return self._generate_openrouter(provider_model, prompt, temperature, max_tokens, json_format)

        max_tokens = max_tokens or settings.LLM_MAX_TOKENS
        payload = {
            "model": provider_model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if json_format:
            payload["format"] = "json"

        try:
            r = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=settings.OLLAMA_TIMEOUT_SECONDS
            )
            r.raise_for_status()
            return r.json()["response"]
        except requests.exceptions.RequestException as e:
            raise RuntimeError(self._safe_provider_error("Ollama", e)) from e

    def stream(self, model: str, prompt: str, temperature: float = 0.2) -> Iterator[str]:
        provider, provider_model = self._provider_for(model)
        if provider != "ollama":
            try:
                yield self.generate(model, prompt, temperature=temperature)
            except Exception as e:
                yield f"[STREAM ERROR]: {str(e)}"
            return

        payload = {
            "model": provider_model,
            "prompt": prompt,
            "stream": True,
            "think": False,
            "options": {"temperature": temperature}
        }

        try:
            with requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                stream=True,
                timeout=settings.OLLAMA_TIMEOUT_SECONDS
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if line:
                        data = json.loads(line)
                        if "response" in data:
                            yield data["response"]
                        if data.get("done"):
                            break
        except Exception as e:
            yield f"[STREAM ERROR]: {str(e)}"

    def health(self) -> bool:
        if settings.IS_CLOUD_RUNTIME:
            return bool(settings.GEMINI_API_KEY or settings.OPENROUTER_API_KEY)
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        if settings.IS_CLOUD_RUNTIME:
            return sorted(self._configured_cloud_models())
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return []

    def _generate_gemini(
        self,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: Optional[int],
        json_format: bool,
    ) -> str:
        if not settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        try:
            for attempt in range(2):
                effective_prompt = prompt
                if attempt:
                    effective_prompt += (
                        "\n\nReturn only the user-facing answer. Do not output internal "
                        "safety labels, routing labels, or hidden reasoning."
                    )
                payload = {
                    "contents": [{"role": "user", "parts": [{"text": effective_prompt}]}],
                    "generationConfig": {
                        "temperature": temperature,
                        "maxOutputTokens": max_tokens or settings.LLM_MAX_TOKENS,
                    },
                }
                if json_format:
                    payload["generationConfig"]["responseMimeType"] = "application/json"

                r = requests.post(
                    f"{settings.GEMINI_BASE_URL}/models/{model}:generateContent",
                    headers={"x-goog-api-key": settings.GEMINI_API_KEY},
                    json=payload,
                    timeout=settings.CLOUD_LLM_TIMEOUT_SECONDS,
                )
                r.raise_for_status()
                answer = self._gemini_answer_text(r.json())
                if answer and not self._internal_label_only(answer):
                    return answer

            raise RuntimeError("Gemini returned no user-facing answer.")
        except requests.exceptions.RequestException as e:
            raise RuntimeError(self._safe_provider_error("Gemini", e)) from e

    @staticmethod
    def _gemini_answer_text(data: dict) -> str:
        candidates = data.get("candidates") if isinstance(data, dict) else None
        first = candidates[0] if isinstance(candidates, list) and candidates else {}
        content = first.get("content") if isinstance(first, dict) else {}
        parts = content.get("parts") if isinstance(content, dict) else []
        if not isinstance(parts, list):
            return ""
        return "".join(
            str(part.get("text") or "")
            for part in parts
            if isinstance(part, dict) and not part.get("thought")
        ).strip()

    @staticmethod
    def _internal_label_only(answer: str) -> bool:
        return bool(re.fullmatch(
            r"(?:user\s+)?safety\s*:\s*(?:safe|unsafe|blocked|allowed)",
            answer.strip(),
            flags=re.IGNORECASE,
        ))

    def _generate_openrouter(
        self,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: Optional[int],
        json_format: bool,
    ) -> str:
        if not settings.OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens or settings.LLM_MAX_TOKENS,
        }
        headers = {
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": settings.APP_PUBLIC_URL,
            "X-Title": "AI Platform",
        }
        enforce_json = json_format
        for attempt in range(2):
            request_payload = dict(payload)
            if enforce_json:
                request_payload["response_format"] = {"type": "json_object"}
            try:
                r = requests.post(
                    f"{settings.OPENROUTER_BASE_URL}/chat/completions",
                    json=request_payload,
                    headers=headers,
                    timeout=settings.CLOUD_LLM_TIMEOUT_SECONDS,
                )
                r.raise_for_status()
                answer = self._openrouter_answer_text(r.json())
                if answer:
                    return answer
                if attempt < 1:
                    continue
                raise RuntimeError("OpenRouter returned no user-facing answer.")
            except requests.exceptions.RequestException as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if attempt < 1 and enforce_json and status == 400:
                    enforce_json = False
                    continue
                if attempt < 1 and (status is None or status == 429 or status >= 500):
                    continue
                raise RuntimeError(self._safe_provider_error("OpenRouter", e)) from e

        raise RuntimeError("OpenRouter request failed.")

    @staticmethod
    def _openrouter_answer_text(data: dict) -> str:
        choices = data.get("choices") if isinstance(data, dict) else None
        first = choices[0] if isinstance(choices, list) and choices else {}
        message = first.get("message") if isinstance(first, dict) else {}
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict)
            ).strip()
        return ""

    def _can_fallback_to_openrouter(self, error: RuntimeError) -> bool:
        if not settings.OPENROUTER_API_KEY or not settings.OPENROUTER_MODELS:
            return False
        message = str(error)
        return bool(
            re.search(r"\((?:429 rate limit|5\d\d)\)", message)
            or "Too Many Requests" in message
            or message == "Gemini request failed."
            or message == "Gemini returned no user-facing answer."
        )

    @staticmethod
    def _safe_provider_error(provider: str, error: requests.exceptions.RequestException) -> str:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        if status == 429:
            return f"{provider} request failed (429 rate limit)."
        if status:
            return f"{provider} request failed ({status})."
        return f"{provider} request failed."


ollama = OllamaClient()
