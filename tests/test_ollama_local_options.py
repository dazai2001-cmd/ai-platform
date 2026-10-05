import json
from unittest.mock import Mock

import pytest

from core.config.settings import settings
from core.config.validation import configuration_issues
from infrastructure.llm.ollama_client import OllamaClient


@pytest.mark.parametrize("gpu_layers", [-1, 0, 4])
@pytest.mark.parametrize("stream", [False, True])
def test_local_inference_respects_gpu_choice_and_token_budget(monkeypatch, gpu_layers, stream):
    monkeypatch.setattr(settings, "IS_CLOUD_RUNTIME", False)
    monkeypatch.setattr(settings, "IS_PRODUCTION", False)
    monkeypatch.setattr(settings, "OLLAMA_NUM_GPU", gpu_layers)
    monkeypatch.setattr(settings, "LLM_MAX_TOKENS", 32)
    response = Mock()
    response.json.return_value = {"response": "OK"}
    response.iter_lines.return_value = [json.dumps({"response": "OK", "done": True}).encode()]
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    post = Mock(return_value=response)
    monkeypatch.setattr("infrastructure.llm.ollama_client.requests.post", post)
    client = OllamaClient()

    if stream:
        assert "".join(client.stream("qwen3:8b", "Reply OK")) == "OK"
    else:
        assert client.generate("qwen3:8b", "Reply OK", max_tokens=12) == "OK"

    options = post.call_args.kwargs["json"]["options"]
    assert options["num_predict"] == (32 if stream else 12)
    if gpu_layers == -1:
        assert "num_gpu" not in options
    else:
        assert options["num_gpu"] == gpu_layers


def test_invalid_gpu_layer_setting_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "OLLAMA_NUM_GPU", -2)
    assert any(issue.name == "OLLAMA_NUM_GPU" for issue in configuration_issues(settings))
