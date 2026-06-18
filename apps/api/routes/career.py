from flask import Blueprint, jsonify, request

from apps.api.errors import error_response
from services.career import career_service

career_bp = Blueprint("career", __name__, url_prefix="/api/career")


def _payload():
    data = request.json or {}
    cv_text = data.get("cv_text", "").strip()
    job_description = data.get("job_description", "").strip()
    model = (data.get("model") or "").strip() or None
    if not cv_text:
        return None, jsonify({"error": "cv_text is required"}), 400
    if not job_description:
        return None, jsonify({"error": "job_description is required"}), 400
    return (cv_text, job_description, model), None, None


@career_bp.post("/analyze")
def analyze():
    args, error, status = _payload()
    if error:
        return error, status
    try:
        return jsonify(career_service.analyze_fit(*args))
    except Exception as e:
        return error_response(e, 502)


@career_bp.post("/tailor")
def tailor():
    args, error, status = _payload()
    if error:
        return error, status
    try:
        return jsonify(career_service.tailor_cv(*args))
    except Exception as e:
        return error_response(e, 502)


@career_bp.post("/cover-letter")
def cover_letter():
    args, error, status = _payload()
    if error:
        return error, status
    try:
        return jsonify(career_service.draft_cover_letter(*args))
    except Exception as e:
        return error_response(e, 502)


@career_bp.post("/pack")
def pack():
    args, error, status = _payload()
    if error:
        return error, status
    try:
        return jsonify(career_service.application_pack(*args))
    except Exception as e:
        return error_response(e, 502)
