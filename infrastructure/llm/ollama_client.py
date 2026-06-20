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

    def _is_openai_compatible_model(self, model: str) -> bool:
        return model in settings.OPENAI_COMPAT_MODELS

    def generate(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        json_format: bool = False
    ) -> str:
        if self._is_openai_compatible_model(model):
            return self._generate_openai_compatible(model, prompt, temperature, max_tokens, json_format)

        max_tokens = max_tokens or settings.LLM_MAX_TOKENS
        payload = {
            "model": model,
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
        if self._is_openai_compatible_model(model):
            yield from self._stream_openai_compatible(model, prompt, temperature)
            return

        payload = {
            "model": model,
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

    def _generate_openai_compatible(
        self,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: Optional[int],
        json_format: bool,
    ) -> str:
        if not settings.OPENAI_COMPAT_API_KEY:
            raise RuntimeError("OpenAI-compatible API key is not configured")

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max(max_tokens or settings.LLM_MAX_TOKENS, settings.OPENAI_COMPAT_MIN_TOKENS),
            "stream": False,
        }
        if json_format:
            payload["response_format"] = {"type": "json_object"}

        try:
            r = requests.post(
                f"{settings.OPENAI_COMPAT_BASE_URL}/chat/completions",
                headers=self._openai_compatible_headers(),
                json=payload,
                timeout=settings.OPENAI_COMPAT_TIMEOUT_SECONDS,
            )
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"OpenAI-compatible request failed: {e}") from e
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError("OpenAI-compatible response did not include assistant content") from e

    def _stream_openai_compatible(self, model: str, prompt: str, temperature: float) -> Iterator[str]:
        if not settings.OPENAI_COMPAT_API_KEY:
            yield "[STREAM ERROR]: OpenAI-compatible API key is not configured"
            return

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": settings.OPENAI_COMPAT_MIN_TOKENS,
            "stream": True,
        }
        try:
            with requests.post(
                f"{settings.OPENAI_COMPAT_BASE_URL}/chat/completions",
                headers=self._openai_compatible_headers(),
                json=payload,
                stream=True,
                timeout=settings.OPENAI_COMPAT_TIMEOUT_SECONDS,
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content
        except Exception as e:
            yield f"[STREAM ERROR]: {str(e)}"

    @staticmethod
    def _openai_compatible_headers() -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.OPENAI_COMPAT_API_KEY}",
            "Content-Type": "application/json",
        }

    def health(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def cloud_configured(self) -> bool:
        return bool(settings.OPENAI_COMPAT_API_KEY and settings.OPENAI_COMPAT_MODELS)

    def list_models(self) -> list[str]:
        models = []
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]
        except Exception:
            models = []
        if self.cloud_configured():
            models.extend(model for model in settings.OPENAI_COMPAT_MODELS if model not in models)
        return models


ollama = OllamaClient()
