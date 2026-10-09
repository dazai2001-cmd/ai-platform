"""Run with: python -m experiments.agent_workflow.run --help."""

import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .workflow import build_graph


DEFAULT_DB = Path(__file__).resolve().parents[2] / "data/cache/agent_workflow/checkpoints.sqlite"


def main():
    parser = argparse.ArgumentParser(description="Local Qwen tools + durable LangGraph clarification.")
    parser.add_argument("--prompt", help="Start a new task.")
    parser.add_argument("--resume", help="Answer a paused task using the same --thread.")
    parser.add_argument("--thread", help="Task ID; generated automatically for a new task.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--context", type=int, default=4096)
    parser.add_argument("--output", type=Path, help="Also save the JSON result to this file.")
    args = parser.parse_args()
    if bool(args.prompt) == bool(args.resume):
        parser.error("Provide exactly one of --prompt or --resume.")
    if args.resume and (not args.thread or not args.resume.strip() or len(args.resume) > 2000):
        parser.error("Resuming requires --thread and a non-empty answer of at most 2000 characters.")
    if args.context < 1024:
        parser.error("Use a context window of at least 1024 tokens.")

    # Keep this local experiment independent of any app tracing configuration.
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    model = init_chat_model(args.model, model_provider="ollama", base_url=args.base_url,
                            temperature=0, reasoning=False, num_ctx=args.context,
                            num_predict=512, keep_alive="5m",
                            client_kwargs={"timeout": 180.0})
    args.db.parent.mkdir(parents=True, exist_ok=True)
    thread = args.thread or str(uuid4())
    config = {"configurable": {"thread_id": thread}, "recursion_limit": 20}
    with SqliteSaver.from_conn_string(str(args.db)) as saver:
        graph = build_graph(model, saver)
        snapshot = graph.get_state(config)
        if args.prompt and snapshot.values:
            parser.error("That thread already exists. Use a new --thread or resume the pending task.")
        if args.resume and not any(task.interrupts for task in snapshot.tasks):
            parser.error("That thread has no pending clarification in this checkpoint database.")
        graph_input = Command(resume=args.resume) if args.resume else {
            "messages": [HumanMessage(content=args.prompt)], "model_calls": 0,
            "tool_calls": 0, "status": "running",
        }
        result = graph.invoke(graph_input, config)
        payload = {
            "thread": thread, "model": args.model, "context": args.context,
            "status": "paused" if result.get("__interrupt__") else result["status"],
            "model_calls": result["model_calls"], "tool_calls": result["tool_calls"],
            "interrupts": [item.value for item in result.get("__interrupt__", [])],
            "messages": [message.model_dump(mode="json") for message in result["messages"]],
        }
    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
