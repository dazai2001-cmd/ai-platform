from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from application.retrieval.retriever import Retriever
from core.config.settings import settings
from domain.rag import agentic_retrieval as workflow
from domain.rag import pipeline as pipeline_module
from domain.rag.pipeline import QAPipeline


class ScriptedModel:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.inputs = []

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages):
        self.inputs.append(messages)
        return next(self.responses)


def call(name, args, identifier="call-1"):
    return AIMessage(content="", tool_calls=[{
        "name": name, "args": args, "id": identifier, "type": "tool_call",
    }])


def chunk(source="handbook.md", text="Project Atlas launches on 15 September 2026.", owner="owner"):
    return {"score": 0.1, "metadata": {"source": source, "text": text, "user_id": owner}}


@pytest.fixture(autouse=True)
def local_agentic_settings(monkeypatch):
    monkeypatch.setattr(settings, "RAG_AGENTIC_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_AGENTIC_MODELS", ["qwen3:8b"])
    monkeypatch.setattr(settings, "IS_CLOUD_RUNTIME", False)
    monkeypatch.setattr(settings, "RAG_AGENTIC_MAX_SEARCHES", 2)
    monkeypatch.setattr(settings, "RAG_AGENTIC_MAX_MODEL_CALLS", 4)
    monkeypatch.setattr(settings, "RAG_AGENTIC_CONTEXT_TOKENS", 4096)


def setup_pipeline(monkeypatch, *responses, retrieved=None):
    model = ScriptedModel(*responses)
    monkeypatch.setattr(workflow, "create_retrieval_model", Mock(return_value=model))
    retriever = Mock()
    retriever.search.return_value = [chunk()] if retrieved is None else retrieved
    retriever.format_context.side_effect = Retriever(None, None).format_context
    generate = Mock(return_value="Project Atlas launches on 15 September 2026 [handbook.md].")
    monkeypatch.setattr(pipeline_module.ollama, "generate", generate)
    return QAPipeline(retriever), retriever, model, generate


def test_native_search_and_verified_sources_feed_the_existing_answer_client(monkeypatch):
    pipeline, retriever, model, generate = setup_pipeline(monkeypatch,
        call("search_knowledge_base", {"query": "Project Atlas launch"}),
        call("finish_retrieval", {"answerable": True, "sources": ["handbook.md"]}, "call-2"))
    result = pipeline.ask("When does Atlas launch?", model="qwen3:8b", user_id="owner")
    retriever.search.assert_called_once_with("Project Atlas launch", user_id="owner")
    assert result["agentic"]["searches"] == 1
    assert result["agentic"]["planning_calls"] == 2
    assert result["sources"] == [{"source": "handbook.md", "score": 0.1}]
    assert "15 September 2026" in generate.call_args.args[1]
    assert result["retrieved_contexts"] == [chunk()["metadata"]["text"]]
    assert any(isinstance(message, ToolMessage) for message in model.inputs[-1])


def test_two_searches_combine_evidence_for_a_multihop_answer(monkeypatch):
    pipeline, retriever, _, generate = setup_pipeline(monkeypatch,
        call("search_knowledge_base", {"query": "Atlas approver role"}),
        call("search_knowledge_base", {"query": "Release captain name"}, "call-2"),
        call("finish_retrieval", {"answerable": True, "sources": ["handbook.md", "directory.md"], "person_name": "Avery Chen"}, "call-3"))
    retriever.search.side_effect = [
        [chunk(text="Atlas launch is approved by the release captain.")],
        [chunk("directory.md", "The release captain is Avery Chen.")],
    ]
    result = pipeline.ask("Who approves Atlas? Give the person's name.", user_id="owner")
    assert result["agentic"]["searches"] == 2
    assert {item["source"] for item in result["sources"]} == {"handbook.md", "directory.md"}
    assert "release captain" in generate.call_args.args[1]
    assert "Avery Chen" in generate.call_args.args[1]


