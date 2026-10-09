"""Offline graph mechanics tests. These do not prove a real model's accuracy."""

import json

import pytest

# A default repository pytest run must still work without experiment packages.
pytest.importorskip("langgraph.checkpoint.sqlite", reason="Install experiments/agent_workflow/requirements.txt")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .workflow import TOOLS_BY_NAME, build_graph


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


def tool_call(name, args, call_id="call-1"):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def start(prompt="Test task"):
    return {"messages": [HumanMessage(content=prompt)], "model_calls": 0,
            "tool_calls": 0, "status": "running"}


def config(thread="test-task"):
    return {"configurable": {"thread_id": thread}, "recursion_limit": 20}


def test_native_tool_result_is_passed_back_to_model():
    model = ScriptedModel(AIMessage(content="", tool_calls=[tool_call("multiply", {"a": 5, "b": 24})]),
                          AIMessage(content="120 frames."))
    result = build_graph(model, InMemorySaver()).invoke(start(), config())
    assert result["status"] == "complete"
    assert result["model_calls"] == 2
    assert result["tool_calls"] == 1
    tool_message = next(message for message in model.inputs[-1] if isinstance(message, ToolMessage))
    assert json.loads(tool_message.content) == {"value": 120}
    assert tool_message.tool_call_id == "call-1"


def test_multiple_tool_steps_preserve_previous_results():
    model = ScriptedModel(
        AIMessage(content="", tool_calls=[tool_call("list_demo_models", {"media_type": "video"})]),
        AIMessage(content="", tool_calls=[tool_call("multiply", {"a": 5, "b": 24}, "call-2")]),
        AIMessage(content="120 frames; the demo video worker is unavailable."),
    )
    result = build_graph(model, InMemorySaver()).invoke(start(), config())
    tools = [message for message in model.inputs[-1] if isinstance(message, ToolMessage)]
    assert len(tools) == 2
    assert json.loads(tools[0].content)["models"][0]["available"] is False
    assert json.loads(tools[1].content)["value"] == 120
    assert result["tool_calls"] == 2


def test_clarification_survives_reopening_sqlite_and_preserves_thread_isolation(tmp_path):
    database = str(tmp_path / "checkpoints.sqlite")
    first_model = ScriptedModel(AIMessage(content="", tool_calls=[
        tool_call("ask_user", {"question": "What style should the cat image have?"}),
    ]))
    with SqliteSaver.from_conn_string(database) as saver:
        graph = build_graph(first_model, saver)
        paused = graph.invoke(start("Plan a cat image"), config())
        assert paused["__interrupt__"][0].value["question"] == "What style should the cat image have?"
        assert paused["tool_calls"] == 0

    resumed_model = ScriptedModel(
        AIMessage(content="", tool_calls=[tool_call("list_demo_models", {"media_type": "image"}, "call-2")]),
        AIMessage(content="A watercolor cat using demo-image-worker; simulation only."),
    )
    with SqliteSaver.from_conn_string(database) as saver:
        graph = build_graph(resumed_model, saver)
        assert graph.get_state(config("other-task")).values == {}
        result = graph.invoke(Command(resume="Watercolor, square format"), config())
        assert result["status"] == "complete"
        assert result["model_calls"] == 3
        assert result["tool_calls"] == 2
        assert any(isinstance(message, HumanMessage) and message.content == "Plan a cat image"
                   for message in resumed_model.inputs[0])
        answer = next(message for message in resumed_model.inputs[0] if isinstance(message, ToolMessage))
        assert json.loads(answer.content)["user_answer"] == "Watercolor, square format"


def test_clarification_pauses_before_sibling_tool_executes(monkeypatch):
    executed = []
    monkeypatch.setattr(type(TOOLS_BY_NAME["multiply"]), "invoke", lambda self, args: executed.append(args) or "120")
    model = ScriptedModel(
        AIMessage(content="", tool_calls=[
            tool_call("multiply", {"a": 5, "b": 24}),
            tool_call("ask_user", {"question": "Which style?"}, "call-2"),
        ]), AIMessage(content="Done"),
    )
    graph = build_graph(model, InMemorySaver())
    graph.invoke(start(), config())
    assert executed == []
    graph.invoke(Command(resume="Watercolor"), config())
    assert executed == [{"a": 5, "b": 24}]


@pytest.mark.parametrize("call", [
    tool_call("multiply", {"a": "not a number", "b": 24}),
    tool_call("list_demo_models", {"media_type": "audio"}),
    tool_call("unknown_tool", {}),
    tool_call("ask_user", {"question": " "}),
])
def test_invalid_tool_calls_return_paired_error_without_crashing(call):
    model = ScriptedModel(AIMessage(content="", tool_calls=[call]), AIMessage(content="Tool rejected."))
    result = build_graph(model, InMemorySaver()).invoke(start(), config())
    error = next(message for message in result["messages"] if isinstance(message, ToolMessage))
    assert error.status == "error"
    assert error.tool_call_id == call["id"]
    assert result["status"] == "complete"


def test_model_loop_stops_at_budget():
    model = ScriptedModel(*[AIMessage(content="", tool_calls=[
        tool_call("multiply", {"a": 5, "b": 24}, f"call-{index}"),
    ]) for index in range(2)])
    result = build_graph(model, InMemorySaver(), max_model_calls=2).invoke(start(), config())
    assert result["status"] == "limited"
    assert result["model_calls"] == 2
    assert len(model.inputs) == 2


def test_tool_budget_rejects_whole_batch_before_execution():
    model = ScriptedModel(AIMessage(content="", tool_calls=[
        tool_call("multiply", {"a": 5, "b": 24}, f"call-{index}") for index in range(2)
    ]))
    result = build_graph(model, InMemorySaver(), max_tool_calls=1).invoke(start(), config())
    assert result["status"] == "limited"
    assert result["tool_calls"] == 0
    assert all(message.status == "error" for message in result["messages"] if isinstance(message, ToolMessage))


def test_oversized_input_stops_before_model_invocation():
    model = ScriptedModel()
    result = build_graph(model, InMemorySaver()).invoke(start("x" * 11_000), config())
    assert result["status"] == "limited"
    assert model.inputs == []
