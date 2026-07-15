import uuid
import threading
from flask import Blueprint, request, jsonify, Response, stream_with_context
from werkzeug.utils import secure_filename
from agents.rag_agent import rag_agent
from apps.api.deps import save_upload, remove_upload, ALLOWED_PDF
from apps.api.errors import error_response
from core.config.constants import TASK_RAG
from core.config.settings import settings
from services.jobs.job_service import jobs
from apps.api.auth_context import current_user_id
from services.settings.model_settings_service import model_settings

rag_bp = Blueprint("rag", __name__, url_prefix="/api/rag")
_DOCUMENT_QUOTA_LOCK = threading.Lock()
_PENDING_DOCUMENTS: dict[str, set[str]] = {}


class _DocumentReservation:
    def __init__(self, user_id: str, source: str):
        self.user_id = user_id
        self.source = source
        self.released = False

    def release(self) -> None:
        if self.released:
            return
        with _DOCUMENT_QUOTA_LOCK:
            pending = _PENDING_DOCUMENTS.get(self.user_id)
            if pending is not None:
                pending.discard(self.source)
                if not pending:
                    _PENDING_DOCUMENTS.pop(self.user_id, None)
            self.released = True


def _validated_source(value, *, default: str | None = None) -> str:
    if value is None:
        value = default
    if not isinstance(value, str):
        raise ValueError("source must be a string")
    source = value.strip()
    if not source:
        raise ValueError("source is required")
    if len(source) > settings.MAX_SOURCE_CHARS:
        raise ValueError(f"source exceeds the {settings.MAX_SOURCE_CHARS}-character limit")
    return source


def _reserve_document(user_id: str, source: str) -> _DocumentReservation:
    """Atomically include in-flight work in the single-process document quota."""
    with _DOCUMENT_QUOTA_LOCK:
        existing_sources = {
            document.get("source")
            for document in rag_agent.documents(user_id=user_id)
            if document.get("source")
        }
        pending = _PENDING_DOCUMENTS.setdefault(user_id, set())
        if source in existing_sources or source in pending:
            raise ValueError("A document with this source already exists. Delete it before uploading again.")
        if len(existing_sources | pending) >= settings.MAX_DOCUMENTS_PER_USER:
            raise ValueError("Document limit reached. Delete an existing document before uploading another.")
        pending.add(source)
    return _DocumentReservation(user_id, source)


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
    path = None
    reservation = None
    try:
        user_id = current_user_id()
        upload = request.files.get("file")
        filename = _validated_source(secure_filename(upload.filename) if upload else None, default="document.pdf")
        reservation = _reserve_document(user_id, filename)
        path = save_upload(upload, ALLOWED_PDF)
        count = rag_agent.ingest_pdf(path, filename=filename, user_id=user_id)
        return jsonify({"filename": filename, "chunks": count})
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 500)
    finally:
        remove_upload(path)
        if reservation:
            reservation.release()


@rag_bp.post("/upload/pdf/async")
def upload_pdf_async():
    path = None
    reservation = None
    try:
        user_id = current_user_id()
        upload = request.files.get("file")
        filename = _validated_source(secure_filename(upload.filename) if upload else None, default="document.pdf")
        reservation = _reserve_document(user_id, filename)
        path = save_upload(upload, ALLOWED_PDF)
        owned_path = path
        owned_reservation = reservation

        def ingest(path_to_ingest=owned_path, document_reservation=owned_reservation):
            try:
                count = rag_agent.ingest_pdf(path_to_ingest, filename=filename, user_id=user_id)
                return {"filename": filename, "chunks": count}
            finally:
                remove_upload(path_to_ingest)
                document_reservation.release()

        job = jobs.start(f"Ingest {filename}", ingest, user_id=user_id)
        path = None  # The background job now owns cleanup.
        reservation = None  # The background job now owns the quota reservation.
        return jsonify({"job_id": job["id"], "status": job["status"], "filename": filename}), 202
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 500)
    finally:
        remove_upload(path)
        if reservation:
            reservation.release()


@rag_bp.post("/upload/url")
def upload_url():
    reservation = None
    try:
        url = _validated_source((request.json or {}).get("url"))
        user_id = current_user_id()
        reservation = _reserve_document(user_id, url)
        count = rag_agent.ingest_url(url, user_id=user_id)
        return jsonify({"url": url, "chunks": count})
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 500)
    finally:
        if reservation:
            reservation.release()


@rag_bp.post("/upload/text")
def upload_text():
    data = request.json or {}
    raw_text = data.get("text", "")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return jsonify({"error": "text is required"}), 400
    text = raw_text.strip()
    reservation = None
    try:
        source = _validated_source(data.get("source"), default="note")
        if source == "note":
            source = f"note-{uuid.uuid4().hex[:12]}"
        user_id = current_user_id()
        reservation = _reserve_document(user_id, source)
        count = rag_agent.ingest_text(text, source=source, user_id=user_id)
        return jsonify({"source": source, "chunks": count})
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 500)
    finally:
        if reservation:
            reservation.release()


@rag_bp.get("/stats")
def stats():
    return jsonify(rag_agent.stats(user_id=current_user_id()))


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
