import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, g, jsonify, request
from flask_cors import CORS
from core.config.settings import settings
from apps.api.routes.rag import rag_bp
from apps.api.routes.bi import bi_bp
from apps.api.routes.memory import memory_bp
from apps.api.routes.health import health_bp
from apps.api.routes.chat import chat_bp
from apps.api.routes.career import career_bp
from apps.api.routes.jobs import jobs_bp
from apps.api.routes.auth import auth_bp
from services.auth.auth_service import auth_service


PUBLIC_API_PREFIXES = (
    "/api/auth/",
    "/api/health",
)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = settings.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB uploads

    CORS(app, resources={r"/api/*": {"origins": settings.CORS_ORIGINS}})

    @app.before_request
    def require_api_token_and_user():
        if request.method == "OPTIONS":
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

    @app.get("/")
    def root():
        return {"name": "AI Platform", "version": "1.0", "agents": ["rag", "bi", "critic"]}

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=settings.PORT, debug=settings.DEBUG)
