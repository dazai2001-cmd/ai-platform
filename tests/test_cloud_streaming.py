import json
from unittest.mock import Mock

import pytest
import requests

from core.config.settings import settings
from infrastructure.llm.ollama_client import OllamaClient


class StreamResponse:
    def __init__(self, events, status=200):
        self.events = events
        self.status = status
        self.closed = False
        self.read_events = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def raise_for_status(self):
        if self.status >= 400:
            response = requests.Response()
            response.status_code = self.status
            raise requests.HTTPError("Secret key and private prompt", response=response)

    def iter_lines(self, **kwargs):
        assert kwargs == {"chunk_size": 1, "decode_unicode": True}
        yield ": provider processing"
        yield ""
        for event in self.events:
            self.read_events += 1
            data = json.dumps(event, ensure_ascii=False) if isinstance(event, dict) else event
            yield f"data: {data}".encode("utf-8")
            yield b""


def gemini(text, finish=None, thought=False):
    candidate = {"content": {"parts": [{"text": text, "thought": thought}]}}
    if finish:
        candidate["finishReason"] = finish
    return {"candidates": [candidate]}


def openrouter(text, finish=None):
    return {"choices": [{"delta": {"content": text}, "finish_reason": finish}]}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "GEMINI_MODELS", ["gemini-3.5-flash"])
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(settings, "OPENROUTER_MODELS", ["test/model"])
    return OllamaClient()


def test_gemini_delivers_incremental_text_without_thoughts_or_lost_spaces(client, monkeypatch):
    response = StreamResponse([
        gemini("Hidden reasoning", thought=True),
        gemini("Hello "), gemini("café\n", "STOP"),
    ])
    post = Mock(return_value=response)
    monkeypatch.setattr("infrastructure.llm.ollama_client.requests.post", post)
    tokens = client.stream("gemini:gemini-3.5-flash", "Private prompt")

    assert next(tokens) == "Hello "
    assert response.read_events == 2
    assert not response.closed
    assert list(tokens) == ["café\n"]
    assert response.closed
    assert post.call_args.args[0].endswith(":streamGenerateContent")
    assert post.call_args.kwargs["params"] == {"alt": "sse"}
    assert post.call_args.kwargs["json"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "LOW"}


def test_openrouter_handles_comments_unicode_usage_and_done(client, monkeypatch):
    response = StreamResponse([
        openrouter("Hello "), openrouter("世界", "stop"),
        {"choices": [], "usage": {"total_tokens": 3}}, "[DONE]",
    ])
    post = Mock(return_value=response)
    monkeypatch.setattr("infrastructure.llm.ollama_client.requests.post", post)
    assert list(client.stream("openrouter:test/model", "Prompt")) == ["Hello ", "世界"]
    assert response.closed
    assert post.call_args.kwargs["json"]["stream"] is True


@pytest.mark.parametrize("provider,first", [
    ("gemini:gemini-3.5-flash", gemini("First ")),
    ("openrouter:test/model", openrouter("First ")),
])
def test_cancelling_cloud_stream_closes_the_live_provider_connection(client, monkeypatch, provider, first):
    response = StreamResponse([first, first])
    monkeypatch.setattr("infrastructure.llm.ollama_client.requests.post", Mock(return_value=response))
    tokens = client.stream(provider, "Prompt")
    assert next(tokens) == "First "
    tokens.close()
    assert response.closed
    assert response.read_events == 1


def test_gemini_rate_limit_falls_back_before_emitting_any_answer(client, monkeypatch):
    limited = StreamResponse([], status=429)
    recovered = StreamResponse([openrouter("Recovered", "stop"), "[DONE]"])
    post = Mock(side_effect=[limited, recovered])
    monkeypatch.setattr("infrastructure.llm.ollama_client.requests.post", post)
    assert list(client.stream("gemini:gemini-3.5-flash", "Prompt")) == ["Recovered"]
    assert limited.closed and recovered.closed
    assert post.call_count == 2


@pytest.mark.parametrize("provider,event", [
    ("gemini:gemini-3.5-flash", gemini("Partial")),
    ("gemini:gemini-3.5-flash", gemini("Partial", "MAX_TOKENS")),
    ("openrouter:test/model", openrouter("Partial")),
    ("openrouter:test/model", openrouter("Partial", "length")),
])
def test_disconnected_or_truncated_stream_is_failed_without_mixing_provider_answers(client, monkeypatch, provider, event):
    response = StreamResponse([event])
    post = Mock(return_value=response)
    monkeypatch.setattr("infrastructure.llm.ollama_client.requests.post", post)
    tokens = list(client.stream(provider, "Prompt"))
    assert tokens[0] == "Partial"
    assert tokens[-1].startswith("[STREAM ERROR]:")
    assert "incomplete response" in tokens[-1]
    assert post.call_count == 1
    assert response.closed


@pytest.mark.parametrize("event", [
    {"error": {"message": "Secret key and private prompt"}}, "not-json",
])
def test_stream_protocol_errors_do_not_expose_raw_provider_data(client, monkeypatch, event):
    response = StreamResponse([event])
    post = Mock(return_value=response)
    monkeypatch.setattr("infrastructure.llm.ollama_client.requests.post", post)
    answer = "".join(client.stream("openrouter:test/model", "Prompt"))
    assert answer.startswith("[STREAM ERROR]:")
    assert "Secret key" not in answer
    assert "private prompt" not in answer
    assert response.closed


def test_sse_multiline_data_fields_are_parsed_as_one_event():
    response = Mock()
    response.iter_lines.return_value = iter([
        'event: message', 'data: {"choices":', 'data: []}', '',
    ])
    assert list(OllamaClient._sse_events(response, "OpenRouter")) == [{"choices": []}]


def test_regular_gemini_chat_also_uses_the_lower_latency_thinking_setting(client, monkeypatch):
    response = Mock()
    response.json.return_value = gemini("Answer", "STOP")
    post = Mock(return_value=response)
    monkeypatch.setattr("infrastructure.llm.ollama_client.requests.post", post)
    assert client.generate("gemini:gemini-3.5-flash", "Prompt") == "Answer"
    assert post.call_args.kwargs["json"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "LOW"}
