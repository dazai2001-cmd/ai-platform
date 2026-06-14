from flask import Blueprint, jsonify
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
