from __future__ import annotations

from unittest.mock import Mock

from services.local_agent import context as module
from services.local_agent.state import LocalState


def test_long_history_digest_is_bounded_and_keeps_recent_details(tmp_path, monkeypatch):
    state = LocalState(tmp_path)
    history = [{"role": "user", "content": f"Older question {i}. " + "x" * 5000} for i in range(20)]
    history[-1] = {"role": "user", "content": "The current output should be a watercolor cat."}
    monkeypatch.setattr(module.memory, "to_llm_format", Mock(return_value=history))
    monkeypatch.setattr(module.memory, "facts_text", Mock(return_value="Explicit preference: use square images."))
    context = module.context_for(state, "session", "owner")
    assert len(context) < 3600
    assert "watercolor cat" in context and "square images" in context
    summary = state.list("context", "owner")[0]
    assert summary["lines"] and state.list("context", "other") == []
    # Reopening keeps older excerpts even if memory now contains only recent turns.
    monkeypatch.setattr(module.memory, "to_llm_format", Mock(return_value=history[-4:]))
    reopened_context = module.context_for(LocalState(tmp_path), "session", "owner")
    assert "Older question" in reopened_context


def test_context_queries_memory_with_the_authenticated_owner(tmp_path, monkeypatch):
    history = Mock(return_value=[])
    facts = Mock(return_value="")
    monkeypatch.setattr(module.memory, "to_llm_format", history)
    monkeypatch.setattr(module.memory, "facts_text", facts)
    module.context_for(LocalState(tmp_path), "same-session", "owner")
    history.assert_called_once_with("same-session", user_id="owner")
    facts.assert_called_once_with(user_id="owner")
