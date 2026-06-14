import uuid
from flask import Blueprint, request, jsonify, Response, stream_with_context
from domain.router.router import QueryRouter
from agents.rag_agent import rag_agent
from agents.bi_agent import bi_agent
from agents.critic_agent import critic_agent
from agents.general_agent import general_agent
from core.config.constants import TASK_BI, TASK_RAG
from apps.api.errors import error_response

chat_bp = Blueprint("chat", __name__, url_prefix="/api/chat")
router = QueryRouter()


@chat_bp.post("")
def chat():
    data = request.json or {}
    query = data.get("query", "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())
    use_critic = data.get("critic", False)

    if not query:
        return jsonify({"error": "query is required"}), 400

    # Route
    route = router.route(query)
    task_type = route.get("type", "rag")
    model = route.get("model")

    try:
        if task_type == TASK_BI:
            result = bi_agent.ask(query, session_id=session_id, dataset_name=data.get("dataset"), model=model)
        elif task_type == TASK_RAG:
            result = rag_agent.ask(query, session_id=session_id, model=model)
        else:
            result = general_agent.ask(query, session_id=session_id, model=model)

        # Optionally run critic
        if use_critic and task_type == TASK_RAG:
            critique = critic_agent.review_rag_result(query, result, model=model)
            result["critique"] = critique
            if critique.get("improved_answer"):
                result["answer"] = critique["improved_answer"]

        result["route"] = task_type
        result["session_id"] = session_id
        return jsonify(result)

    except Exception as e:
        return error_response(e, 502)


@chat_bp.post("/stream")
def chat_stream():
    data = request.json or {}
    query = data.get("query", "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())

    if not query:
        return jsonify({"error": "query is required"}), 400

    route = router.route(query)
    generator, session_id = rag_agent.stream_ask(query, session_id=session_id, model=route.get("model"))

    def stream():
        for token in generator:
            yield token

    return Response(stream_with_context(stream()), mimetype="text/plain")
