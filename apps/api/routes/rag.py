import uuid
from flask import Blueprint, request, jsonify, Response, stream_with_context
from agents.rag_agent import rag_agent
from apps.api.deps import save_upload, ALLOWED_PDF
from apps.api.errors import error_response
from core.config.constants import TASK_RAG
from services.jobs.job_service import jobs
from apps.api.auth_context import current_user_id
from services.settings.model_settings_service import model_settings

rag_bp = Blueprint("rag", __name__, url_prefix="/api/rag")


@rag_bp.post("/ask")
def ask():
    data = request.json or {}
    question = data.get("question", "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())

    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        user_id = current_user_id()
        result = rag_agent.ask(question, session_id=session_id, model=model_settings.model_for("rag", user_id=user_id), user_id=user_id)
        result["route"] = TASK_RAG
        return jsonify(result)
    except Exception as e:
        return error_response(e, 502)


@rag_bp.post("/ask/stream")
def ask_stream():
    data = request.json or {}
    question = data.get("question", "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())

    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        user_id = current_user_id()
        generator, _, selected_model = rag_agent.stream_ask(question, session_id=session_id, model=model_settings.model_for("rag", user_id=user_id), user_id=user_id)

        def stream():
            for token in generator:
                yield token

        response = Response(stream_with_context(stream()), mimetype="text/plain")
        response.headers["X-Session-Id"] = session_id
        response.headers["X-Route"] = TASK_RAG
        response.headers["X-Model"] = selected_model
        return response
    except Exception as e:
        return error_response(e, 502)


@rag_bp.post("/upload/pdf")
def upload_pdf():
    try:
        path = save_upload(request.files.get("file"), ALLOWED_PDF)
        filename = request.files["file"].filename
        count = rag_agent.ingest_pdf(path, filename=filename, user_id=current_user_id())
        return jsonify({"filename": filename, "chunks": count})
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 500, expose=True)


@rag_bp.post("/upload/pdf/async")
def upload_pdf_async():
    try:
        path = save_upload(request.files.get("file"), ALLOWED_PDF)
        filename = request.files["file"].filename

        user_id = current_user_id()

        def ingest():
            count = rag_agent.ingest_pdf(path, filename=filename, user_id=user_id)
            return {"filename": filename, "chunks": count}

        job = jobs.start(f"Ingest {filename}", ingest, user_id=user_id)
        return jsonify({"job_id": job["id"], "status": job["status"], "filename": filename}), 202
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 500, expose=True)


@rag_bp.post("/upload/url")
def upload_url():
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "url is required"}), 400
    try:
        count = rag_agent.ingest_url(url, user_id=current_user_id())
        return jsonify({"url": url, "chunks": count})
    except Exception as e:
        return error_response(e, 500, expose=True)


@rag_bp.post("/upload/text")
def upload_text():
    data = request.json or {}
    text = data.get("text", "").strip()
    source = data.get("source", "note")
    if not text:
        return jsonify({"error": "text is required"}), 400
    try:
        count = rag_agent.ingest_text(text, source=source, user_id=current_user_id())
        return jsonify({"source": source, "chunks": count})
    except Exception as e:
        return error_response(e, 500, expose=True)


@rag_bp.get("/stats")
def stats():
    return jsonify(rag_agent.stats())


@rag_bp.get("/documents")
def documents():
    return jsonify(rag_agent.documents(user_id=current_user_id()))


@rag_bp.get("/documents/<path:source>")
def document_preview(source: str):
    return jsonify(rag_agent.document_preview(source, user_id=current_user_id()))


@rag_bp.delete("/documents/<path:source>")
def delete_document(source: str):
    deleted = rag_agent.delete_document(source, user_id=current_user_id())
    return jsonify({"source": source, "deleted_chunks": deleted})
