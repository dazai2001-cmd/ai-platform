"""Local-only API. The runtime guard applies even if a flag is enabled in cloud."""
from __future__ import annotations

import uuid
from flask import Blueprint, jsonify, request, send_file

from apps.api.auth_context import current_user_id
from core.config.settings import settings
from services.local_agent.state import enabled

local_bp = Blueprint("local", __name__, url_prefix="/api/local")


@local_bp.before_request
def require_local_runtime():
    if request.path.endswith("/capabilities"):
        return None
    if not enabled():
        return jsonify({"error": "Local workflows are unavailable in this runtime."}), 404


@local_bp.get("/capabilities")
def capabilities():
    if not enabled():
        return jsonify({"enabled": False})
    from services.local_agent.media_models import catalogue, worker_info
    info = worker_info()
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: F401
        checkpoint_ready = True
    except ImportError:
        checkpoint_ready = False
    return jsonify({"enabled": True, "checkpoint_ready": checkpoint_ready, "orchestrator": settings.LOCAL_AGENT_MODEL,
        "context_tokens": settings.LOCAL_AGENT_CONTEXT_TOKENS, "max_model_calls": settings.LOCAL_AGENT_MAX_MODEL_CALLS,
        "max_tool_calls": settings.LOCAL_AGENT_MAX_TOOL_CALLS, "models": catalogue(),
        "media_device": settings.LOCAL_MEDIA_DEVICE, "worker": info, "video_worker": worker_info("video")})


def agents():
    from services.local_agent.agent_service import agent_service
    return agent_service()


def media():
    from services.local_agent.media_service import media_service
    return media_service()


@local_bp.post("/runs")
def start_run():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(agents().start(data.get("query"), data.get("session_id") or uuid.uuid4().hex, current_user_id())), 202
    except ImportError:
        return jsonify({"error": "Install requirements-local-agent.txt to enable local checkpoints."}), 503
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@local_bp.get("/runs")
def list_runs():
    return jsonify(agents().list(current_user_id()))


@local_bp.get("/runs/<run_id>")
def get_run(run_id):
    run = agents().get(run_id, current_user_id())
    return jsonify(run) if run else (jsonify({"error": "Task not found"}), 404)


@local_bp.post("/runs/<run_id>/resume")
def resume_run(run_id):
    try:
        run = agents().resume(run_id, (request.get_json(silent=True) or {}).get("answer"), current_user_id())
        return (jsonify(run), 202) if run else (jsonify({"error": "Task not found"}), 404)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@local_bp.post("/runs/<run_id>/cancel")
def cancel_run(run_id):
    run = agents().cancel(run_id, current_user_id())
    return jsonify(run) if run else (jsonify({"error": "Task not found"}), 404)


@local_bp.get("/media")
def list_media():
    return jsonify(media().list(current_user_id()))


@local_bp.get("/media/<job_id>")
def get_media(job_id):
    job = media().get(job_id, current_user_id())
    return jsonify(job) if job else (jsonify({"error": "Generation not found"}), 404)


@local_bp.post("/media/<job_id>/cancel")
def cancel_media(job_id):
    job = media().cancel(job_id, current_user_id())
    return jsonify(job) if job else (jsonify({"error": "Generation not found"}), 404)


@local_bp.delete("/media/<job_id>")
def delete_media(job_id):
    try:
        if not media().delete(job_id, current_user_id()):
            return jsonify({"error": "Generation not found"}), 404
        return jsonify({"deleted": True})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@local_bp.get("/media/<job_id>/artifact")
def get_artifact(job_id):
    job = media().get(job_id, current_user_id())
    if not job or job["status"] != "succeeded":
        return jsonify({"error": "Output not found"}), 404
    path = media().artifact_path(job)
    if not path.is_file():
        return jsonify({"error": "Output not found"}), 404
    return send_file(path, mimetype="image/png" if job["kind"] == "image" else "video/mp4", conditional=True)