def test_other_users_chunks_never_reach_the_planner_or_answer(monkeypatch):
    pipeline, _, model, generate = setup_pipeline(monkeypatch,
        call("search_knowledge_base", {"query": "Atlas"}),
        call("finish_retrieval", {"answerable": True, "sources": ["handbook.md"]}, "call-2"),
        retrieved=[chunk(), chunk("secret.md", "OTHER-USER-SECRET", owner="other")])
    result = pipeline.ask("When does Atlas launch?", user_id="owner")
    assert "OTHER-USER-SECRET" not in str(model.inputs)
    assert "OTHER-USER-SECRET" not in generate.call_args.args[1]
    assert "secret.md" not in str(result)


def test_role_only_person_answer_triggers_another_search(monkeypatch):
    pipeline, retriever, model, generate = setup_pipeline(monkeypatch,
        call("search_knowledge_base", {"query": "Atlas approver"}),
        call("finish_retrieval", {"answerable": True, "sources": ["handbook.md"], "person_name": "release captain"}, "call-2"),
        call("search_knowledge_base", {"query": "release captain name"}, "call-3"),
        call("finish_retrieval", {"answerable": True, "sources": ["handbook.md", "directory.md"], "person_name": "Avery Chen"}, "call-4"))
    retriever.search.side_effect = [
        [chunk(text="The release captain approves the Atlas launch.")],
        [chunk("directory.md", "The release captain is Avery Chen.")],
    ]
    result = pipeline.ask("Who approves Atlas? Give the person's name.", user_id="owner")
    assert result["agentic"]["searches"] == 2
    assert result["agentic"]["planning_calls"] == 4
    assert any(isinstance(message, ToolMessage) and message.status == "error" for message in model.inputs[2])
    assert "Avery Chen" in generate.call_args.args[1]


def test_invented_person_name_cannot_pass_literal_evidence_validation(monkeypatch):
    pipeline, _, _, generate = setup_pipeline(monkeypatch,
        call("search_knowledge_base", {"query": "Atlas approver"}),
        call("finish_retrieval", {"answerable": True, "sources": ["handbook.md"], "person_name": "Invented Person"}, "call-2"),
        call("finish_retrieval", {"answerable": False, "sources": []}, "call-3"))
    result = pipeline.ask("Who approves Atlas? Give the person's name.", user_id="owner")
    assert result["answer"] == workflow.INSUFFICIENT_EVIDENCE
    generate.assert_not_called()


def test_retry_diversifies_repeated_top_hit_and_keeps_the_linking_evidence(monkeypatch):
    monkeypatch.setattr(settings, "TOP_K", 1)
    pipeline, retriever, model, generate = setup_pipeline(monkeypatch,
        call("search_knowledge_base", {"query": "Atlas approver"}),
        call("search_knowledge_base", {"query": "Atlas release captain name"}, "call-2"),
        call("finish_retrieval", {"answerable": True, "sources": ["handbook.md", "directory.md"], "person_name": "Avery Chen"}, "call-3"))
    first = chunk(text="The release captain approves the Atlas launch.")
    second = chunk("directory.md", "The release captain is Avery Chen.")
    retriever.search.side_effect = [[first], [first, second]]
    result = pipeline.ask("Who approves Atlas? Give the person's name.", user_id="owner")
    assert retriever.search.call_args.kwargs == {"k": 2, "user_id": "owner"}
    last_tool = next(message for message in reversed(model.inputs[-1]) if isinstance(message, ToolMessage))
    assert "directory.md" in last_tool.content
    assert "handbook.md" not in last_tool.content
    assert {item["source"] for item in result["sources"]} == {"handbook.md", "directory.md"}
    assert "The release captain approves" in generate.call_args.args[1]


def test_unknown_source_selection_is_rejected_before_answer_generation(monkeypatch):
    pipeline, _, model, generate = setup_pipeline(monkeypatch,
        call("search_knowledge_base", {"query": "Atlas"}),
        call("finish_retrieval", {"answerable": True, "sources": ["invented.md"]}, "call-2"),
        call("finish_retrieval", {"answerable": False, "sources": []}, "call-3"))
    result = pipeline.ask("Question", user_id="owner")
    generate.assert_not_called()
    assert result["answer"] == workflow.INSUFFICIENT_EVIDENCE
    assert result["sources"] == []
    assert any(isinstance(message, ToolMessage) and message.status == "error" for message in model.inputs[-1])


