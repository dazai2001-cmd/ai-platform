import json

from flask import Blueprint, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from apps.api.auth_context import current_user_id
from apps.api.deps import ALLOWED_CV, UploadTooLargeError, remove_upload, save_upload
from apps.api.errors import error_response
from core.config.settings import settings
from services.career import career_service
from services.career.cv_document_service import cv_documents
from services.career.job_search_service import career_jobs

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
    user_id = current_user_id()
    try:
        career_jobs.ensure_application_pack_allowed(user_id=user_id)
        result = career_service.application_pack(*args)
        career_jobs.record_application_pack(user_id=user_id)
        return jsonify(result)
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 502)


@career_bp.get("/preferences")
def get_preferences():
    return jsonify(career_jobs.preferences(user_id=current_user_id()))


@career_bp.put("/preferences")
def save_preferences():
    return jsonify(career_jobs.save_preferences(request.json or {}, user_id=current_user_id()))


@career_bp.get("/profile")
def get_profile():
    return jsonify(career_jobs.profile(user_id=current_user_id()))


@career_bp.put("/profile")
def save_profile():
    data = request.json or {}
    return jsonify(career_jobs.save_profile(data.get("cv_text", ""), user_id=current_user_id()))


@career_bp.post("/profile/import")
def import_profile():
    path = None
    try:
        upload = request.files.get("file")
        original_filename = upload.filename if upload else ""
        file_extension = f".{original_filename.rsplit('.', 1)[-1].lower()}" if "." in original_filename else ""
        filename = secure_filename(original_filename) if original_filename else ""
        if original_filename and (not filename or "." not in filename):
            filename = f"cv{file_extension}"
        path = save_upload(
            upload,
            ALLOWED_CV,
            max_bytes=settings.MAX_CV_UPLOAD_BYTES,
            limit_name="CV",
        )
        extracted = cv_documents.extract(path, original_filename)
        profile = career_jobs.save_profile(extracted.text, user_id=current_user_id())
        return jsonify({
            **profile,
            "filename": filename,
            "file_type": extracted.file_type,
            "characters": extracted.characters,
            "pages": extracted.pages,
            "used_ocr": extracted.used_ocr,
        })
    except RequestEntityTooLarge:
        raise
    except UploadTooLargeError as e:
        return error_response(e, 413)
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 500)
    finally:
        remove_upload(path)


@career_bp.delete("/profile")
def delete_profile():
    return jsonify(career_jobs.delete_profile(user_id=current_user_id()))


@career_bp.get("/jobs")
def list_jobs():
    return jsonify(career_jobs.list_jobs(user_id=current_user_id()))


@career_bp.post("/jobs")
def save_job():
    data = request.json or {}
    try:
        return jsonify(career_jobs.save_job(
            description=data.get("description", ""),
            title=data.get("title", ""),
            company=data.get("company", ""),
            location=data.get("location", ""),
            url=data.get("url", ""),
            source=data.get("source", "manual"),
            cv_text=data.get("cv_text", ""),
            user_id=current_user_id(),
        ))
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 502)


@career_bp.post("/jobs/import-url")
def import_job_url():
    data = request.json or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url is required"}), 400
    try:
        return jsonify(career_jobs.import_url(url, cv_text=data.get("cv_text", ""), user_id=current_user_id()))
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 502, expose=True)


@career_bp.post("/jobs/search")
def search_jobs():
    data = request.json or {}
    try:
        limit = int(data.get("limit", 50))
        limit = max(1, min(limit, 50))
        return jsonify(career_jobs.search_jobs(cv_text=data.get("cv_text", ""), limit=limit, user_id=current_user_id()))
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 502, expose=True)


@career_bp.post("/jobs/search/stream")
def search_jobs_stream():
    data = request.json or {}
    try:
        limit = int(data.get("limit", 50))
        limit = max(1, min(limit, 50))
    except ValueError as e:
        return error_response(e, 400)

    user_id = current_user_id()

    def stream():
        try:
            for event in career_jobs.stream_search_jobs(cv_text=data.get("cv_text", ""), limit=limit, user_id=user_id):
                yield json.dumps(event) + "\n"
        except Exception as e:
            yield json.dumps({"event": "error", "error": str(e)}) + "\n"

    return Response(stream_with_context(stream()), mimetype="application/x-ndjson")


@career_bp.post("/jobs/<job_id>/score")
def score_job(job_id: str):
    data = request.json or {}
    try:
        return jsonify(career_jobs.score_job(job_id, data.get("cv_text", ""), user_id=current_user_id()))
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 502)


@career_bp.post("/jobs/<job_id>/pack")
def generate_match_pack(job_id: str):
    data = request.json or {}
    user_id = current_user_id()
    job = career_jobs.get_job(job_id, user_id=user_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    if not isinstance(job.get("fit_score"), (int, float)) or job["fit_score"] < 70:
        return jsonify({"error": "application packs are available only for 70+ matches"}), 400

    cv_text = str(data.get("cv_text") or career_jobs.profile(user_id=user_id).get("cv_text") or "").strip()
    if not cv_text:
        return jsonify({"error": "CV/profile is required"}), 400
    try:
        career_jobs.ensure_application_pack_allowed(user_id=user_id)
        result = career_service.application_pack_for_match(
            cv_text,
            job["description"],
            job.get("analysis") or {},
            data.get("model"),
        )
        career_jobs.record_application_pack(user_id=user_id)
        return jsonify(result)
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 502)


@career_bp.post("/jobs/score-batches")
def create_score_batch():
    data = request.json or {}
    try:
        batch = career_jobs.create_score_batch(
            cv_text=data.get("cv_text", ""),
            job_ids=data.get("job_ids") if isinstance(data.get("job_ids"), list) else None,
            user_id=current_user_id(),
        )
        return jsonify(batch), 202
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 502)


@career_bp.get("/jobs/score-batches/current")
def current_score_batch():
    return jsonify(career_jobs.get_current_score_batch(user_id=current_user_id()))


@career_bp.get("/jobs/score-batches/<batch_id>")
def get_score_batch(batch_id: str):
    batch = career_jobs.get_score_batch(batch_id, user_id=current_user_id())
    if not batch:
        return jsonify({"error": "score batch not found"}), 404
    return jsonify(batch)


@career_bp.post("/jobs/score-batches/<batch_id>/cancel")
def cancel_score_batch(batch_id: str):
    try:
        return jsonify(career_jobs.cancel_score_batch(batch_id, user_id=current_user_id()))
    except ValueError as e:
        return error_response(e, 404)
    except Exception as e:
        return error_response(e, 502)


@career_bp.put("/jobs/<job_id>/status")
def update_job_status(job_id: str):
    data = request.json or {}
    try:
        return jsonify(career_jobs.update_status(job_id, data.get("status", ""), user_id=current_user_id()))
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 502)


@career_bp.delete("/jobs/<job_id>")
def delete_job(job_id: str):
    career_jobs.delete_job(job_id, user_id=current_user_id())
    return jsonify({"deleted": True})
