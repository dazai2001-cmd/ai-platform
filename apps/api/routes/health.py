import time

from flask import Blueprint, jsonify, request
from infrastructure.llm.ollama_client import ollama
from services.analytics.analytics_service import analytics
from core.config.settings import settings
from apps.api.errors import error_response
from services.settings.model_settings_service import model_settings

health_bp = Blueprint("health", __name__, url_prefix="/api")


@health_bp.get("/health")
def health():
    ollama_ok = ollama.health()
    cloud_ok = ollama.cloud_configured()
    models = ollama.list_models()
    return jsonify({
        "status": "ok" if ollama_ok or cloud_ok else "degraded",
        "ollama": ollama_ok,
        "cloud_models": cloud_ok,
        "models": models,
        "task_models": settings.TASK_MODELS,
        "router_model": settings.ROUTER_MODEL,
    })


@health_bp.post("/health/warmup")
def warmup():
    data = request.json or {}
    model = (data.get("model") or "").strip() or settings.TASK_MODELS["general"]
    t0 = time.monotonic()
    try:
        response = ollama.generate(model, "/no_think\nReply with ok.", temperature=0, max_tokens=4)
        return jsonify({
            "ok": True,
            "model": model,
            "response": response.strip(),
            "latency_ms": round((time.monotonic() - t0) * 1000),
        })
    except Exception as e:
        return error_response(e, 502)


@health_bp.get("/settings/models")
def get_model_settings():
    return jsonify({
        "task_models": model_settings.get(),
        "available_models": ollama.list_models(),
    })


@health_bp.put("/settings/models")
def update_model_settings():
    data = request.json or {}
    return jsonify({
        "task_models": model_settings.update(data.get("task_models", {})),
        "available_models": ollama.list_models(),
    })


@health_bp.delete("/settings/models")
def reset_model_settings():
    return jsonify({
        "task_models": model_settings.reset(),
        "available_models": ollama.list_models(),
    })


@health_bp.get("/analytics/summary")
def summary():
    since = request.args.get("since_hours", 24, type=int)
    return jsonify(analytics.summary(since_hours=since))


@health_bp.get("/analytics/recent")
def recent():
    n = request.args.get("n", 20, type=int)
    return jsonify(analytics.recent(n=n))
