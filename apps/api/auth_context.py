from flask import g, has_request_context, request

from core.config.settings import settings


LOCAL_USER_ID = "local"


def bearer_token() -> str:
    if not has_request_context():
        return ""
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


def cookie_token() -> str:
    if not has_request_context():
        return ""
    cookie_name = getattr(settings, "AUTH_COOKIE_NAME", "ai_platform_session")
    return (request.cookies.get(cookie_name) or "").strip()


def request_auth_token() -> tuple[str, str]:
    """Return the request credential and its source, preferring explicit bearer auth."""
    token = bearer_token()
    if token:
        return token, "bearer"
    token = cookie_token()
    if token:
        return token, "cookie"
    return "", ""


def current_user_id() -> str:
    user = getattr(g, "current_user", None)
    if user and user.get("id"):
        return user["id"]
    return LOCAL_USER_ID
