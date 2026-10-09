"""Small, bounded agent with native tools and a durable clarification pause."""

import json
import math
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from pydantic import BaseModel, Field, ValidationError


class MultiplyInput(BaseModel):
    a: float = Field(ge=-1_000_000, le=1_000_000)
    b: float = Field(ge=-1_000_000, le=1_000_000)


class QuestionInput(BaseModel):
    question: str = Field(min_length=1, max_length=400)


@tool(args_schema=MultiplyInput)
def multiply(a: float, b: float) -> str:
    """Multiply two numbers, for example video duration by frames per second."""
    value = a * b
    if not math.isfinite(value):
        raise ValueError("The result must be finite.")
    return json.dumps({"value": value})


@tool
def list_demo_models(media_type: Literal["image", "video"]) -> str:
    """Look up the simulated image/video model catalogue. No models are run."""
    return json.dumps({
        "simulation": True,
        "models": [{
            "id": f"demo-{media_type}-worker",
            "media_type": media_type,
            "available": media_type == "image",
            "description": "Fixture for routing tests; not an installed generation model.",
        }],
    })


@tool(args_schema=QuestionInput)
def ask_user(question: str) -> str:
    """Ask one focused question when essential user requirements are missing."""
    # The graph intercepts this tool and pauses before executing any other tool.
    raise RuntimeError("ask_user must be handled by the graph's clarification node.")


TOOLS = [multiply, list_demo_models, ask_user]
TOOLS_BY_NAME = {item.name: item for item in TOOLS}
SYSTEM_PROMPT = """You are Qwen, testing a local agent workflow.
Use native tool calls, never write tool-call JSON as ordinary text.
Use multiply for arithmetic; base your final answer on the returned value.
For image planning, the required details are subject, visual style, and size
or aspect ratio. For video planning, also require duration. Check the user's
request and any previous clarification answers before choosing your next tool.
If any required detail is missing, your NEXT action MUST be ask_user. Ask a
single focused question covering the missing details. Do not call the model
catalogue yet, invent defaults, or ask a question in ordinary final text.
For example, a request for a mountain picture lacks style and size: call
ask_user to ask for those details first. A watercolor square mountain image
already has all required details, so look up the image catalogue immediately.
Call ask_user by itself and wait for the answer. Once all required details
are present, call list_demo_models before selecting a model. Only choose an
available model from that tool's response. The catalogue is simulated; no
image/video has been generated. Clearly say so when giving a plan. If none
is available, explain that limitation. Answer concisely when you have results.
"""


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    model_calls: int
    tool_calls: int
    status: str


def build_graph(model: Any, checkpointer: Any, *, max_model_calls: int = 6,
                max_tool_calls: int = 8, max_prompt_chars: int = 10_000):
    """Use LangChain tool binding inside an explicit LangGraph execution loop.

    Tools are read-only fixtures. Budgets apply to the whole checkpointed task,
    including resumes. The character cap is a coarse input guard, not a tokenizer.
    """
    if min(max_model_calls, max_tool_calls, max_prompt_chars) < 1:
        raise ValueError("All experiment budgets must be positive.")
    bound_model = model.bind_tools(TOOLS)

    def agent(state: AgentState):
        if state.get("model_calls", 0) >= max_model_calls:
            return {"messages": [AIMessage(content="Stopped: model-call limit reached.")],
                    "status": "limited"}
        messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
        size = sum(len(json.dumps(message.model_dump(), default=str)) for message in messages)
        size += sum(len(json.dumps(item.args, default=str)) for item in TOOLS)
        if size > max_prompt_chars:
            return {"messages": [AIMessage(content="Stopped: experiment input is too long.")],
                    "status": "limited"}
        response = bound_model.invoke(messages)
        if not isinstance(response, AIMessage):
            raise TypeError("The model must return a LangChain AIMessage.")
        return {"messages": [response], "model_calls": state.get("model_calls", 0) + 1,
                "status": "running" if response.tool_calls else "complete"}

    def execute_tools(state: AgentState):
        calls = state["messages"][-1].tool_calls
        used = state.get("tool_calls", 0)
        if used + len(calls) > max_tool_calls:
            # Resolve every pending tool-call ID, even when the task is stopped.
            return {"messages": [ToolMessage(content="Tool budget exhausted.",
                                             tool_call_id=call["id"], status="error")
                                  for call in calls] + [AIMessage(content="Stopped: tool-call limit reached.")],
                    "status": "limited"}

        questions = [call for call in calls if call["name"] == "ask_user"]
        answer = None
        question_error = None
        if questions:
            try:
                if len(questions) != 1:
                    raise ValueError("Ask only one clarification question per turn.")
                question = QuestionInput.model_validate(questions[0]["args"]).question.strip()
                if not question:
                    raise ValueError("The question must not be blank.")
            except (ValidationError, ValueError) as exc:
                question_error = str(exc)
            else:
                # This happens before any sibling tool executes. On resume, this
                # node restarts and interrupt returns the saved user's answer.
                answer = interrupt({"kind": "clarification", "question": question})
                if not isinstance(answer, str) or not answer.strip() or len(answer) > 2000:
                    raise ValueError("Resume with a non-empty answer of at most 2000 characters.")

        results = []
        for call in calls:
            try:
                if call["name"] == "ask_user":
                    if question_error:
                        raise ValueError(question_error)
                    result = json.dumps({"user_answer": answer.strip()})
                else:
                    selected = TOOLS_BY_NAME.get(call["name"])
                    if selected is None:
                        raise ValueError(f"Unknown tool: {call['name']}")
                    result = selected.invoke(call["args"])
                results.append(ToolMessage(content=result, tool_call_id=call["id"],
                                           name=call["name"]))
            except (ValidationError, ValueError, TypeError) as exc:
                results.append(ToolMessage(content=f"Tool rejected: {exc}",
                                           tool_call_id=call["id"], name=call["name"], status="error"))
        return {"messages": results, "tool_calls": used + len(calls)}

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", execute_tools)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", lambda state: "tools" if state["status"] == "running" else END)
    graph.add_conditional_edges("tools", lambda state: END if state["status"] == "limited" else "agent")
    return graph.compile(checkpointer=checkpointer)
