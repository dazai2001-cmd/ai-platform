from flask import Blueprint, jsonify

from services.jobs.job_service import jobs

jobs_bp = Blueprint("jobs", __name__, url_prefix="/api/jobs")


@jobs_bp.get("/<job_id>")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)
