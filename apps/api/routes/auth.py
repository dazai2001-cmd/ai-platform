import time
from urllib.parse import urlsplit

from flask import Blueprint, g, jsonify, request

from apps.api.auth_context import bearer_token, cookie_token, request_auth_token
from apps.api.errors import error_response
from core.config.settings import settings
from services.auth.auth_service import AuthError, auth_service


auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _canonical_origin(value: str, *, strict_header: bool = False) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit((value or "").strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or (strict_header and parsed.path not in {"", "/"})
        ):
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None
    return parsed.scheme, parsed.hostname.lower(), port


def _has_trusted_origin() -> bool:
    supplied = _canonical_origin(request.headers.get("Origin", ""), strict_header=True)
    if not supplied:
        return False
    configured = [settings.APP_PUBLIC_URL, *settings.CORS_ORIGINS]
    return supplied in {
        origin
        for value in configured
        if (origin := _canonical_origin(value)) is not None
    }


def _cookie_options() -> dict:
    return {
        "httponly": True,
        "secure": bool(getattr(settings, "AUTH_COOKIE_SECURE", False)),
        "samesite": getattr(settings, "AUTH_COOKIE_SAMESITE", "Lax"),
        "path": "/",
    }


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@auth_bp.before_app_request
def enforce_cookie_request_origin():
    """Block CSRF when an HttpOnly session cookie authorizes an unsafe request."""
    if request.method in _SAFE_METHODS:
        return None

    # Login, signup, resend, and logout are public endpoints, so they may not
    # have an authenticated cookie yet. Browsers send Origin on these requests;
    # reject any supplied untrusted value while preserving CLI clients that do
    # not send an Origin header.
    if request.blueprint == "auth" and request.headers.get("Origin"):
        if _has_trusted_origin():
            return None
        return jsonify({"error": "untrusted request Origin"}), 403

    source = getattr(g, "auth_token_source", "")
    if request.endpoint == "auth.logout":
        _, source = request_auth_token()
    if source != "cookie":
        return None
    if _has_trusted_origin():
        return None
    return jsonify({"error": "trusted Origin required for cookie-authenticated request"}), 403


@auth_bp.post("/signup")
def signup():
    data = request.json or {}
    try:
        result = auth_service.create_user(data.get("email", ""), data.get("password", ""))
        response = jsonify({
            "ok": True,
            "message": "Account created. Verify your email before logging in.",
            **result,
        })
        response.status_code = 201
        return _no_store(response)
    except AuthError as e:
        return error_response(e, 400)


@auth_bp.post("/login")
def login():
    data = request.json or {}
    try:
        result = auth_service.login(data.get("email", ""), data.get("password", ""))
        response_body = dict(result)
        # Browser sessions should receive the credential only through the
        # HttpOnly cookie. Keep the response token for non-browser clients so
        # existing bearer-token integrations remain compatible.
        if request.headers.get("Origin") and _has_trusted_origin():
            response_body.pop("token", None)
        response = jsonify(response_body)
        response.set_cookie(
            getattr(settings, "AUTH_COOKIE_NAME", "ai_platform_session"),
            result["token"],
            max_age=max(0, int(result["expires_at"] - time.time())),
            **_cookie_options(),
        )
        return _no_store(response)
    except AuthError as e:
        return error_response(e, 401)


@auth_bp.get("/verify")
def verify_email():
    try:
        response = jsonify({"ok": True, **auth_service.verify_email(request.args.get("token", ""))})
        return _no_store(response)
    except AuthError as e:
        return error_response(e, 400)


@auth_bp.post("/resend-verification")
def resend_verification():
    data = request.json or {}
    try:
        response = jsonify({"ok": True, **auth_service.resend_verification(data.get("email", ""))})
        return _no_store(response)
    except AuthError as e:
        return error_response(e, 400)


@auth_bp.get("/me")
def me():
    user = auth_service.authenticate_token(bearer_token())
    if not user:
        return _no_store(jsonify({"user": None})), 401
    return _no_store(jsonify({"user": user}))


@auth_bp.post("/logout")
def logout():
    tokens = {token for token in (bearer_token(), cookie_token()) if token}
    for token in tokens:
        auth_service.logout(token)
    response = jsonify({"ok": True})
    response.delete_cookie(
        getattr(settings, "AUTH_COOKIE_NAME", "ai_platform_session"),
        **_cookie_options(),
    )
    return _no_store(response)
