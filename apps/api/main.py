import sys
import os
import json
import logging
import re
import time
import uuid
from io import BytesIO
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, g, jsonify, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from core.config.settings import settings
from core.config.validation import validate_settings
from apps.api.routes.rag import rag_bp
from apps.api.routes.bi import bi_bp
from apps.api.routes.memory import memory_bp
from apps.api.routes.health import health_bp
from apps.api.routes.chat import chat_bp
from apps.api.routes.career import career_bp
from apps.api.routes.jobs import jobs_bp
from apps.api.routes.auth import auth_bp
from services.auth.auth_service import auth_service
from services.security.rate_limiter import configure_rate_limits
from apps.api.request_limits import SizeLimitedRequest


PUBLIC_API_PATHS = {"/api/health", "/api/health/live", "/api/health/ready"}
PUBLIC_API_PREFIXES = ("/api/auth/",)
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
request_logger = logging.getLogger("ai_platform.requests")


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    request_logger.setLevel(logging.INFO)
    app.request_class = SizeLimitedRequest
    if settings.TRUST_PROXY_HOPS:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=settings.TRUST_PROXY_HOPS,
            x_proto=settings.TRUST_PROXY_HOPS,
            x_host=settings.TRUST_PROXY_HOPS,
        )
    app.config["SECRET_KEY"] = settings.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = settings.MAX_UPLOAD_BYTES
    app.config["RATELIMIT_ENABLED"] = settings.RATE_LIMIT_ENABLED
    app.config["RATELIMIT_HEADERS_ENABLED"] = True
    if test_config:
        app.config.update(test_config)

    if not app.config.get("SKIP_CONFIG_VALIDATION", False):
        validate_settings(settings)

    CORS(
        app,
        resources={r"/api/*": {"origins": settings.CORS_ORIGINS}},
        supports_credentials=True,
    )

    @app.before_request
    def attach_request_context():
        supplied_request_id = request.headers.get("X-Request-ID", "")
        g.request_id = (
            supplied_request_id
            if REQUEST_ID_RE.fullmatch(supplied_request_id)
            else uuid.uuid4().hex
        )
        g.request_started_at = time.monotonic()

    @app.after_request
    def apply_security_headers(response):
        response.headers.setdefault("X-Request-ID", getattr(g, "request_id", uuid.uuid4().hex))
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        )
        if request.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if settings.IS_PRODUCTION:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        request_logger.info(json.dumps({
            "event": "request_complete",
            "request_id": getattr(g, "request_id", None),
            "method": request.method,
            "path": request.path,
            "status": response.status_code,
            "duration_ms": round(
                (time.monotonic() - getattr(g, "request_started_at", time.monotonic())) * 1000,
                2,
            ),
            "user_id": (getattr(g, "current_user", None) or {}).get("id"),
        }, separators=(",", ":")))
        return response

    @app.before_request
    def enforce_request_limits():
        if not request.path.startswith("/api/"):
            return None
        if request.is_json:
            if request.content_length and request.content_length > settings.MAX_JSON_BYTES:
                return jsonify({"error": "JSON request body is too large"}), 413
            if request.content_length is None and request.environ.get("wsgi.input_terminated"):
                # Chunked requests have no Content-Length. Read at most one
                # byte beyond the cap, then restore the bounded body so Flask
                # can parse it normally in this request.
                body = request.environ["wsgi.input"].read(settings.MAX_JSON_BYTES + 1)
                if len(body) > settings.MAX_JSON_BYTES:
                    return jsonify({"error": "JSON request body is too large"}), 413
                request.environ["wsgi.input"] = BytesIO(body)
                request.environ["CONTENT_LENGTH"] = str(len(body))

        data = request.get_json(silent=True) if request.is_json else None
        if not isinstance(data, dict):
            return None

        prompt_fields = {
            "query",
            "question",
            "message",
            "prompt",
            "content",
            "cv_text",
            "job_description",
            "description",
        }
        for field in prompt_fields:
            value = data.get(field)
            if isinstance(value, str) and len(value) > settings.MAX_PROMPT_CHARS:
                return jsonify({"error": f"{field} exceeds the maximum length"}), 413

        text = data.get("text")
        if isinstance(text, str) and len(text) > settings.MAX_TEXT_INGEST_CHARS:
            return jsonify({"error": "text exceeds the ingestion limit"}), 413
        return None

    @app.before_request
    def require_api_token_and_user():
        if request.method == "OPTIONS":
            return None
        if request.path in PUBLIC_API_PATHS:
            return None
        if not settings.API_AUTH_TOKEN or not request.path.startswith("/api/"):
            supplied = None
        else:
            supplied = request.headers.get("X-API-Token", "")
            if supplied != settings.API_AUTH_TOKEN:
                return jsonify({"error": "unauthorized"}), 401

        if any(request.path.startswith(prefix) for prefix in PUBLIC_API_PREFIXES):
            return None
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        user = auth_service.authenticate_token(token)
        if user:
            g.current_user = user
            return None
        if not settings.AUTH_REQUIRED or not request.path.startswith("/api/"):
            return None
        return jsonify({"error": "login required"}), 401

    for bp in [auth_bp, rag_bp, bi_bp, memory_bp, health_bp, chat_bp, career_bp, jobs_bp]:
        app.register_blueprint(bp)

    configure_rate_limits(app)

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify({"error": "request body is too large"}), 413

    @app.errorhandler(429)
    def rate_limit_exceeded(_error):
        return jsonify({"error": "rate limit exceeded; retry later"}), 429

    @app.get("/")
    def root():
        return {
            "name": "AI Platform",
            "version": settings.APP_VERSION,
            "commit": settings.GIT_SHA,
            "environment": settings.APP_ENV,
            "agents": ["rag", "bi", "critic"],
        }

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=settings.PORT, debug=settings.DEBUG)
