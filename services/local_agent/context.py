"""Bounded recent context and a persistent extractive digest, without extra inference."""
from __future__ import annotations

import hashlib
from services.local_agent.state import LocalState
from services.memory.memory_service import memory


def context_for(state: LocalState, session_id: str, user_id: str) -> str:
    summary_id = hashlib.sha256(f"{user_id}:{session_id}".encode()).hexdigest()
    summary = state.get("context", summary_id, user_id) or {"id": summary_id, "lines": [], "seen": []}
    history = memory.to_llm_format(session_id, user_id=user_id)
    # Retain a bounded extractive digest of older messages. This is explicitly
    # lossy context, never a source of new document facts or tool instructions.
    for message in history[:-4]:
        fingerprint = hashlib.sha256(f"{message['role']}:{message['content']}".encode()).hexdigest()
        if fingerprint not in summary["seen"]:
            summary["lines"].append(f"{message['role']}: {message['content'][:220]}")
            summary["seen"].append(fingerprint)
    summary["lines"] = summary["lines"][-8:]
    summary["seen"] = summary["seen"][-80:]
    state.put("context", summary, user_id)
    recent = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in history[-4:])
    facts = memory.facts_text(user_id=user_id)[:800]
    # Cap each part so older excerpts cannot crowd out the newest turn/facts.
    older = "\n".join(summary["lines"])[-1000:]
    return f"Older conversation excerpts (may omit details):\n{older}\nRecent conversation:\n{recent[-1600:]}\nExplicit saved facts:\n{facts}"
