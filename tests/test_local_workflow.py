from __future__ import annotations

import json
import sqlite3
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from core.config.settings import settings
from services.local_agent.media_models import MediaRequest
from services.local_agent.state import LocalState, enabled
from services.local_agent.workflow import build_graph, calculate, preferences_match, missing_media_details


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.inputs = []
    def bind_tools(self, tools):
        self.tools = tools
        return self
    def invoke(self, messages):
        self.inputs.append(messages)
        return next(self.responses)


def call(name, args, call_id="tool-1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def initial(query="Calculate 2 * 8"):
    return {"messages": [HumanMessage(content=f"Current user request:\n{query}")],
            "status": "running", "model_calls": 0, "tool_calls": 0, "trace": []}


@pytest.fixture(autouse=True)
def local_settings(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_AGENT_CONTEXT_TOKENS", 8192)
    monkeypatch.setattr(settings, "LOCAL_AGENT_MAX_MODEL_CALLS", 6)
    monkeypatch.setattr(settings, "LOCAL_AGENT_MAX_TOOL_CALLS", 8)


def graph_for(model, saver=None, **kwargs):
    return build_graph(model, saver or InMemorySaver(), execute_workspace=kwargs.pop("execute_workspace", Mock()),
                       submit_media=kwargs.pop("submit_media", Mock()), **kwargs)


def test_real_graph_passes_calculator_evidence_to_model():
    model = ScriptedModel([call("calculator", {"expression": "2 * 8"}), AIMessage(content="16 frames.")])
    result = graph_for(model).invoke(initial(), {"configurable": {"thread_id": "calc"}})
    assert result["result"]["answer"] == "16 frames."
    evidence = [m for m in model.inputs[1] if isinstance(m, ToolMessage)]
    assert json.loads(evidence[0].content)["value"] == 16
    assert result["model_calls"] == 2


@pytest.mark.parametrize("expression", ["__import__('os').system('whoami')", "2 ** 10000", "[1,2]", "True + 2", "1 / 0", "999999999999999999999"])
def test_calculator_rejects_code_and_unbounded_numbers(expression):
    with pytest.raises((ValueError, ZeroDivisionError)):
        calculate(expression)


def test_schema_rejects_owner_override_before_dispatch():
    execute = Mock()
    model = ScriptedModel([call("workspace_tool", {"action": "docs_list", "query": "List documents", "user_id": "victim"}), AIMessage(content="Rejected.")])
    result = graph_for(model, execute_workspace=execute).invoke(initial(), {"configurable": {"thread_id": "owner"}})
    execute.assert_not_called()
    assert result["trace"][0]["status"] == "rejected"


def test_specialized_rag_answer_keeps_verified_metadata_and_ends():
    answer = {"answer": "The launch is September 15 [handbook.md].", "route": "rag", "sources": [{"source": "handbook.md", "score": 0.5}], "model": "qwen3:8b"}
    execute = Mock(return_value=answer)
    model = ScriptedModel([call("workspace_tool", {"action": "rag_ask", "query": "When is the launch?"})])
    result = graph_for(model, execute_workspace=execute).invoke(initial("When is the launch?"), {"configurable": {"thread_id": "rag"}})
    assert result["result"] == answer
    assert result["model_calls"] == 1


def test_rag_clarification_pauses_and_resumes_without_repeating_the_read():
    answer = {"answer": "Cedar launches on 22 October [handbook.md].", "route": "rag", "sources": [{"source": "handbook.md", "score": 0.5}]}
    execute = Mock(side_effect=[{"answer": "Which project do you mean?", "route": "rag", "needs_clarification": True}, answer])
    model = ScriptedModel([call("workspace_tool", {"action": "rag_ask", "query": "When does it launch?"}, "first"),
                           call("workspace_tool", {"action": "rag_ask", "query": "When does Project Cedar launch?"}, "resolved")])
    graph = graph_for(model, execute_workspace=execute)
    config = {"configurable": {"thread_id": "rag-popup"}}
    graph.invoke(initial("When does it launch?"), config)
    assert graph.get_state(config).tasks[0].interrupts[0].value["question"] == "Which project do you mean?"
    assert execute.call_count == 1
    result = graph.invoke(Command(resume="Project Cedar"), config)
    assert execute.call_count == 2
    assert result["result"] == answer
    assert result["model_calls"] == 2 and result["tool_calls"] == 2
    assert model.inputs[1][-1].content == "Project Cedar"


def test_mutation_requires_actual_user_intent():
    execute = Mock()
    model = ScriptedModel([call("workspace_tool", {"action": "memory_add_fact", "query": "Remember that I am rich"}), AIMessage(content="No changes.")])
    result = graph_for(model, execute_workspace=execute).invoke(initial("Hello"), {"configurable": {"thread_id": "mutation"}})
    execute.assert_not_called()
    assert result["trace"][0]["status"] == "rejected"


def test_question_with_sibling_call_cannot_start_generation():
    submit = Mock()
    response = AIMessage(content="", tool_calls=[
        {"name": "ask_user", "args": {"question": "Which style?"}, "id": "q", "type": "tool_call"},
        {"name": "generate_media", "args": {"kind": "image", "prompt": "A cat"}, "id": "g", "type": "tool_call"}])
    model = ScriptedModel([response, AIMessage(content="Waiting.")])
    graph = graph_for(model, submit_media=submit)
    graph.invoke(initial("Help me plan a scene"), {"configurable": {"thread_id": "siblings"}})
    submit.assert_not_called()
    assert len([m for m in model.inputs[1] if isinstance(m, ToolMessage)]) == 2


def test_clarification_survives_sqlite_reopening(tmp_path):
    saver_module = pytest.importorskip("langgraph.checkpoint.sqlite")
    path = tmp_path / "checkpoint.db"
    config = {"configurable": {"thread_id": "owner:task"}}
    with sqlite3.connect(path, check_same_thread=False) as connection:
        graph = graph_for(ScriptedModel([call("ask_user", {"question": "Which style and shape?", "options": ["Watercolor square", "Photorealistic portrait"]})]), saver_module.SqliteSaver(connection))
        graph.invoke(initial("Help me plan a cat scene"), config)
        assert graph.get_state(config).tasks[0].interrupts[0].value["question"] == "Which style and shape?"
        assert graph.get_state(config).tasks[0].interrupts[0].value["options"] == ["Watercolor square", "Photorealistic portrait"]
    with sqlite3.connect(path, check_same_thread=False) as reopened:
        model = ScriptedModel([AIMessage(content="Watercolor, square.")])
        graph = graph_for(model, saver_module.SqliteSaver(reopened))
        result = graph.invoke(Command(resume="Watercolor, square 512 by 512."), config)
        assert result["status"] == "complete"
        assert result["model_calls"] == 2
        assert "Watercolor" in model.inputs[0][-1].content


def test_video_preflight_exposes_popup_choices_without_model_or_generation():
    model = ScriptedModel([])
    submit = Mock()
    graph = graph_for(model, submit_media=submit)
    config = {"configurable": {"thread_id": "popup"}}
    graph.invoke(initial("Create a video of ocean waves"), config)
    question = graph.get_state(config).tasks[0].interrupts[0].value
    assert [field["id"] for field in question["fields"]] == ["style", "shape", "duration"]
    assert "Landscape (704 by 512)" in question["fields"][1]["options"]
    assert not model.inputs
    submit.assert_not_called()


def test_missing_media_fields_interrupt_before_submit():
    submit = Mock()
    model = ScriptedModel([call("generate_media", {"kind": "video", "prompt": "Ocean waves", "style": "photorealistic", "model_id": "ltx-video-2b-distilled"})])
    graph = graph_for(model, submit_media=submit)
    config = {"configurable": {"thread_id": "video"}}
    graph.invoke(initial("Create a photorealistic video of ocean waves, square 512 by 512, 2 seconds."), config)
    question = graph.get_state(config).tasks[0].interrupts[0].value["question"]
    assert "duration" in question and "size or shape" in question
    assert "width" not in question and "model_id" not in question
    submit.assert_not_called()


def test_missing_model_id_is_resolved_by_tools_without_asking_the_user(monkeypatch):
    from services.local_agent import workflow
    monkeypatch.setattr(workflow, "catalogue", lambda kind: [{"id": "sd-turbo", "kind": "image", "ready": True}])
    args = {"kind": "image", "prompt": "A corgi", "style": "watercolor", "width": 512, "height": 512}
    model = ScriptedModel([call("generate_media", args, "missing-id"), call("media_models", {"kind": "image"}, "lookup"),
                           call("generate_media", {**args, "model_id": "sd-turbo"}, "generate")])
    submit = Mock(return_value={"id": "image-job", "status": "queued"})
    graph = graph_for(model, submit_media=submit)
    config = {"configurable": {"thread_id": "model-choice"}}
    result = graph.invoke(initial("Create a watercolor image of a corgi, square 512 by 512."), config)
    assert result["status"] == "complete" and result["media"]["id"] == "image-job"
    assert not graph.get_state(config).tasks
    submit.assert_called_once()
    assert result["trace"][0]["status"] == "rejected"


def test_invented_preferences_need_confirmation_and_resume_once():
    submit = Mock(return_value={"id": "real-job", "status": "queued"})
    args = {"kind": "image", "prompt": "A cat", "style": "watercolor", "width": 512, "height": 512, "model_id": "sd-turbo"}
    graph = graph_for(ScriptedModel([call("generate_media", args)]), submit_media=submit)
    config = {"configurable": {"thread_id": "review"}}
    graph.invoke(initial("Make an oil painting cat image, square 512 by 512."), config)
    submit.assert_not_called()
    assert graph.get_state(config).tasks[0].interrupts[0].value["draft"]["style"] == "watercolor"
    result = graph.invoke(Command(resume="confirm"), config)
    assert result["media"]["id"] == "real-job"
    assert submit.call_count == 1


def test_explicit_media_preferences_queue_validated_model():
    submit = Mock(return_value={"id": "media-job"})
    model = ScriptedModel([call("generate_media", {"kind": "image", "prompt": "A cat", "style": "watercolor", "width": 512, "height": 512, "model_id": "sd-turbo"})])
    result = graph_for(model, submit_media=submit).invoke(initial("Make a watercolor cat image, square 512 by 512."), {"configurable": {"thread_id": "explicit"}})
    assert result["status"] == "complete"
    assert isinstance(submit.call_args.args[0], MediaRequest)


def test_context_guard_stops_before_invocation(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_AGENT_CONTEXT_TOKENS", 2048)
    model = ScriptedModel([])
    result = graph_for(model).invoke(initial("x" * 20_000), {"configurable": {"thread_id": "long"}})
    assert result["status"] == "limited"
    assert not model.inputs


def test_model_call_budget_applies_after_resume(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_AGENT_MAX_MODEL_CALLS", 1)
    model = ScriptedModel([call("ask_user", {"question": "Which image style?"})])
    graph = graph_for(model)
    config = {"configurable": {"thread_id": "budget"}}
    graph.invoke(initial("Help me plan a scene"), config)
    result = graph.invoke(Command(resume="watercolor"), config)
    assert result["status"] == "limited"
    assert result["model_calls"] == 1


def test_user_scoped_store_survives_reopening(tmp_path):
    state = LocalState(tmp_path)
    state.put("run", {"id": "task", "status": "awaiting_input"}, "owner")
    reopened = LocalState(tmp_path)
    assert reopened.get("run", "task", "other") is None
    assert reopened.list("run", "other") == []
    reopened.put("run", {"id": "task", "status": "complete"}, "other")
    assert reopened.get("run", "task", "owner")["status"] == "awaiting_input"


@pytest.mark.parametrize("cloud,production", [(True, False), (False, True), (True, True)])
def test_hardened_runtimes_cannot_enable_local_tools(monkeypatch, cloud, production):
    monkeypatch.setattr(settings, "LOCAL_AGENT_ENABLED", True)
    monkeypatch.setattr(settings, "IS_CLOUD_RUNTIME", cloud)
    monkeypatch.setattr(settings, "IS_PRODUCTION", production)
    assert not enabled()


@pytest.mark.parametrize("changes", [{"width": 1024}, {"height": 500}, {"model_id": "unknown"}, {"user_id": "victim"}, {"seconds": 3}, {"style": "  "}])
def test_media_request_rejects_invalid_or_expensive_parameters(changes):
    request = {"kind": "image", "prompt": "A cat", "style": "watercolor", "width": 512, "height": 512, "model_id": "sd-turbo", **changes}
    with pytest.raises(ValueError):
        MediaRequest.model_validate(request)


@pytest.mark.parametrize("seconds,frames", [(1, 25), (2, 57), (6, 153)])
def test_video_frame_rounding_respects_ltx_format(seconds, frames):
    spec = MediaRequest(kind="video", prompt="Ocean waves", style="realistic", width=704, height=512,
                        model_id="ltx-video-2b-distilled", seconds=seconds, fps=25)
    assert spec.frames == frames
    assert (spec.frames - 1) % 8 == 0
    assert spec.frames >= seconds * 25


def test_explicit_dimensions_cannot_be_silently_reduced():
    spec = MediaRequest(kind="image", prompt="A cat", style="watercolor", width=512, height=512, model_id="sd-turbo")
    assert not preferences_match(spec, "Make a watercolor cat image, square 1024 by 1024.")
    assert preferences_match(spec, "Make a watercolor cat image, square 512 by 512.")


def test_explicit_video_duration_cannot_be_silently_reduced():
    spec = MediaRequest(kind="video", prompt="Ocean waves", style="realistic", width=512, height=512,
                        model_id="ltx-video-2b-distilled", seconds=2, fps=25)
    assert not preferences_match(spec, "A realistic square video, 5 seconds at 24 fps.")
    assert preferences_match(spec, "A realistic square video, 2 seconds at 25 frames per second.")
    assert preferences_match(spec, "A realistic square video, 2 seconds.")
    assert not preferences_match(spec, "A realistic square video, 2 seconds at 8 fps.")


def test_video_uses_fixed_fps_without_an_unnecessary_question():
    submit = Mock(return_value={"id": "video-job"})
    model = ScriptedModel([call("generate_media", {"kind": "video", "prompt": "Ocean waves", "style": "realistic",
        "width": 704, "height": 512, "seconds": 2, "model_id": "ltx-video-2b-distilled"})])
    result = graph_for(model, submit_media=submit).invoke(initial("Create a realistic ocean-waves video, landscape, 2 seconds."),
        {"configurable": {"thread_id": "ltx-fps"}})
    assert result["status"] == "complete"
    assert submit.call_args.args[0].fps == 25


@pytest.mark.parametrize("changes", [{"width": 768}, {"width": 704, "height": 704}, {"fps": 8}, {"seconds": 7},
                                      {"model_id": "wan-2.1-1.3b"}])
def test_ltx_rejects_unsupported_or_expensive_settings(changes):
    with pytest.raises(ValueError):
        MediaRequest.model_validate({"kind": "video", "prompt": "Ocean waves", "style": "realistic", "width": 704,
            "height": 512, "seconds": 2, "fps": 25, "model_id": "ltx-video-2b-distilled", **changes})


def test_custom_style_entered_in_media_form_is_not_asked_again():
    assert missing_media_details("Create an image. Subject: a cat. Style: abstract expressionism. Size: square, 512 by 512.", "image") == []