def test_clarification_returns_a_question_without_retrieval_or_answer_generation(monkeypatch):
    pipeline, retriever, _, generate = setup_pipeline(monkeypatch,
        call("ask_clarification", {"question": "Which project do you mean?"}))
    result = pipeline.ask("Please clarify which document to use.", user_id="owner")
    assert result["answer"] == "Which project do you mean?"
    assert result["needs_clarification"] is True
    retriever.search.assert_not_called()
    generate.assert_not_called()


def test_followup_history_is_available_but_bounded(monkeypatch):
    pipeline, _, model, _ = setup_pipeline(monkeypatch,
        call("search_knowledge_base", {"query": "Project Atlas launch"}),
        call("finish_retrieval", {"answerable": True, "sources": ["handbook.md"]}, "call-2"))
    history = [{"role": "user", "content": "EXCLUDED-OLDER-TURN"}]
    history += [{"role": "user", "content": "Project Atlas"}, {"role": "assistant", "content": "x" * 800},
                {"role": "user", "content": "When?"}, {"role": "assistant", "content": "We were discussing Atlas."}]
    pipeline.ask("When does it launch?", history=history, user_id="owner")
    assert "Project Atlas" in str(model.inputs[0])
    assert "EXCLUDED-OLDER-TURN" not in str(model.inputs[0])
    assert max(len(message.content) for message in model.inputs[0][1:-1]) <= 500


def test_streaming_and_json_modes_use_identical_verified_context(monkeypatch):
    responses = [
        call("search_knowledge_base", {"query": "Atlas"}),
        call("finish_retrieval", {"answerable": True, "sources": ["handbook.md"]}, "call-2"),
    ]
    pipeline, _, _, generate = setup_pipeline(monkeypatch, *responses)
    factory = Mock(side_effect=[ScriptedModel(*responses), ScriptedModel(*responses)])
    monkeypatch.setattr(workflow, "create_retrieval_model", factory)
    stream = Mock(return_value=iter(["15 September ", "2026"]))
    monkeypatch.setattr(pipeline_module.ollama, "stream", stream)
    pipeline.ask("When?", user_id="owner")
    assert "".join(pipeline.stream_ask("When?", user_id="owner")) == "15 September 2026"
    assert generate.call_args.args[1] == stream.call_args.args[1]


def test_streamed_clarification_does_not_start_an_answer_stream(monkeypatch):
    pipeline, _, _, _ = setup_pipeline(monkeypatch,
        call("ask_clarification", {"question": "Which project?"}))
    stream = Mock()
    monkeypatch.setattr(pipeline_module.ollama, "stream", stream)
    assert list(pipeline.stream_ask("Please clarify which document to use.", user_id="owner")) == ["Which project?"]
    stream.assert_not_called()


def test_loop_stops_at_search_limit_without_generating_unsupported_answer(monkeypatch):
    pipeline, retriever, _, generate = setup_pipeline(monkeypatch, *[
        call("search_knowledge_base", {"query": f"query {index}"}, f"call-{index}") for index in range(3)
    ])
    result = pipeline.ask("Question", user_id="owner")
    assert retriever.search.call_count == 2
    assert result["agentic"]["status"] == "limited"
    generate.assert_not_called()


def test_tool_cannot_override_authenticated_user_id(monkeypatch):
    pipeline, retriever, _, generate = setup_pipeline(monkeypatch,
        call("search_knowledge_base", {"query": "secrets", "user_id": "other"}),
        call("ask_clarification", {"question": "Which document?"}, "call-2"))
    pipeline.ask("Question", user_id="owner")
    retriever.search.assert_not_called()
    generate.assert_not_called()


