from flask import Blueprint, jsonify, request
from infrastructure.llm.ollama_client import ollama
from services.analytics.analytics_service import analytics
from core.config.settings import settings

health_bp = Blueprint("health", __name__, url_prefix="/api")


@health_bp.get("/health")
def health():
    ollama_ok = ollama.health()
    models = ollama.list_models() if ollama_ok else []
    return jsonify({
        "status": "ok" if ollama_ok else "degraded",
        "ollama": ollama_ok,
        "models": models,
        "task_models": settings.TASK_MODELS,
        "router_model": settings.ROUTER_MODEL,
    })


@health_bp.get("/analytics/summary")
def summary():
    since = request.args.get("since_hours", 24, type=int)
    return jsonify(analytics.summary(since_hours=since))


@health_bp.get("/analytics/recent")
def recent():
    n = request.args.get("n", 20, type=int)
    return jsonify(analytics.recent(n=n))
