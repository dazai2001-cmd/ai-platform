import json
from unittest.mock import Mock

import pytest

from core.config.settings import settings
from infrastructure.llm.ollama_client import OllamaClient, ProviderOutputError
from services.career.career_service import CareerService


PACK = {
    "analysis": {"fit_score": 85, "summary": "Python experience fits the role."},
    "tailored_cv": {"tailored_bullets": ["Built Flask applications."]},
    "cover_letter": {"cover_letter": "Dear Hiring Team, my Python and Flask experience fits this role."},
}


class Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def gemini_response(text, finish_reason="STOP"):
    return Response({"candidates": [{
        "content": {"parts": [{"text": text}]}, "finishReason": finish_reason,
    }]})


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "GEMINI_MODELS", ["gemini-3.5-flash"])
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(settings, "TASK_MODELS", {"career": "gemini:gemini-3.5-flash"})
    return OllamaClient()


def test_cloud_career_pack_recovers_truncated_json_and_preserves_nested_fields(gemini, monkeypatch):
    from services.career import career_service as service_object
    from importlib import import_module

    module = import_module("services.career.career_service")
    monkeypatch.setattr(module, "ollama", gemini)
    post = Mock(side_effect=[gemini_response('{"analysis":', "MAX_TOKENS"), gemini_response(json.dumps(PACK))])
    monkeypatch.setattr("infrastructure.llm.ollama_client.requests.post", post)

    result = service_object.application_pack("Built Python and Flask applications.", "Python and Flask role")

    assert not result.get("degraded", False)
    assert result["analysis"]["fit_score"] == 85
    assert result["tailored_cv"]["tailored_bullets"] == ["Built Flask applications."]
    assert result["cover_letter"]["cover_letter"] == PACK["cover_letter"]["cover_letter"]
    first = post.call_args_list[0].kwargs["json"]["generationConfig"]
    second = post.call_args_list[1].kwargs["json"]["generationConfig"]
    assert first["responseMimeType"] == "application/json"
    assert set(first["responseJsonSchema"]["required"]) == {"analysis", "tailored_cv", "cover_letter"}
    assert first["thinkingConfig"]["thinkingLevel"] == "LOW"
    assert second["maxOutputTokens"] > first["maxOutputTokens"]
    assert post.call_count == 2


@pytest.mark.parametrize("mode,reason", [
    ("truncated", "truncated_output"), ("invalid", "invalid_output"), ("unavailable", "provider_error"),
])
def test_career_fallback_explains_failure_without_exposing_provider_details(gemini, monkeypatch, mode, reason):
    from importlib import import_module

    module = import_module("services.career.career_service")
    monkeypatch.setattr(module, "ollama", gemini)
    if mode == "truncated":
        post = Mock(return_value=gemini_response('{"analysis":', "MAX_TOKENS"))
        monkeypatch.setattr("infrastructure.llm.ollama_client.requests.post", post)
    elif mode == "invalid":
        monkeypatch.setattr(gemini, "generate", Mock(return_value='{"analysis":{"fit_score":101}}'))
    else:
        monkeypatch.setattr(gemini, "generate", Mock(side_effect=RuntimeError("secret-key and private CV text")))

    result = CareerService().application_pack("Python engineer", "Python role")

    assert result["degraded"] is True
    assert result["degraded_reason"] == reason
    assert "basic local fallback" in result["warning"]
    assert "secret-key" not in json.dumps(result)
    assert "private CV text" not in json.dumps(result)
    assert 0 <= result["analysis"]["fit_score"] <= 100


def test_local_career_passes_schema_to_ollama_without_cloud_thinking_settings(monkeypatch):
    from importlib import import_module

    module = import_module("services.career.career_service")
    monkeypatch.setattr(settings, "IS_CLOUD_RUNTIME", False)
    monkeypatch.setattr(settings, "IS_PRODUCTION", False)
    monkeypatch.setattr(module, "ollama", OllamaClient())
    monkeypatch.setattr(settings, "TASK_MODELS", {"career": "local-model"})
    post = Mock(return_value=Response({"response": json.dumps(PACK), "done_reason": "stop"}))
    monkeypatch.setattr("infrastructure.llm.ollama_client.requests.post", post)

    result = CareerService().application_pack("Python engineer", "Python role")
    assert not result.get("degraded", False)
    assert post.call_args.kwargs["json"]["format"]["type"] == "object"
    assert post.call_args.kwargs["json"]["think"] is False


def test_openrouter_retries_truncated_structured_output(monkeypatch):
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(settings, "OPENROUTER_MODELS", ["test-model"])
    post = Mock(side_effect=[
        Response({"choices": [{"message": {"content": '{"fit_score":'}, "finish_reason": "length"}]}),
        Response({"choices": [{"message": {"content": '{"fit_score":85}'}, "finish_reason": "stop"}]}),
    ])
    monkeypatch.setattr("infrastructure.llm.ollama_client.requests.post", post)
    schema = {"type": "object", "properties": {"fit_score": {"type": "number"}}, "required": ["fit_score"], "additionalProperties": False}

    result = OllamaClient().generate("openrouter:test-model", "Analyze fit", max_tokens=900, json_schema=schema)
    assert json.loads(result)["fit_score"] == 85
    first = post.call_args_list[0].kwargs["json"]
    second = post.call_args_list[1].kwargs["json"]
    assert first["response_format"]["json_schema"]["schema"] == schema
    assert second["max_tokens"] > first["max_tokens"]


def test_native_provider_detects_truncated_json(monkeypatch):
    monkeypatch.setattr(settings, "IS_CLOUD_RUNTIME", False)
    monkeypatch.setattr(settings, "IS_PRODUCTION", False)
    monkeypatch.setattr("infrastructure.llm.ollama_client.requests.post", Mock(return_value=Response({
        "response": '{"fit_score":', "done_reason": "length",
    })))
    with pytest.raises(ProviderOutputError):
        OllamaClient().generate("local-model", "Analyze fit", json_format=True)
