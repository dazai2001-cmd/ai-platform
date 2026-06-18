import uuid
from pathlib import Path
from flask import Blueprint, request, jsonify
from agents.bi_agent import bi_agent
from apps.api.deps import save_upload, ALLOWED_DATA
from apps.api.errors import error_response
from core.config.constants import TASK_BI

bi_bp = Blueprint("bi", __name__, url_prefix="/api/bi")


@bi_bp.post("/upload")
def upload():
    try:
        f = request.files.get("file")
        if not f or f.filename == "":
            raise ValueError("No file provided")
        name = request.form.get("name") or Path(f.filename).stem
        path = save_upload(f, ALLOWED_DATA)
        if f.filename.lower().endswith(".csv"):
            info = bi_agent.load_csv(path, name)
        else:
            info = bi_agent.load_excel(path, name)
        return jsonify(info)
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 500)


@bi_bp.get("/datasets")
def datasets():
    return jsonify(bi_agent.list_datasets())


@bi_bp.get("/datasets/<name>/sample")
def sample(name: str):
    result = bi_agent.get_sample(name)
    if result is None:
        return jsonify({"error": "Dataset not found"}), 404
    return jsonify(result)


@bi_bp.post("/ask")
def ask():
    data = request.json or {}
    question = data.get("question", "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())
    dataset = data.get("dataset")

    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        result = bi_agent.ask(question, session_id=session_id, dataset_name=dataset)
        result["route"] = TASK_BI
        return jsonify(result)
    except Exception as e:
        return error_response(e, 502)
