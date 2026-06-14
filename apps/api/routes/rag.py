import uuid
from flask import Blueprint, request, jsonify
from agents.rag_agent import rag_agent
from apps.api.deps import save_upload, ALLOWED_PDF
from apps.api.errors import error_response
from domain.router.router import QueryRouter

rag_bp = Blueprint("rag", __name__, url_prefix="/api/rag")
router = QueryRouter()


@rag_bp.post("/ask")
def ask():
    data = request.json or {}
    question = data.get("question", "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())

    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        route = router.route(question)
        result = rag_agent.ask(question, session_id=session_id, model=route["model"])
        result["route"] = route["type"]
        return jsonify(result)
    except Exception as e:
        return error_response(e, 502)


@rag_bp.post("/upload/pdf")
def upload_pdf():
    try:
        path = save_upload(request.files.get("file"), ALLOWED_PDF)
        filename = request.files["file"].filename
        count = rag_agent.ingest_pdf(path, filename=filename)
        return jsonify({"filename": filename, "chunks": count})
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
        count = rag_agent.ingest_url(url)
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
        count = rag_agent.ingest_text(text, source=source)
        return jsonify({"source": source, "chunks": count})
    except Exception as e:
        return error_response(e, 500, expose=True)


@rag_bp.get("/stats")
def stats():
    return jsonify(rag_agent.stats())
