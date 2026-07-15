from __future__ import annotations

from functools import update_wrapper

from flask import g, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from core.config.settings import settings


def _client_key() -> str:
    user = getattr(g, "current_user", None)
    if user and user.get("id"):
        return f"user:{user['id']}"

    return f"ip:{get_remote_address()}"


_LIMIT_GROUPS = {
    "auth": (
        lambda: settings.RATE_LIMIT_AUTH,
        ("auth.signup", "auth.login", "auth.verify_email", "auth.resend_verification"),
    ),
    "ai": (
        lambda: settings.RATE_LIMIT_AI,
        (
            "chat.chat", "chat.chat_stream", "chat.workspace_chat",
            "chat.general_chat", "chat.general_chat_stream", "rag.ask",
            "rag.ask_stream", "bi.ask", "health.warmup", "career.analyze",
            "career.tailor", "career.cover_letter", "career.pack",
            "career.import_job_url", "career.search_jobs", "career.search_jobs_stream",
            "career.score_job", "career.generate_match_pack", "career.create_score_batch",
        ),
    ),
    "upload": (
        lambda: settings.RATE_LIMIT_UPLOAD,
        (
            "rag.upload_pdf", "rag.upload_pdf_async", "rag.upload_url", "rag.upload_text",
            "bi.upload", "career.import_profile",
        ),
    ),
}


def configure_rate_limits(app) -> Limiter:
    """Attach shared-storage-aware limits after blueprints have been registered."""
    limiter = Limiter(
        key_func=_client_key,
        default_limits=[],
        storage_uri=settings.RATE_LIMIT_STORAGE_URI,
    )
    limiter.init_app(app)
    for scope, (limit_value, endpoints) in _LIMIT_GROUPS.items():
        shared_limit = limiter.shared_limit(limit_value, scope=scope)
        for endpoint in endpoints:
            view = app.view_functions.get(endpoint)
            if view is not None:
                # Flask blueprints reuse the same view callables across app
                # factories. Give each app its own wrapper so Flask-Limiter's
                # decorator metadata (which includes a weak limiter reference)
                # cannot be overwritten by a subsequently-created app.
                def app_local_view(*args, __view=view, **kwargs):
                    return __view(*args, **kwargs)

                update_wrapper(app_local_view, view, updated=())
                app.view_functions[endpoint] = shared_limit(app_local_view)

    # Flask-Limiter's decorated views intentionally keep a weak reference to
    # the Limiter. Retain the owning instance for the lifetime of this app.
    app.extensions["ai_platform_limiter"] = limiter
    return limiter
