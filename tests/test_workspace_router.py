import json
from unittest.mock import Mock

import pytest

from domain.router import workspace_router as router_module


def test_workspace_uses_the_model_decision_and_executes_its_tool(monkeypatch):
    query = "Which resources can I consult? {context}"
    generate = Mock(return_value=json.dumps({
        "action": "docs_list",
        "confidence": 0.91,
        "arguments": {},
    }))
    documents = Mock(return_value=[])
    monkeypatch.setattr(router_module.ollama, "generate", generate)
    monkeypatch.setattr(router_module.rag_agent, "documents", documents)

    result = router_module.WorkspaceRouter().handle(
        query, session_id="chat-1", user_id="owner"
    )

    generate.assert_called_once()
    prompt = generate.call_args.args[1]
    assert query in prompt
    assert '"action": "one allowed action"' in prompt
    assert result["workspace_action"] == "docs_list"
    assert result["workspace_router"]["source"] == "model"
    documents.assert_called_once_with(user_id="owner")


@pytest.mark.parametrize("response", ["not JSON", '{"action": "unknown_tool"}'])
def test_workspace_falls_back_when_the_model_returns_an_invalid_action(monkeypatch, response):
    generate = Mock(return_value=response)
    monkeypatch.setattr(router_module.ollama, "generate", generate)

    decision = router_module.WorkspaceRouter().route("Hello there")

    generate.assert_called_once()
    assert decision["action"] == "general_chat"
    assert decision["router_source"] == "fallback"
