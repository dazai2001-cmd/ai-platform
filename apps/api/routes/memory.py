from flask import Blueprint, jsonify, request
from services.memory.memory_service import memory

memory_bp = Blueprint("memory", __name__, url_prefix="/api/memory")


@memory_bp.get("/<session_id>")
def get_history(session_id: str):
    return jsonify(memory.get(session_id))


@memory_bp.delete("/<session_id>")
def clear_session(session_id: str):
    memory.clear(session_id)
    return jsonify({"cleared": True})


@memory_bp.get("")
def list_sessions():
    return jsonify(memory.list_sessions())


@memory_bp.get("/sessions")
def session_summaries():
    return jsonify(memory.session_summaries())


@memory_bp.get("/facts")
def get_facts():
    return jsonify(memory.facts())


@memory_bp.post("/facts")
def add_fact():
    content = (request.json or {}).get("content", "").strip()
    if not content:
        return jsonify({"error": "content is required"}), 400
    return jsonify(memory.add_fact(content))


@memory_bp.delete("/facts/<fact_id>")
def delete_fact(fact_id: str):
    memory.delete_fact(fact_id)
    return jsonify({"deleted": True})
