import importlib
from unittest.mock import Mock

import pytest

from agents import rag_agent as rag_module
from core.config.settings import settings
from domain.rag.pipeline import QAPipeline
from services.analytics.analytics_service import AnalyticsService
from services.memory.memory_service import MemoryService
from services.storage.sqlite_service import SQLiteService


@pytest.fixture
def brain(tmp_path, monkeypatch):
    database = SQLiteService(str(tmp_path / "memory.db"))
    memory_module = importlib.import_module("services.memory.memory_service")
    monkeypatch.setattr(memory_module, "db", database)
    monkeypatch.setattr(settings, "IS_PRODUCTION", True)
    monkeypatch.setattr(settings, "MAX_MEMORY_MESSAGES", 50)
    memory = MemoryService()
    analytics = AnalyticsService(tmp_path / "analytics.jsonl")
    monkeypatch.setattr(rag_module, "memory", memory)
    monkeypatch.setattr(rag_module, "analytics", analytics)
    agent = rag_module.RAGAgent()
    agent.pipeline = Mock(spec=QAPipeline)
    return agent, memory, analytics


def test_brain_stream_persists_completed_turn_and_uses_only_owners_history(brain):
    agent, memory, analytics = brain
    memory.add("shared-id", "user", "The Juniper document", user_id="owner")
    memory.add("shared-id", "assistant", "The budget is 123 dollars.", user_id="owner")
    memory.add("shared-id", "user", "Another user's secret", user_id="other")
    agent.pipeline.stream_ask.return_value = iter(["It is ", "123 dollars."])

    stream, session, model = agent.stream_ask(
        "What was that budget?", session_id="shared-id", model="chosen-model", user_id="owner",
    )
    assert analytics.summary(user_id="owner")["total_queries"] == 0
    assert "".join(stream) == "It is 123 dollars."
    assert session == "shared-id"
    assert model == "chosen-model"
    agent.pipeline.stream_ask.assert_called_once_with(
        "What was that budget?", model="chosen-model", user_id="owner",
        history=[
            {"role": "user", "content": "The Juniper document"},
            {"role": "assistant", "content": "The budget is 123 dollars."},
        ],
    )
    assert MemoryService().to_llm_format(session, user_id="owner")[-2:] == [
        {"role": "user", "content": "What was that budget?"},
        {"role": "assistant", "content": "It is 123 dollars."},
    ]
    assert memory.to_llm_format(session, user_id="other") == [
        {"role": "user", "content": "Another user's secret"},
    ]
    assert analytics.summary(user_id="owner")["by_agent"] == {"rag": 1}
    assert analytics.summary(user_id="owner")["success_rate"] == 1
    assert analytics.summary(user_id="other")["total_queries"] == 0
    assert "What was that budget?" not in analytics._log.read_text(encoding="utf-8")


@pytest.mark.parametrize("mode,error_type", [
    ("marker", "stream_error"), ("exception", "RuntimeError"),
    ("cancel", "stream_cancelled"), ("empty", "empty_response"),
])
def test_failed_or_cancelled_brain_stream_records_failure_without_partial_memory(brain, mode, error_type):
    agent, memory, analytics = brain
    closed = []

    def upstream():
        try:
            if mode == "empty":
                return
            yield "Partial answer"
            if mode == "exception":
                raise RuntimeError("provider failed")
            if mode == "marker":
                yield "[STREAM ERROR]: provider failed"
        finally:
            closed.append(True)

    agent.pipeline.stream_ask.return_value = upstream()
    stream, session, _ = agent.stream_ask("Private question", user_id="owner")
    if mode == "exception":
        with pytest.raises(RuntimeError, match="provider failed"):
            list(stream)
    elif mode == "cancel":
        assert next(stream) == "Partial answer"
        stream.close()
    else:
        list(stream)

    assert closed == [True]
    assert memory.get(session, user_id="owner") == []
    events = analytics.recent(user_id="owner")
    assert len(events) == 1
    assert events[0]["success"] is False
    assert events[0]["error_type"] == error_type
    assert "Private question" not in analytics._log.read_text(encoding="utf-8")


def test_brain_retrieval_failure_records_query_failure(brain):
    agent, memory, analytics = brain
    agent.pipeline.stream_ask.side_effect = RuntimeError("retrieval failed")
    with pytest.raises(RuntimeError, match="retrieval failed"):
        agent.stream_ask("Question", session_id="failed-session", user_id="owner")
    assert memory.get("failed-session", user_id="owner") == []
    events = analytics.recent(user_id="owner")
    assert len(events) == 1
    assert events[0]["success"] is False
    assert events[0]["error_type"] == "RuntimeError"


def test_streaming_and_regular_qa_share_bounded_conversation_context(monkeypatch):
    from domain.rag import pipeline as pipeline_module

    retriever = Mock()
    retriever.search.return_value = []
    retriever.format_context.return_value = "Juniper has a budget of 123 dollars."
    pipeline = QAPipeline(retriever)
    history = [{"role": "user", "content": "old message outside the context limit"}]
    history.extend({"role": "user", "content": f"recent message {i}"} for i in range(4))
    generate = Mock(return_value="123 dollars")
    stream = Mock(return_value=iter(["123 dollars"]))
    monkeypatch.setattr(pipeline_module.ollama, "generate", generate)
    monkeypatch.setattr(pipeline_module.ollama, "stream", stream)

    pipeline.ask("What was that?", history=history, model="chosen-model", user_id="owner")
    assert list(pipeline.stream_ask("What was that?", history=history, model="chosen-model", user_id="owner")) == ["123 dollars"]
    prompt = stream.call_args.args[1]
    assert prompt == generate.call_args.args[1]
    assert "old message outside the context limit" not in prompt
    assert all(f"recent message {i}" in prompt for i in range(4))
    assert "Juniper has a budget of 123 dollars." in prompt
    assert prompt.split("CONVERSATION HISTORY:\n", 1)[1].split("QUESTION:\n", 1)[1].startswith("What was that?")
    assert retriever.search.call_args.kwargs == {"user_id": "owner"}
