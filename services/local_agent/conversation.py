"""Keep local task references beside the existing, unchanged chat database schema."""
from __future__ import annotations

import hashlib

from services.local_agent.state import LocalState, enabled


def _key(conversation_id: str, user_id: str) -> str:
    return hashlib.sha256(f"{user_id}:{conversation_id}".encode()).hexdigest()


def _fingerprint(message: dict) -> str:
    return hashlib.sha256(f"{message.get('role')}:{message.get('content')}".encode()).hexdigest()


def save_references(conversation_id: str, messages: list[dict], user_id: str):
    if not enabled():
        return
    state = LocalState()
    references = []
    for index, message in enumerate(messages):
        if message.get("role") != "assistant" or not isinstance(message.get("run_id"), str):
            continue
        run = state.get("run", message["run_id"], user_id)
        if not run or run["session_id"] != conversation_id:
            continue
        job = (run.get("result") or {}).get("media_job")
        submitted_job = message.get("media_job")
        if job and isinstance(submitted_job, dict) and submitted_job.get("id") == job["id"]:
            kind = "media"
        elif message.get("needs_clarification") and run["status"] == "awaiting_input":
            kind = "question"
        else:
            continue
        references.append({"index": index, "fingerprint": _fingerprint(message), "run_id": run["id"], "kind": kind})
    item_id = _key(conversation_id, user_id)
    if references:
        state.put("conversation", {"id": item_id, "references": references}, user_id)
    else:
        state.delete("conversation", item_id, user_id)


def hydrate(conversation: dict, user_id: str) -> dict:
    if not enabled():
        return conversation
    state = LocalState()
    stored = state.get("conversation", _key(conversation["id"], user_id), user_id)
    for reference in (stored or {}).get("references", []):
        index = reference["index"]
        messages = conversation["messages"]
        if not 0 <= index < len(messages) or _fingerprint(messages[index]) != reference["fingerprint"]:
            continue
        run = state.get("run", reference["run_id"], user_id)
        if not run or run["session_id"] != conversation["id"]:
            continue
        if reference["kind"] == "media":
            job_id = ((run.get("result") or {}).get("media_job") or {}).get("id")
            job = state.get("media", job_id, user_id) if job_id else None
            if job:
                messages[index].update(run_id=run["id"], media_job=job)
        elif run["status"] == "awaiting_input" and run.get("question"):
            messages[index].update(run_id=run["id"], needs_clarification=True, clarification=run["question"])
    return conversation


def clear_references(conversation_id: str, user_id: str):
    if enabled():
        LocalState().delete("conversation", _key(conversation_id, user_id), user_id)
