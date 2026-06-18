import uuid
from flask import Blueprint, request, jsonify, Response, stream_with_context
from domain.router.router import QueryRouter
from agents.rag_agent import rag_agent
from agents.bi_agent import bi_agent
from agents.critic_agent import critic_agent
from agents.general_agent import general_agent
from core.config.constants import TASK_BI, TASK_RAG
from apps.api.errors import error_response
from services.chat.conversation_service import conversations

chat_bp = Blueprint("chat", __name__, url_prefix="/api/chat")
router = QueryRouter()


@chat_bp.get("/conversations")
def list_conversations():
    return jsonify(conversations.list())


@chat_bp.post("/conversations")
def create_conversation():
    data = request.json or {}
    return jsonify(conversations.create(title=data.get("title") or "New chat", conversation_id=data.get("id")))


@chat_bp.get("/conversations/<conversation_id>")
def get_conversation(conversation_id: str):
    conversation = conversations.get(conversation_id)
    if not conversation:
        return jsonify({"error": "conversation not found"}), 404
    return jsonify(conversation)


@chat_bp.put("/conversations/<conversation_id>")
def save_conversation(conversation_id: str):
    data = request.json or {}
    return jsonify(conversations.save_messages(
        conversation_id=conversation_id,
        title=data.get("title") or "New chat",
        messages=data.get("messages") or [],
    ))


@chat_bp.delete("/conversations/<conversation_id>")
def delete_conversation(conversation_id: str):
    conversations.delete(conversation_id)
    return jsonify({"deleted": True})


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


@chat_bp.post("/general")
def general_chat():
    data = request.json or {}
    query = data.get("query", "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())
    model = data.get("model")

    if not query:
        return jsonify({"error": "query is required"}), 400

    try:
        result = general_agent.ask(query, session_id=session_id, model=model)
        result["route"] = "general"
        result["session_id"] = session_id
        return jsonify(result)
    except Exception as e:
        return error_response(e, 502)


@chat_bp.post("/general/stream")
def general_chat_stream():
    data = request.json or {}
    query = data.get("query", "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())
    model = data.get("model")

    if not query:
        return jsonify({"error": "query is required"}), 400

    generator, _, selected_model = general_agent.stream_ask(query, session_id=session_id, model=model)

    def stream():
        for token in generator:
            yield token

    response = Response(stream_with_context(stream()), mimetype="text/plain")
    response.headers["X-Session-Id"] = session_id
    response.headers["X-Route"] = "general"
    response.headers["X-Model"] = selected_model
    return response


@chat_bp.post("/stream")
def chat_stream():
    data = request.json or {}
    query = data.get("query", "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())

    if not query:
        return jsonify({"error": "query is required"}), 400

    route = router.route(query)
    task_type = route.get("type", "general")
    model = route.get("model")

    if task_type == TASK_RAG:
        generator, session_id, selected_model = rag_agent.stream_ask(query, session_id=session_id, model=model)
    elif task_type == TASK_BI:
        result = bi_agent.ask(query, session_id=session_id, dataset_name=data.get("dataset"), model=model)
        selected_model = result.get("model") or model or ""
        generator = iter([result.get("answer", "")])
    else:
        generator, session_id, selected_model = general_agent.stream_ask(query, session_id=session_id, model=model)

    def stream():
        for token in generator:
            yield token

    response = Response(stream_with_context(stream()), mimetype="text/plain")
    response.headers["X-Session-Id"] = session_id
    response.headers["X-Route"] = task_type
    response.headers["X-Model"] = selected_model
    return response
