import json
import re
import requests
from typing import Optional, Iterator
from core.config.settings import settings


class ProviderOutputError(RuntimeError):
    def __init__(self, provider: str):
        self.reason = "truncated_output"
        super().__init__(f"{provider} returned truncated structured output.")


class OllamaClient:
    """
    Unified interface for all local LLM calls via Ollama.
    """

    def __init__(self):
        self.base_url = settings.OLLAMA_BASE_URL.rstrip("/")

    @staticmethod
    def _local_options(temperature: float, max_tokens: Optional[int] = None) -> dict:
        options = {
            "temperature": temperature,
            "num_predict": max_tokens or settings.LLM_MAX_TOKENS,
        }
        if settings.OLLAMA_NUM_GPU >= 0:
            options["num_gpu"] = settings.OLLAMA_NUM_GPU
        return options

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
        json_format: bool = False,
        json_schema: Optional[dict] = None,
    ) -> str:
        json_format = json_format or json_schema is not None
        provider, provider_model = self._provider_for(model)
        if provider == "gemini":
            try:
                return self._generate_gemini(provider_model, prompt, temperature, max_tokens, json_format, json_schema)
            except RuntimeError as e:
                if self._can_fallback_to_openrouter(e):
                    return self._generate_openrouter(
                        settings.OPENROUTER_MODELS[0],
                        prompt,
                        temperature,
                        max_tokens,
                        json_format,
                        json_schema,
                    )
                raise
        if provider == "openrouter":
            return self._generate_openrouter(provider_model, prompt, temperature, max_tokens, json_format, json_schema)

        payload = {
            "model": provider_model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": self._local_options(temperature, max_tokens),
        }
        if json_format:
            payload["format"] = json_schema if json_schema is not None else "json"

        try:
            r = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=settings.OLLAMA_TIMEOUT_SECONDS
            )
            r.raise_for_status()
            data = r.json()
            if json_format and data.get("done_reason") == "length":
                raise ProviderOutputError("Ollama")
            return data["response"]
        except requests.exceptions.RequestException as e:
            raise RuntimeError(self._safe_provider_error("Ollama", e)) from e

    def stream(self, model: str, prompt: str, temperature: float = 0.2) -> Iterator[str]:
        provider, provider_model = self._provider_for(model)
        if provider != "ollama":
            tokens = (
                self._stream_gemini(provider_model, prompt, temperature)
                if provider == "gemini" else self._stream_openrouter(provider_model, prompt, temperature)
            )
            emitted = False
            try:
                try:
                    for token in tokens:
                        emitted = emitted or bool(token.strip())
                        yield token
                except RuntimeError as error:
                    # Switching providers after output begins would join two
                    # different answers and could save an incomplete turn.
                    if provider == "gemini" and not emitted and self._can_fallback_to_openrouter(error):
                        yield from self._stream_openrouter(settings.OPENROUTER_MODELS[0], prompt, temperature)
                    else:
                        raise
            except Exception as e:
                yield f"[STREAM ERROR]: {str(e)}"
            finally:
                tokens.close()
            return

        payload = {
            "model": provider_model,
            "prompt": prompt,
            "stream": True,
            "think": False,
            "options": self._local_options(temperature)
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

    @staticmethod
    def _gemini_thinking_config(model: str) -> dict:
        if model.startswith("gemini-3") and "image" not in model:
            return {"thinkingConfig": {"thinkingLevel": "LOW"}}
        if model.startswith("gemini-2.5-flash") and "image" not in model:
            return {"thinkingConfig": {"thinkingBudget": 0}}
        return {}

    @staticmethod
    def _sse_events(response, provider: str) -> Iterator[dict]:
        data_lines = []

        def decode():
            payload = "\n".join(data_lines)
            if payload == "[DONE]":
                return None
            try:
                event = json.loads(payload)
            except (TypeError, ValueError) as error:
                raise RuntimeError(f"{provider} returned invalid streaming data.") from error
            if not isinstance(event, dict):
                raise RuntimeError(f"{provider} returned invalid streaming data.")
            if event.get("error"):
                # Provider errors can echo prompts or credentials. Never put
                # their raw SSE payload into the browser or application logs.
                raise RuntimeError(f"{provider} request failed.")
            return event

        for line in response.iter_lines(chunk_size=1, decode_unicode=True):
            if isinstance(line, bytes):
                line = line.decode("utf-8")
            if not line:
                if data_lines:
                    event = decode()
                    data_lines = []
                    if event is None:
                        return
                    yield event
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))
            # SSE comments, event names, ids and retry hints contain no answer.
        if data_lines:
            event = decode()
            if event is not None:
                yield event

    def _stream_gemini(self, model: str, prompt: str, temperature: float) -> Iterator[str]:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": settings.LLM_MAX_TOKENS,
                **self._gemini_thinking_config(model),
            },
        }
        complete = False
        answered = False
        try:
            with requests.post(
                f"{settings.GEMINI_BASE_URL}/models/{model}:streamGenerateContent",
                params={"alt": "sse"},
                headers={"x-goog-api-key": settings.GEMINI_API_KEY},
                json=payload, stream=True, timeout=settings.CLOUD_LLM_TIMEOUT_SECONDS,
            ) as response:
                response.raise_for_status()
                for event in self._sse_events(response, "Gemini"):
                    candidates = event.get("candidates")
                    candidate = candidates[0] if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) else {}
                    content = candidate.get("content")
                    parts = content.get("parts", []) if isinstance(content, dict) else []
                    for part in parts if isinstance(parts, list) else []:
                        if not isinstance(part, dict):
                            continue
                        text = part.get("text")
                        if isinstance(text, str) and text and not part.get("thought"):
                            answered = answered or bool(text.strip())
                            yield text
                    finish = candidate.get("finishReason")
                    if finish == "STOP":
                        complete = True
                    elif finish:
                        raise RuntimeError("Gemini returned an incomplete response.")
        except requests.exceptions.RequestException as error:
            raise RuntimeError(self._safe_provider_error("Gemini", error)) from error
        if not answered:
            raise RuntimeError("Gemini returned no user-facing answer.")
        if not complete:
            raise RuntimeError("Gemini returned an incomplete response.")

    def _stream_openrouter(self, model: str, prompt: str, temperature: float) -> Iterator[str]:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": settings.LLM_MAX_TOKENS,
            "stream": True,
        }
        complete = False
        answered = False
        try:
            with requests.post(
                f"{settings.OPENROUTER_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": settings.APP_PUBLIC_URL,
                    "X-Title": "AI Platform",
                },
                json=payload, stream=True, timeout=settings.CLOUD_LLM_TIMEOUT_SECONDS,
            ) as response:
                response.raise_for_status()
                for event in self._sse_events(response, "OpenRouter"):
                    choices = event.get("choices")
                    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                    delta = choice.get("delta")
                    text = delta.get("content") if isinstance(delta, dict) else None
                    if isinstance(text, str) and text:
                        answered = answered or bool(text.strip())
                        yield text
                    finish = choice.get("finish_reason")
                    if finish == "stop":
                        complete = True
                    elif finish:
                        raise RuntimeError("OpenRouter returned an incomplete response.")
        except requests.exceptions.RequestException as error:
            raise RuntimeError(self._safe_provider_error("OpenRouter", error)) from error
        if not answered:
            raise RuntimeError("OpenRouter returned no user-facing answer.")
        if not complete:
            raise RuntimeError("OpenRouter returned an incomplete response.")

    def _generate_gemini(
        self,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: Optional[int],
        json_format: bool,
        json_schema: Optional[dict] = None,
    ) -> str:
        if not settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        try:
            output_tokens = max_tokens or settings.LLM_MAX_TOKENS
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
                        "maxOutputTokens": output_tokens,
                        **self._gemini_thinking_config(model),
                    },
                }
                if json_format:
                    payload["generationConfig"]["responseMimeType"] = "application/json"
                if json_schema is not None:
                    payload["generationConfig"]["responseJsonSchema"] = json_schema

                r = requests.post(
                    f"{settings.GEMINI_BASE_URL}/models/{model}:generateContent",
                    headers={"x-goog-api-key": settings.GEMINI_API_KEY},
                    json=payload,
                    timeout=settings.CLOUD_LLM_TIMEOUT_SECONDS,
                )
                r.raise_for_status()
                data = r.json()
                candidates = data.get("candidates") if isinstance(data, dict) else None
                candidate = candidates[0] if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) else {}
                if json_format and candidate.get("finishReason") == "MAX_TOKENS":
                    if attempt == 0:
                        output_tokens = min(output_tokens * 2, 8192)
                        continue
                    raise ProviderOutputError("Gemini")
                answer = self._gemini_answer_text(data)
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
        json_schema: Optional[dict] = None,
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
                request_payload["response_format"] = (
                    {"type": "json_schema", "json_schema": {"name": "response", "strict": True, "schema": json_schema}}
                    if json_schema is not None else {"type": "json_object"}
                )
            try:
                r = requests.post(
                    f"{settings.OPENROUTER_BASE_URL}/chat/completions",
                    json=request_payload,
                    headers=headers,
                    timeout=settings.CLOUD_LLM_TIMEOUT_SECONDS,
                )
                r.raise_for_status()
                data = r.json()
                choices = data.get("choices") if isinstance(data, dict) else None
                choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                if json_format and choice.get("finish_reason") == "length":
                    if attempt == 0:
                        payload["max_tokens"] = min(payload["max_tokens"] * 2, 8192)
                        continue
                    raise ProviderOutputError("OpenRouter")
                answer = self._openrouter_answer_text(data)
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
            or isinstance(error, ProviderOutputError)
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
