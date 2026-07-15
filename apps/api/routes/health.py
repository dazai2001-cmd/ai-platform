import time

from flask import Blueprint, jsonify, request
from redis import Redis
from infrastructure.llm.ollama_client import ollama
from services.analytics.analytics_service import analytics
from core.config.settings import settings
from core.config.validation import active_model_configuration_valid
from apps.api.errors import error_response
from services.settings.model_settings_service import model_settings
from services.storage.sqlite_service import db
from apps.api.auth_context import current_user_id

health_bp = Blueprint("health", __name__, url_prefix="/api")


def _database_ready() -> bool:
    try:
        row = db.query_one("SELECT 1 AS ready")
        return bool(row and row.get("ready") == 1)
    except Exception:
        return False


def _rate_limit_store_ready() -> bool:
    if not settings.RATE_LIMIT_ENABLED:
        return not settings.IS_PRODUCTION
    uri = settings.RATE_LIMIT_STORAGE_URI
    if uri == "memory://":
        return not settings.IS_PRODUCTION
    client = None
    try:
        client = Redis.from_url(
            uri,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        return bool(client.ping())
    except Exception:
        return False
    finally:
        if client is not None:
            client.close()


def _model_provider_ready() -> bool:
    if not active_model_configuration_valid(settings):
        return False
    if settings.IS_CLOUD_RUNTIME:
        return bool(
            (settings.GEMINI_API_KEY and settings.GEMINI_MODELS)
            or (settings.OPENROUTER_API_KEY and settings.OPENROUTER_MODELS)
        )
    return ollama.health()


def _available_models() -> list[str]:
    if not ollama.health():
        return []
    models = ollama.list_models()
    if settings.IS_PRODUCTION and not settings.IS_CLOUD_RUNTIME:
        allowed = set(settings.LOCAL_ALLOWED_MODELS)
        return sorted(model for model in models if model in allowed)
    return models


def _readiness_payload() -> tuple[dict, int]:
    checks = {
        "database": _database_ready(),
        "rate_limit_store": _rate_limit_store_ready(),
        "model_provider": _model_provider_ready(),
    }
    ready = all(checks.values())
    return ({
        "status": "ok" if ready else "degraded",
        "runtime": settings.AI_RUNTIME,
        "version": settings.APP_VERSION,
        "commit": settings.GIT_SHA,
        "checks": checks,
    }, 200 if ready else 503)


@health_bp.get("/health/live")
def liveness():
    return jsonify({
        "status": "ok",
        "version": settings.APP_VERSION,
        "commit": settings.GIT_SHA,
    })


@health_bp.get("/health/ready")
def readiness():
    payload, status = _readiness_payload()
    return jsonify(payload), status


@health_bp.get("/health")
def health():
    payload, status = _readiness_payload()
    return jsonify(payload), status


@health_bp.post("/health/warmup")
def warmup():
    data = request.json or {}
    user_id = current_user_id()
    t0 = time.monotonic()
    try:
        model = model_settings.resolve_model("general", data.get("model"), user_id=user_id)
        response = ollama.generate(model, "/no_think\nReply with ok.", temperature=0, max_tokens=4)
        return jsonify({
            "ok": True,
            "model": model,
            "response": response.strip(),
            "latency_ms": round((time.monotonic() - t0) * 1000),
        })
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 502)


@health_bp.get("/settings/models")
def get_model_settings():
    return jsonify({
        "task_models": model_settings.get(user_id=current_user_id()),
        "available_models": _available_models(),
    })


@health_bp.put("/settings/models")
def update_model_settings():
    data = request.json or {}
    try:
        return jsonify({
            "task_models": model_settings.update(data.get("task_models", {}), user_id=current_user_id()),
            "available_models": _available_models(),
        })
    except ValueError as e:
        return error_response(e, 400)


@health_bp.delete("/settings/models")
def reset_model_settings():
    return jsonify({
        "task_models": model_settings.reset(user_id=current_user_id()),
        "available_models": _available_models(),
    })


@health_bp.get("/analytics/summary")
def summary():
    since = max(1, min(request.args.get("since_hours", 24, type=int) or 24, 24 * 365))
    return jsonify(analytics.summary(user_id=current_user_id(), since_hours=since))


@health_bp.get("/analytics/recent")
def recent():
    n = max(1, min(request.args.get("n", 20, type=int) or 20, 100))
    return jsonify(analytics.recent(user_id=current_user_id(), n=n))
