import json
import requests
from typing import Optional, Iterator
from core.config.settings import settings


class OllamaClient:
    """
    Unified interface for all local LLM calls via Ollama.
    """

    def __init__(self):
        self.base_url = settings.OLLAMA_BASE_URL.rstrip("/")

    def _provider_for(self, model: str) -> tuple[str, str]:
        if model.startswith("gemini:"):
            return "gemini", model.split(":", 1)[1]
        if model.startswith("openrouter:"):
            return "openrouter", model.split(":", 1)[1]
        if settings.IS_CLOUD_RUNTIME:
            return "gemini", (settings.GEMINI_MODELS[0] if settings.GEMINI_MODELS else model)
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
            return self._generate_gemini(provider_model, prompt, temperature, max_tokens, json_format)
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
            raise RuntimeError(f"Ollama request failed: {e}") from e

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
            models = []
            if settings.GEMINI_API_KEY:
                models.extend(f"gemini:{model}" for model in settings.GEMINI_MODELS)
            if settings.OPENROUTER_API_KEY:
                models.extend(f"openrouter:{model}" for model in settings.OPENROUTER_MODELS)
            return models
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
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens or settings.LLM_MAX_TOKENS,
            },
        }
        if json_format:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        try:
            r = requests.post(
                f"{settings.GEMINI_BASE_URL}/models/{model}:generateContent",
                params={"key": settings.GEMINI_API_KEY},
                json=payload,
                timeout=settings.OLLAMA_TIMEOUT_SECONDS,
            )
            r.raise_for_status()
            data = r.json()
            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            return "".join(part.get("text", "") for part in parts).strip()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Gemini request failed: {e}") from e

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
        if json_format:
            payload["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": settings.APP_PUBLIC_URL,
            "X-Title": "AI Platform",
        }
        try:
            r = requests.post(
                f"{settings.OPENROUTER_BASE_URL}/chat/completions",
                json=payload,
                headers=headers,
                timeout=settings.OLLAMA_TIMEOUT_SECONDS,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"OpenRouter request failed: {e}") from e


ollama = OllamaClient()
