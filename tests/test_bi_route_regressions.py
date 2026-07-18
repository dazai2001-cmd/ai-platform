import io
from unittest.mock import Mock

import pytest
from flask import Flask

from domain.bi.pipeline import BIBusyError, BIInputError


def _bi_decision() -> dict:
    return {
        "action": "bi_ask",
        "confidence": 1.0,
        "arguments": {},
        "router_model": "router-model",
        "router_source": "test",
    }


def test_workspace_bi_auto_selects_the_only_dataset(monkeypatch):
    from domain.router import workspace_router as router_module

    router = router_module.WorkspaceRouter()
    monkeypatch.setattr(router, "route", lambda _query, user_id: _bi_decision())
    list_datasets = Mock(return_value=[{"name": "sales"}])
    ask = Mock(return_value={"answer": "total revenue is 300"})
    monkeypatch.setattr(router_module.bi_agent, "list_datasets", list_datasets)
    monkeypatch.setattr(router_module.bi_agent, "ask", ask)
    monkeypatch.setattr(router_module.model_settings, "model_for", Mock(return_value="bi-model"))

    result = router.handle("show revenue", session_id="session-1", user_id="owner")

    assert result["route"] == "bi"
    list_datasets.assert_called_once_with(user_id="owner")
    ask.assert_called_once_with(
        "show revenue",
        session_id="session-1",
        dataset_name="sales",
        model="bi-model",
        user_id="owner",
    )


@pytest.mark.parametrize(
    ("datasets", "message"),
    [
        ([], "No dataset is available"),
        ([{"name": "sales"}, {"name": "inventory"}], "Multiple datasets are available"),
    ],
)
def test_workspace_bi_requires_a_safe_selection_for_zero_or_multiple_datasets(
    monkeypatch, datasets, message
):
    from domain.router import workspace_router as router_module

    router = router_module.WorkspaceRouter()
    monkeypatch.setattr(router, "route", lambda _query, user_id: _bi_decision())
    ask = Mock()
    monkeypatch.setattr(router_module.bi_agent, "list_datasets", Mock(return_value=datasets))
    monkeypatch.setattr(router_module.bi_agent, "ask", ask)

    with pytest.raises(BIInputError, match=message):
        router.handle("show revenue", session_id="session-1", user_id="owner")

    ask.assert_not_called()


def test_workspace_bi_honours_an_explicit_dataset_without_guessing(monkeypatch):
    from domain.router import workspace_router as router_module

    router = router_module.WorkspaceRouter()
    monkeypatch.setattr(router, "route", lambda _query, user_id: _bi_decision())
    list_datasets = Mock()
    ask = Mock(return_value={"answer": "inventory is 20"})
    monkeypatch.setattr(router_module.bi_agent, "list_datasets", list_datasets)
    monkeypatch.setattr(router_module.bi_agent, "ask", ask)
    monkeypatch.setattr(router_module.model_settings, "model_for", Mock(return_value="bi-model"))

    router.handle(
        "show inventory",
        session_id="session-2",
        user_id="owner",
        dataset_name="inventory",
    )

    list_datasets.assert_not_called()
    assert ask.call_args.kwargs["dataset_name"] == "inventory"


def test_workspace_api_threads_an_explicit_dataset(monkeypatch):
    from apps.api.routes import chat as chat_routes

    handle = Mock(return_value={"answer": "ok"})
    monkeypatch.setattr(chat_routes.workspace_router, "handle", handle)
    monkeypatch.setattr(chat_routes, "current_user_id", lambda: "owner")
    app = Flask(__name__)
    app.register_blueprint(chat_routes.chat_bp)

    response = app.test_client().post(
        "/api/chat/workspace",
        json={"query": "show inventory", "session_id": "session-3", "dataset": "inventory"},
    )

    assert response.status_code == 200
    handle.assert_called_once_with(
        "show inventory",
        session_id="session-3",
        user_id="owner",
        dataset_name="inventory",
    )


def test_invalid_sample_dataset_name_is_a_json_400():
    from apps.api.routes import bi as bi_routes

    app = Flask(__name__)
    app.register_blueprint(bi_routes.bi_bp)

    response = app.test_client().get("/api/bi/datasets/bad-name/sample")

    assert response.status_code == 400
    assert "Dataset name must start" in response.get_json()["error"]


def test_busy_bi_upload_is_a_safe_json_503(monkeypatch):
    from apps.api.routes import bi as bi_routes

    remove_upload = Mock()
    monkeypatch.setattr(bi_routes, "save_upload", Mock(return_value="scratch.csv"))
    monkeypatch.setattr(bi_routes, "remove_upload", remove_upload)
    monkeypatch.setattr(bi_routes.bi_agent, "load_csv", Mock(side_effect=BIBusyError()))
    app = Flask(__name__)
    app.register_blueprint(bi_routes.bi_bp)

    response = app.test_client().post(
        "/api/bi/upload",
        data={"file": (io.BytesIO(b"value\n1\n"), "sales.csv")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "BI analysis is busy on this server. Wait a moment and try again."
    }
    remove_upload.assert_called_once_with("scratch.csv")


def test_busy_bi_sample_is_a_safe_json_503(monkeypatch):
    from apps.api.routes import bi as bi_routes

    monkeypatch.setattr(bi_routes.bi_agent, "get_sample", Mock(side_effect=BIBusyError()))
    app = Flask(__name__)
    app.register_blueprint(bi_routes.bi_bp)

    response = app.test_client().get("/api/bi/datasets/sales/sample")

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "BI analysis is busy on this server. Wait a moment and try again."
    }


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/workspace", "/api/chat/stream"])
def test_non_bi_value_errors_remain_generic_502s(monkeypatch, endpoint):
    from apps.api import errors
    from apps.api.routes import chat as chat_routes

    monkeypatch.setattr(errors.settings, "DEBUG", False)
    monkeypatch.setattr(chat_routes.router, "route", Mock(return_value={"type": "general"}))
    monkeypatch.setattr(chat_routes.model_settings, "model_for", Mock(return_value="general-model"))
    internal_error = ValueError("Dim mismatch: got 12, expected 384")
    if endpoint == "/api/chat":
        monkeypatch.setattr(chat_routes.general_agent, "ask", Mock(side_effect=internal_error))
    elif endpoint == "/api/chat/workspace":
        monkeypatch.setattr(chat_routes.workspace_router, "handle", Mock(side_effect=internal_error))
    else:
        monkeypatch.setattr(chat_routes.general_agent, "stream_ask", Mock(side_effect=internal_error))

    app = Flask(__name__)
    app.register_blueprint(chat_routes.chat_bp)
    response = app.test_client().post(endpoint, json={"query": "hello"})

    assert response.status_code == 502
    assert response.get_json() == {"error": "Request failed"}