def test_precomputed_retrieval_is_reused_and_evaluation_sees_selected_context(monkeypatch):
    pipeline, retriever, _, _ = setup_pipeline(monkeypatch,
        call("finish_retrieval", {"answerable": True, "sources": ["handbook.md"]}))
    result = pipeline.ask("Question", retrieval_results=[chunk()], user_id="owner")
    retriever.search.assert_not_called()
    assert result["retrieved_contexts"] == [chunk()["metadata"]["text"]]


def test_context_guard_stops_before_any_model_or_retriever_call(monkeypatch):
    pipeline, retriever, model, generate = setup_pipeline(monkeypatch)
    result = pipeline.ask("x" * 15_000, user_id="owner")
    assert result["agentic"]["status"] == "context_limit"
    assert result["needs_clarification"] is True
    assert model.inputs == []
    retriever.search.assert_not_called()
    generate.assert_not_called()


def test_unresolved_reference_is_checked_before_model_or_retrieval(monkeypatch):
    pipeline, retriever, model, generate = setup_pipeline(monkeypatch)
    result = pipeline.ask("When does it launch?", user_id="owner")
    assert result["needs_clarification"] is True
    assert result["agentic"]["searches"] == result["agentic"]["planning_calls"] == 0
    assert "Which project" in result["answer"]
    assert model.inputs == []
    retriever.search.assert_not_called()
    generate.assert_not_called()


def test_missing_document_fact_cannot_be_asked_as_clarification_after_search(monkeypatch):
    pipeline, _, _, generate = setup_pipeline(monkeypatch,
        call("search_knowledge_base", {"query": "Atlas CEO phone"}),
        call("ask_clarification", {"question": "Is the release captain the CEO?"}, "call-2"),
        call("finish_retrieval", {"answerable": False, "sources": []}, "call-3"))
    result = pipeline.ask("What is the Atlas CEO's phone number?", user_id="owner")
    assert result["needs_clarification"] is False
    assert result["answer"] == workflow.INSUFFICIENT_EVIDENCE
    generate.assert_not_called()


def test_conversation_recall_keeps_existing_history_based_answers(monkeypatch):
    pipeline, retriever, _, generate = setup_pipeline(monkeypatch,
        call("finish_retrieval", {"answerable": True, "sources": [], "evidence": "conversation"}))
    result = pipeline.ask("What nickname did I ask you to use?",
                          history=[{"role": "user", "content": "Please call me Mango."}], user_id="owner")
    retriever.search.assert_not_called()
    assert result["sources"] == []
    assert "Please call me Mango." in generate.call_args.args[1]


@pytest.mark.parametrize("cloud,model,enabled", [
    (True, "gemini:gemini-3.5-flash", True),
    (False, "openrouter:chosen", True),
    (False, "llama3:latest", True),
    (False, "qwen3:8b", False),
])
def test_cloud_and_disabled_modes_keep_existing_single_pass_flow(monkeypatch, cloud, model, enabled):
    monkeypatch.setattr(settings, "IS_CLOUD_RUNTIME", cloud)
    monkeypatch.setattr(settings, "RAG_AGENTIC_ENABLED", enabled)
    pipeline, retriever, _, generate = setup_pipeline(monkeypatch)
    result = pipeline.ask("Question", model=model, user_id="owner")
    retriever.search.assert_called_once_with("Question", user_id="owner")
    generate.assert_called_once()
    assert "agentic" not in result
    workflow.create_retrieval_model.assert_not_called()


def test_retrieval_model_preserves_local_allowlist_and_gpu_configuration(monkeypatch):
    init = Mock()
    monkeypatch.setattr(workflow, "init_chat_model", init)
    monkeypatch.setattr(settings, "IS_PRODUCTION", True)
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_MODELS", ["qwen3:8b"])
    monkeypatch.setattr(settings, "OLLAMA_NUM_GPU", 0)
    with pytest.raises(ValueError, match="allow-list"):
        workflow.create_retrieval_model("unapproved-model")
    init.assert_not_called()
    workflow.create_retrieval_model("qwen3:8b")
    assert init.call_args.kwargs["num_gpu"] == 0
    assert init.call_args.kwargs["num_ctx"] == 4096
    assert init.call_args.kwargs["reasoning"] is False
