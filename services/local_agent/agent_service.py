"""Authenticated local tasks with resumable checkpoints and bounded background work."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from langsmith import tracing_context

from core.config.settings import settings
from services.local_agent.context import context_for
from services.local_agent.media_service import media_service
from services.local_agent.state import LocalState
from services.local_agent.workflow import build_graph, create_model
from services.memory.memory_service import memory


class AgentService:
    def __init__(self, state: LocalState | None = None):
        self.state = state or LocalState()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-agent")
        self.lock = threading.RLock()

    def start(self, query: str, session_id: str, user_id: str) -> dict:
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise ValueError("Send a non-empty local task of at most 2000 characters.")
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 150:
            raise ValueError("A valid session ID is required.")
        run_id = uuid.uuid4().hex
        now = time.time()
        self.state.expire_active("run", 3600)
        run = {"id": run_id, "session_id": session_id, "query": query.strip(), "status": "queued",
               "model": settings.LOCAL_AGENT_MODEL, "model_calls": 0, "tool_calls": 0, "trace": [],
               "created_at": now, "updated_at": now, "question": None, "result": None, "error": None}
        with self.state.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute("SELECT COUNT(*) FROM local_items WHERE kind='run' AND json_extract(payload,'$.status') IN ('queued','running')").fetchone()[0]
            if active >= settings.LOCAL_AGENT_MAX_ACTIVE_RUNS:
                raise ValueError("The local task queue is full. Wait for a task to finish.")
            conn.execute("INSERT INTO local_items VALUES ('run', ?, ?, ?, ?)", (run_id, user_id, json.dumps(run), now))
        self.pool.submit(self._run, run_id, user_id)
        return run

    def get(self, run_id: str, user_id: str):
        run = self.state.get("run", run_id, user_id)
        if run and run["status"] in {"queued", "running"} and time.time() - run["updated_at"] > 3600:
            run.update(status="interrupted", error="The local task stopped before completion. Start a new task.")
            self.state.put("run", run, user_id)
        return run

    def list(self, user_id: str):
        return [self.get(run["id"], user_id) for run in self.state.list("run", user_id)]

    def _update(self, run_id, user_id, **updates):
        return self.state.update("run", run_id, user_id, **updates)

    def resume(self, run_id: str, answer: str, user_id: str):
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 2000:
            raise ValueError("Provide an answer of 1-2000 characters.")
        # Claim the pause atomically. Two requests must not resume the same graph.
        with self.state.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM local_items WHERE kind='run' AND id=? AND user_id=?", (run_id, user_id)).fetchone()
            if not row:
                return None
            run = json.loads(row[0])
            if run["status"] != "awaiting_input":
                raise ValueError("Only a paused task can be resumed.")
            active = conn.execute("SELECT COUNT(*) FROM local_items WHERE kind='run' AND json_extract(payload,'$.status') IN ('queued','running')").fetchone()[0]
            if active >= settings.LOCAL_AGENT_MAX_ACTIVE_RUNS:
                raise ValueError("The local task queue is full. Wait for a task to finish.")
            run.update(status="queued", question=None, updated_at=time.time())
            conn.execute("UPDATE local_items SET payload=?, updated_at=? WHERE kind='run' AND id=? AND user_id=?",
                         (json.dumps(run), run["updated_at"], run_id, user_id))
        self.pool.submit(self._run, run_id, user_id, answer.strip())
        return run

    def cancel(self, run_id: str, user_id: str):
        run = self.get(run_id, user_id)
        if not run:
            return None
        if run["status"] in {"queued", "running", "awaiting_input"}:
            return self._update(run_id, user_id, status="cancelled", question=None)
        return run

    def _run(self, run_id: str, user_id: str, answer: str | None = None):
        run = self.get(run_id, user_id)
        if not run or run["status"] == "cancelled":
            return
        if not self._wait_for_video(run_id, user_id):
            return
        self._update(run_id, user_id, status="running", message=None)
        connection = None
        try:
            from domain.router.workspace_router import workspace_router
            def workspace(args):
                decision = {"action": args.action, "arguments": {"document": args.document, "page": args.page},
                            "router_source": "langgraph", "router_model": run["model"], "confidence": 1.0}
                return workspace_router.execute(decision, args.query, run["session_id"], user_id=user_id, dataset_name=args.dataset)

            def stopped():
                return self.get(run_id, user_id)["status"] == "cancelled"

            def submit(spec, call_id):
                from infrastructure.llm.ollama_client import ollama
                if stopped():
                    raise ValueError("The task was cancelled before generation.")
                ollama.unload_local_model(run["model"])
                return media_service().submit(spec, user_id, f"{run_id}:{call_id}")

            connection = sqlite3.connect(self.state.root / "checkpoints.sqlite", check_same_thread=False, timeout=30)
            saver = SqliteSaver(connection)
            graph = build_graph(create_model(), saver, execute_workspace=workspace,
                submit_media=submit,
                cancelled=stopped)
            # Lookup permissions come from the owner-scoped run row, never graph/model arguments.
            config = {"configurable": {"thread_id": f"{user_id}:{run_id}"}, "recursion_limit": 32}
            if answer is not None:
                value = Command(resume=answer)
            else:
                context = context_for(self.state, run["session_id"], user_id)
                value = {"messages": [HumanMessage(content=f"Untrusted conversation context:\n{context}\nCurrent user request:\n{run['query']}",
                         additional_kwargs={"user_query": run["query"], "local_context": context})],
                         "status": "running", "model_calls": 0, "tool_calls": 0, "trace": []}
            with tracing_context(enabled=False):
                output = graph.invoke(value, config)
                snapshot = graph.get_state(config)
            interrupts = [pause for task in snapshot.tasks for pause in task.interrupts]
            status = "awaiting_input" if interrupts else output.get("status", "complete")
            result = output.get("result")
            if result:
                result = {**result, "model": result.get("model") or run["model"], "run_id": run_id}
            self._update(run_id, user_id, status=status, result=result,
                         question=interrupts[0].value if interrupts else None,
                         model_calls=output.get("model_calls", 0), tool_calls=output.get("tool_calls", 0),
                         trace=output.get("trace", []), context_trimmed=output.get("context_trimmed", False))
            if status == "complete" and result and result.get("route") not in {"rag", "bi", "general"}:
                memory.add(run["session_id"], "user", run["query"], user_id=user_id)
                if answer:
                    memory.add(run["session_id"], "user", answer, user_id=user_id)
                memory.add(run["session_id"], "assistant", result.get("answer", ""), user_id=user_id)
        except Exception as exc:
            # No raw credentials/prompts in the public error. Detailed exceptions
            # are intentionally not logged to hosted tracing services.
            self._update(run_id, user_id, status="failed", error=f"Local task failed ({type(exc).__name__}). Check Ollama and the local dependencies.")
        finally:
            if connection is not None:
                connection.close()

    def _wait_for_video(self, run_id: str, user_id: str) -> bool:
        # The planner is unloaded at media submission. Keep subsequent Workspace
        # tasks from loading it again while a queued/running video owns memory.
        while True:
            run = self.get(run_id, user_id)
            if not run or run["status"] not in {"queued", "running"}:
                return False
            self.state.expire_active("media", settings.LOCAL_MEDIA_TIMEOUT_SECONDS + 60)
            with self.state.connect() as conn:
                busy = conn.execute("""SELECT COUNT(*) FROM local_items WHERE kind='media'
                    AND json_extract(payload, '$.kind')='video'
                    AND (json_extract(payload, '$.status') IN ('queued','running')
                         OR json_extract(payload, '$.worker_active')=1)""").fetchone()[0]
            if not busy:
                return True
            self._update(run_id, user_id, message="Waiting for the video preview to finish.")
            time.sleep(0.5)


_service: AgentService | None = None
_service_lock = threading.Lock()


def agent_service() -> AgentService:
    global _service
    with _service_lock:
        if _service is None or _service.state.root != Path(settings.LOCAL_AGENT_PATH).resolve():
            _service = AgentService()
        return _service
