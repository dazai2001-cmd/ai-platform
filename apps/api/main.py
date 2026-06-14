import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request
from flask_cors import CORS
from core.config.settings import settings
from apps.api.routes.rag import rag_bp
from apps.api.routes.bi import bi_bp
from apps.api.routes.memory import memory_bp
from apps.api.routes.health import health_bp
from apps.api.routes.chat import chat_bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = settings.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB uploads

    CORS(app, resources={r"/api/*": {"origins": settings.CORS_ORIGINS}})

    @app.before_request
    def require_api_token():
        if request.method == "OPTIONS":
            return None
        if not settings.API_AUTH_TOKEN or not request.path.startswith("/api/"):
            return None
        supplied = request.headers.get("X-API-Token", "")
        if supplied != settings.API_AUTH_TOKEN:
            return jsonify({"error": "unauthorized"}), 401
        return None

    for bp in [rag_bp, bi_bp, memory_bp, health_bp, chat_bp]:
        app.register_blueprint(bp)

    @app.get("/")
    def root():
        return {"name": "AI Platform", "version": "1.0", "agents": ["rag", "bi", "critic"]}

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=settings.PORT, debug=settings.DEBUG)
