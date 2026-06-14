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

    def generate(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None
    ) -> str:
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

    def health(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return []


ollama = OllamaClient()
