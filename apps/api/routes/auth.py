from flask import Blueprint, jsonify, request

from apps.api.errors import error_response
from services.auth.auth_service import AuthError, auth_service


auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def _bearer_token() -> str:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


@auth_bp.post("/signup")
def signup():
    data = request.json or {}
    try:
        result = auth_service.create_user(data.get("email", ""), data.get("password", ""))
        return jsonify({
            "ok": True,
            "message": "Account created. Verify your email before logging in.",
            **result,
        }), 201
    except AuthError as e:
        return error_response(e, 400)


@auth_bp.post("/login")
def login():
    data = request.json or {}
    try:
        return jsonify(auth_service.login(data.get("email", ""), data.get("password", "")))
    except AuthError as e:
        return error_response(e, 401)


@auth_bp.get("/verify")
def verify_email():
    try:
        return jsonify({"ok": True, **auth_service.verify_email(request.args.get("token", ""))})
    except AuthError as e:
        return error_response(e, 400)


@auth_bp.post("/resend-verification")
def resend_verification():
    data = request.json or {}
    try:
        return jsonify({"ok": True, **auth_service.resend_verification(data.get("email", ""))})
    except AuthError as e:
        return error_response(e, 400)


@auth_bp.get("/me")
def me():
    user = auth_service.authenticate_token(_bearer_token())
    if not user:
        return jsonify({"user": None}), 401
    return jsonify({"user": user})


@auth_bp.post("/logout")
def logout():
    token = _bearer_token()
    if token:
        auth_service.logout(token)
    return jsonify({"ok": True})
