import uuid
from pathlib import Path
from flask import Blueprint, request, jsonify
from agents.bi_agent import bi_agent
from apps.api.deps import save_upload, remove_upload, ALLOWED_DATA
from apps.api.errors import error_response
from apps.api.auth_context import current_user_id
from core.config.constants import TASK_BI
from core.config.settings import settings
from domain.bi.pipeline import BIInputError, BIProviderError
from services.settings.model_settings_service import model_settings

bi_bp = Blueprint("bi", __name__, url_prefix="/api/bi")


@bi_bp.post("/upload")
def upload():
    path = None
    try:
        f = request.files.get("file")
        if not f or f.filename == "":
            raise ValueError("No file provided")
        name = request.form.get("name") or Path(f.filename).stem
        path = save_upload(f, ALLOWED_DATA, max_bytes=settings.MAX_DATASET_UPLOAD_BYTES)
        user_id = current_user_id()
        if f.filename.lower().endswith(".csv"):
            info = bi_agent.load_csv(path, name, user_id=user_id)
        else:
            info = bi_agent.load_excel(path, name, user_id=user_id)
        return jsonify(info)
    except BIProviderError as e:
        return error_response(e, e.status_code, expose=True)
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 500)
    finally:
        # The pipeline persists the bounded upload in the application database,
        # so Render's temporary filesystem is only request-scoped scratch space.
        remove_upload(path)


@bi_bp.get("/datasets")
def datasets():
    try:
        return jsonify(bi_agent.list_datasets(user_id=current_user_id()))
    except Exception as e:
        return error_response(e, 500, expose=False)


@bi_bp.get("/datasets/<name>/sample")
def sample(name: str):
    try:
        result = bi_agent.get_sample(name, user_id=current_user_id())
    except BIProviderError as e:
        return error_response(e, e.status_code, expose=True)
    except BIInputError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 500, expose=False)
    if result is None:
        return jsonify({"error": "Dataset not found"}), 404
    return jsonify(result)


@bi_bp.delete("/datasets/<name>")
def delete_dataset(name: str):
    try:
        if not bi_agent.delete_dataset(name, user_id=current_user_id()):
            return jsonify({"error": "Dataset not found"}), 404
        return jsonify({"deleted": name})
    except ValueError as e:
        return error_response(e, 400)


@bi_bp.post("/ask")
def ask():
    data = request.json or {}
    question = data.get("question", "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())
    dataset = data.get("dataset")

    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        user_id = current_user_id()
        result = bi_agent.ask(
            question,
            session_id=session_id,
            dataset_name=dataset,
            model=model_settings.model_for("bi", user_id=user_id),
            user_id=user_id,
        )
        result["route"] = TASK_BI
        return jsonify(result)
    except BIProviderError as e:
        return error_response(e, e.status_code, expose=True)
    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        return error_response(e, 500, expose=False)
    except Exception as e:
        return error_response(e, 500, expose=False)
    except Exception as e:
        return error_response(e, 502)
