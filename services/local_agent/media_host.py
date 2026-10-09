"""Local host worker API; application auth/ownership stays in the Docker API."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress

from flask import Flask, jsonify, request, send_file

from core.config.settings import settings
from services.local_agent.media_models import MediaRequest, catalogue, worker_info
from services.local_agent.media_service import MediaService

OWNER = "local-docker-api"


def create_host_app(service: MediaService | None = None):
    if settings.IS_CLOUD_RUNTIME or settings.IS_PRODUCTION or settings.LOCAL_MEDIA_WORKER_URL:
        raise ValueError("The media host requires native local development settings.")
    if len(settings.LOCAL_MEDIA_WORKER_TOKEN) < 32:
        raise ValueError("A local worker token with at least 32 characters is required.")
    app = Flask("local-media-host")
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    media = service or MediaService()
    app.extensions["media_service"] = media

    @app.before_request
    def authorize():
        try:
            address = ipaddress.ip_address(request.remote_addr or "")
            local_address = address.is_loopback or address.is_private
        except ValueError:
            local_address = False
        expected = "Bearer " + settings.LOCAL_MEDIA_WORKER_TOKEN
        if not local_address or not hmac.compare_digest(request.headers.get("Authorization", "").encode(), expected.encode()):
            return jsonify(error="Unauthorized"), 401
        if request.view_args and "job_id" in request.view_args:
            job_id = request.view_args["job_id"]
            if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
                return jsonify(error="Invalid job ID"), 400

    def host_id(job_id):
        return hashlib.sha256(f"{OWNER}:{job_id}".encode()).hexdigest()[:32]

    def snapshot(job):
        if not job:
            return jsonify(error="Generation not found"), 404
        return jsonify({key: job.get(key) for key in ("status", "progress", "message", "error", "worker_active")})

    @app.get("/health")
    def health():
        return jsonify(ok=True)

    @app.get("/capabilities")
    def capabilities():
        return jsonify(models=catalogue(), workers={kind: worker_info(kind) for kind in ("image", "video")})

    @app.post("/jobs/<job_id>")
    def submit(job_id):
        try:
            spec = MediaRequest.model_validate(request.get_json(silent=True) or {})
            return snapshot(media.submit(spec, OWNER, job_id))
        except ValueError:
            return jsonify(error="Invalid request or unavailable model. Check the host capabilities."), 400

    @app.get("/jobs/<job_id>")
    def get(job_id):
        return snapshot(media.get(host_id(job_id), OWNER))

    @app.post("/jobs/<job_id>/cancel")
    def cancel(job_id):
        return snapshot(media.cancel(host_id(job_id), OWNER))

    @app.delete("/jobs/<job_id>")
    def delete(job_id):
        try:
            deleted = media.delete(host_id(job_id), OWNER)
            return (jsonify(deleted=True), 200) if deleted else (jsonify(error="Generation not found"), 404)
        except ValueError:
            return jsonify(error="The worker is still active."), 409

    @app.get("/jobs/<job_id>/artifact")
    def artifact(job_id):
        job = media.get(host_id(job_id), OWNER)
        if not job or job["status"] != "succeeded":
            return jsonify(error="Output not found"), 404
        path = media.artifact_path(job)
        if not path.is_file():
            return jsonify(error="Output not found"), 404
        return send_file(path, mimetype="image/png" if job["kind"] == "image" else "video/mp4", conditional=True)

    return app
