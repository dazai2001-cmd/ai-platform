from __future__ import annotations

import time
from unittest.mock import Mock

import pytest

from test_api_integration import client, _bearer, _create_verified_session  # noqa: F401
from test_local_workflow import ScriptedModel, call
from langchain_core.messages import AIMessage


@pytest.fixture
def local_client(client, tmp_path, monkeypatch):
    pytest.importorskip("langgraph.checkpoint.sqlite")
    from core.config.settings import settings
    monkeypatch.setattr(settings, "LOCAL_AGENT_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_AGENT_PATH", str(tmp_path / "local"))
    monkeypatch.setattr(settings, "LOCAL_MEDIA_PATH", str(tmp_path / "media"))
    monkeypatch.setattr(settings, "LOCAL_AGENT_CONTEXT_TOKENS", 8192)
    yield client
    from services.local_agent.agent_service import agent_service
    from services.local_agent.media_service import media_service
    agent_service().pool.shutdown(wait=True)
    media_service().pool.shutdown(wait=True)


def wait(client, run_id, headers):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(f"/api/local/runs/{run_id}", headers=headers)
        run = response.get_json()
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.05)
    raise AssertionError("Test local task did not finish")


@pytest.mark.parametrize("cancel", [False, True])
def test_workspace_waits_for_video_without_reloading_qwen(local_client, monkeypatch, cancel):
    from services.local_agent import agent_service as module
    owner = _create_verified_session(local_client, f"gpu-wait-{str(cancel).lower()}@example.com")
    model = ScriptedModel([call("calculator", {"expression": "1 + 1"}), AIMessage(content="2")])
    create = Mock(return_value=model)
    monkeypatch.setattr(module, "create_model", create)
    service = module.agent_service()
    service.state.put("media", {"id": "other-video", "kind": "video", "status": "running", "worker_active": True,
                                "updated_at": time.time()}, "other-owner")
    headers = _bearer(owner["token"])
    try:
        response = local_client.post("/api/local/runs", headers=headers, json={"query": "Calculate 1 + 1", "session_id": "gpu-wait"})
        run_id = response.get_json()["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            run = service.get(run_id, owner["user"]["id"])
            if run.get("message"):
                break
            time.sleep(0.02)
        assert run["status"] == "queued" and "video preview" in run["message"]
        create.assert_not_called()
        if cancel:
            local_client.post(f"/api/local/runs/{run_id}/cancel", headers=headers)
        service.state.update("media", "other-video", "other-owner", status="succeeded", worker_active=False)
        finished = wait(local_client, run_id, headers)
        assert finished["status"] == ("cancelled" if cancel else "complete")
        if cancel:
            create.assert_not_called()
        else:
            create.assert_called_once()
    finally:
        service.state.update("media", "other-video", "other-owner", status="cancelled", worker_active=False)


def test_native_workspace_tool_uses_authenticated_owner(local_client, monkeypatch):
    from services.local_agent import agent_service as module
    from domain.router import workspace_router as workspace_module
    owner = _create_verified_session(local_client, "local-owner@example.com")
    other = _create_verified_session(local_client, "local-other@example.com")
    model = ScriptedModel([call("workspace_tool", {"action": "docs_list", "query": "List documents"}), AIMessage(content="One document.")])
    monkeypatch.setattr(module, "create_model", lambda: model)
    docs = Mock(return_value=[{"source": "owner.md", "chunks": 1}])
    monkeypatch.setattr(workspace_module.rag_agent, "documents", docs)
    started = local_client.post("/api/local/runs", headers=_bearer(owner["token"]), json={"query": "List documents", "session_id": "shared-id"})
    assert started.status_code == 202
    run_id = started.get_json()["id"]
    assert local_client.get(f"/api/local/runs/{run_id}", headers=_bearer(other["token"])).status_code == 404
    assert local_client.post(f"/api/local/runs/{run_id}/cancel", headers=_bearer(other["token"])).status_code == 404
    assert local_client.post(f"/api/local/runs/{run_id}/resume", headers=_bearer(other["token"]), json={"answer": "take over"}).status_code == 404
    run = wait(local_client, run_id, _bearer(owner["token"]))
    assert run["status"] == "complete"
    docs.assert_called_once_with(user_id=owner["user"]["id"])
    assert local_client.get("/api/local/runs", headers=_bearer(other["token"])).get_json() == []


def test_workspace_chat_resumes_only_its_owners_pending_task(local_client, monkeypatch):
    from services.local_agent import agent_service as module
    owner = _create_verified_session(local_client, "local-pause@example.com")
    model = ScriptedModel([AIMessage(content="Watercolor, square.")])
    monkeypatch.setattr(module, "create_model", lambda: model)
    first = local_client.post("/api/chat/workspace", headers=_bearer(owner["token"]), json={"query": "Make an image", "session_id": "paused-chat"})
    run_id = first.get_json()["local_run"]
    paused = wait(local_client, run_id, _bearer(owner["token"]))
    assert paused["status"] == "awaiting_input"
    assert [field["id"] for field in paused["question"]["fields"]] == ["style", "shape"]
    second = local_client.post("/api/chat/workspace", headers=_bearer(owner["token"]), json={"query": "Watercolor, square", "session_id": "paused-chat"})
    assert second.get_json()["local_run"] == run_id
    assert wait(local_client, run_id, _bearer(owner["token"]))["status"] == "complete"
    again = local_client.post(f"/api/local/runs/{run_id}/resume", headers=_bearer(owner["token"]), json={"answer": "again"})
    assert again.status_code == 400


def test_specialized_rag_question_resumes_through_popup_endpoint(local_client, monkeypatch):
    from services.local_agent import agent_service as module
    from domain.router import workspace_router as workspace_module
    owner = _create_verified_session(local_client, "rag-popup@example.com")
    model = ScriptedModel([call("workspace_tool", {"action": "rag_ask", "query": "When does it launch?"}, "first"),
                           call("workspace_tool", {"action": "rag_ask", "query": "When does Cedar launch?"}, "resolved")])
    monkeypatch.setattr(module, "create_model", lambda: model)
    execute = Mock(side_effect=[{"answer": "Which project do you mean?", "needs_clarification": True, "route": "rag"},
                                {"answer": "22 October [cedar.md].", "route": "rag", "sources": [{"source": "cedar.md"}]}])
    monkeypatch.setattr(workspace_module.WorkspaceRouter, "execute", execute)
    headers = _bearer(owner["token"])
    started = local_client.post("/api/chat/workspace", headers=headers, json={"query": "When does it launch?", "session_id": "rag-chat"})
    run_id = started.get_json()["local_run"]
    paused = wait(local_client, run_id, headers)
    assert paused["status"] == "awaiting_input" and paused["question"]["question"] == "Which project do you mean?"
    assert execute.call_count == 1
    resumed = local_client.post(f"/api/local/runs/{run_id}/resume", headers=headers, json={"answer": "Project Cedar"})
    assert resumed.status_code == 202 and resumed.get_json()["id"] == run_id
    result = wait(local_client, run_id, headers)
    assert result["status"] == "complete" and result["result"]["sources"] == [{"source": "cedar.md"}]
    assert execute.call_count == 2


def test_generated_artifact_is_authenticated_and_user_scoped(local_client):
    from services.local_agent.media_service import media_service
    owner = _create_verified_session(local_client, "image-owner@example.com")
    other = _create_verified_session(local_client, "image-other@example.com")
    service = media_service()
    job = {"id": "a" * 32, "kind": "image", "status": "succeeded", "updated_at": time.time()}
    service.state.put("media", job, owner["user"]["id"])
    service.artifact_path(job).write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    own = local_client.get(f"/api/local/media/{job['id']}/artifact", headers=_bearer(owner["token"]))
    assert own.status_code == 200 and own.mimetype == "image/png"
    assert local_client.get(f"/api/local/media/{job['id']}/artifact", headers=_bearer(other["token"])).status_code == 404
    assert local_client.get("/api/local/media", headers=_bearer(other["token"])).get_json() == []
    assert local_client.delete(f"/api/local/media/{job['id']}", headers=_bearer(other["token"])).status_code == 404


def test_clearing_memory_also_clears_only_the_owners_local_digest(local_client):
    import hashlib
    from services.local_agent.agent_service import agent_service
    owner = _create_verified_session(local_client, "digest-owner@example.com")
    other = _create_verified_session(local_client, "digest-other@example.com")
    state = agent_service().state
    for user in (owner, other):
        user_id = user["user"]["id"]
        item_id = hashlib.sha256(f"{user_id}:shared".encode()).hexdigest()
        state.put("context", {"id": item_id, "lines": ["Saved older context"], "seen": []}, user_id)
    response = local_client.delete("/api/memory/shared", headers=_bearer(owner["token"]))
    assert response.status_code == 200
    assert state.list("context", owner["user"]["id"]) == []
    assert state.list("context", other["user"]["id"])


def test_local_media_card_survives_conversation_reload_with_current_job_status(local_client):
    from services.local_agent.agent_service import agent_service
    owner = _create_verified_session(local_client, "chat-media@example.com")
    user_id = owner["user"]["id"]
    state = agent_service().state
    job = {"id": "media-chat", "kind": "image", "status": "queued", "progress": 0,
           "spec": {"prompt": "A cat", "style": "watercolor", "width": 512, "height": 512, "model_id": "sd-turbo"}}
    state.put("media", job, user_id)
    state.put("run", {"id": "media-run", "session_id": "media-conversation", "status": "complete", "result": {"media_job": job}}, user_id)
    headers = _bearer(owner["token"])
    saved = local_client.put("/api/chat/conversations/media-conversation", headers=headers, json={"messages": [
        {"role": "user", "content": "Make a cat image"},
        {"role": "assistant", "content": "Your image is queued.", "run_id": "media-run", "media_job": job}]})
    assert saved.status_code == 200 and saved.get_json()["messages"][1]["media_job"]["id"] == job["id"]
    state.update("media", job["id"], user_id, status="succeeded", progress=100, artifact_url="/api/local/media/media-chat/artifact")
    restored = local_client.get("/api/chat/conversations/media-conversation", headers=headers).get_json()["messages"][1]
    assert restored["run_id"] == "media-run" and restored["media_job"]["status"] == "succeeded"
    state.delete("media", job["id"], user_id)
    assert "media_job" not in local_client.get("/api/chat/conversations/media-conversation", headers=headers).get_json()["messages"][1]


def test_chat_cannot_attach_another_owners_or_sessions_local_result(local_client):
    from services.local_agent.agent_service import agent_service
    owner = _create_verified_session(local_client, "chat-owner@example.com")
    other = _create_verified_session(local_client, "chat-other@example.com")
    state = agent_service().state
    job = {"id": "private-media", "kind": "image", "status": "succeeded"}
    state.put("media", job, other["user"]["id"])
    state.put("run", {"id": "private-run", "session_id": "own-chat", "status": "complete", "result": {"media_job": job}}, other["user"]["id"])
    state.put("run", {"id": "different-session", "session_id": "other-chat", "status": "complete", "result": {"media_job": job}}, owner["user"]["id"])
    headers = _bearer(owner["token"])
    for run_id in ("private-run", "different-session"):
        saved = local_client.put("/api/chat/conversations/own-chat", headers=headers, json={"messages": [
            {"role": "assistant", "content": "An image", "run_id": run_id, "media_job": job}]})
        assert saved.status_code == 200 and "media_job" not in saved.get_json()["messages"][0]


def test_saved_question_uses_live_checkpoint_and_clears_when_task_finishes(local_client):
    from services.local_agent.agent_service import agent_service
    owner = _create_verified_session(local_client, "chat-question@example.com")
    user_id = owner["user"]["id"]
    state = agent_service().state
    question = {"kind": "clarification", "question": "Which style?", "options": ["Watercolor", "Cinematic"]}
    state.put("run", {"id": "question-run", "session_id": "question-chat", "status": "awaiting_input", "question": question}, user_id)
    headers = _bearer(owner["token"])
    response = local_client.put("/api/chat/conversations/question-chat", headers=headers, json={"messages": [
        {"role": "assistant", "content": "Which style?", "run_id": "question-run", "needs_clarification": True,
         "clarification": {"question": "Client supplied text"}}]})
    assert response.get_json()["messages"][0]["clarification"] == question
    state.update("run", "question-run", user_id, status="complete", question=None)
    restored = local_client.get("/api/chat/conversations/question-chat", headers=headers).get_json()["messages"][0]
    assert not restored.get("needs_clarification")
    assert "clarification" not in restored


@pytest.mark.parametrize("cloud,production", [(True, False), (False, True)])
def test_cloud_and_production_block_all_local_mutations_and_reads(local_client, monkeypatch, cloud, production):
    from core.config.settings import settings
    from apps.api.routes import local as routes
    owner = _create_verified_session(local_client, f"cloud-{str(cloud).lower()}@example.com")
    monkeypatch.setattr(settings, "IS_CLOUD_RUNTIME", cloud)
    monkeypatch.setattr(settings, "IS_PRODUCTION", production)
    access = Mock()
    monkeypatch.setattr(routes, "agents", access)
    monkeypatch.setattr(routes, "media", access)
    headers = _bearer(owner["token"])
    assert local_client.get("/api/local/capabilities", headers=headers).get_json() == {"enabled": False}
    for method, path in [("get", "/api/local/runs"), ("post", "/api/local/runs"), ("get", "/api/local/media"),
                         ("post", "/api/local/runs/id/resume"), ("post", "/api/local/media/id/cancel"),
                         ("get", "/api/local/media/id/artifact"), ("delete", "/api/local/media/id")]:
        assert getattr(local_client, method)(path, headers=headers).status_code == 404
    access.assert_not_called()
