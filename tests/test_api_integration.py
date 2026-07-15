import io
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest


PASSWORD = "integration-password-123"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Create an authenticated-mode app backed only by temporary test data."""
    from core.config.settings import settings
    from services.storage.sqlite_service import SQLiteService, db

    isolated_db = SQLiteService(str(tmp_path / "app.db"))
    monkeypatch.setattr(db, "path", isolated_db.path)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(settings, "API_AUTH_TOKEN", "")
    monkeypatch.setattr(settings, "SEND_VERIFICATION_EMAILS", False)
    monkeypatch.setattr(settings, "IS_CLOUD_RUNTIME", False)
    monkeypatch.setattr(settings, "IS_PRODUCTION", False)
    monkeypatch.setattr(settings, "AUTH_COOKIE_NAME", "test_session")
    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", False)
    monkeypatch.setattr(settings, "AUTH_COOKIE_SAMESITE", "Lax")
    monkeypatch.setattr(settings, "APP_PUBLIC_URL", "http://127.0.0.1:3000")
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["http://127.0.0.1:3000"])
    monkeypatch.setattr(settings, "INDEX_PATH", str(tmp_path / "faiss.index"))

    from apps.api import deps
    from apps.api.main import create_app

    monkeypatch.setattr(deps, "UPLOAD_PATH", tmp_path / "uploads")
    app = create_app({
        "TESTING": True,
        "RATELIMIT_ENABLED": False,
        "SKIP_CONFIG_VALIDATION": True,
    })
    with app.test_client() as test_client:
        yield test_client


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_verified_session(client, email: str) -> dict:
    signup = client.post("/api/auth/signup", json={"email": email, "password": PASSWORD})
    assert signup.status_code == 201
    signup_body = signup.get_json()
    assert signup_body["user"]["email"] == email
    assert signup_body["user"]["email_verified"] is False

    verification = client.get(
        "/api/auth/verify",
        query_string={"token": signup_body["verification_token"]},
    )
    assert verification.status_code == 200
    assert verification.get_json()["user"]["email_verified"] is True

    login = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.get_json()


def test_auth_signup_verify_login_me_and_logout(client):
    signup = client.post(
        "/api/auth/signup",
        json={"email": "person@example.com", "password": PASSWORD},
    )
    assert signup.status_code == 201
    signup_body = signup.get_json()
    assert signup_body["user"]["email"] == "person@example.com"
    assert signup_body["user"]["email_verified"] is False
    assert "password" not in signup_body["user"]
    assert signup_body["verification_token"]

    unverified_login = client.post(
        "/api/auth/login",
        json={"email": "person@example.com", "password": PASSWORD},
    )
    assert unverified_login.status_code == 401
    assert "verify your email" in unverified_login.get_json()["error"].lower()

    verified = client.get(
        "/api/auth/verify",
        query_string={"token": signup_body["verification_token"]},
    )
    assert verified.status_code == 200
    assert verified.get_json()["user"]["email_verified"] is True

    reused_link = client.get(
        "/api/auth/verify",
        query_string={"token": signup_body["verification_token"]},
    )
    assert reused_link.status_code == 400

    login = client.post(
        "/api/auth/login",
        json={"email": "person@example.com", "password": PASSWORD},
    )
    assert login.status_code == 200
    session = login.get_json()
    headers = _bearer(session["token"])

    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.get_json()["user"]["id"] == session["user"]["id"]

    protected = client.get("/api/chat/conversations", headers=headers)
    assert protected.status_code == 200

    logout = client.post("/api/auth/logout", headers=headers)
    assert logout.status_code == 200
    assert logout.get_json() == {"ok": True}
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.get("/api/chat/conversations", headers=headers).status_code == 401


def test_browser_session_cookie_authenticates_and_requires_trusted_origin(client):
    signup = client.post(
        "/api/auth/signup",
        json={"email": "browser@example.com", "password": PASSWORD},
    )
    token = signup.get_json()["verification_token"]
    assert client.get("/api/auth/verify", query_string={"token": token}).status_code == 200

    login = client.post(
        "/api/auth/login",
        json={"email": "browser@example.com", "password": PASSWORD},
    )
    assert login.status_code == 200
    set_cookie = login.headers["Set-Cookie"]
    assert "test_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie
    assert "Path=/" in set_cookie
    assert "Secure" not in set_cookie
    assert login.headers["Cache-Control"] == "no-store"

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.get_json()["user"]["email"] == "browser@example.com"
    assert client.get("/api/chat/conversations").status_code == 200

    missing_origin = client.post(
        "/api/chat/conversations",
        json={"id": "cookie-session", "title": "Cookie session"},
    )
    assert missing_origin.status_code == 403
    assert missing_origin.get_json() == {
        "error": "trusted Origin required for cookie-authenticated request"
    }

    malicious_origin = client.post(
        "/api/chat/conversations",
        json={"id": "cookie-session", "title": "Cookie session"},
        headers={"Origin": "http://127.0.0.1:3000.evil.example"},
    )
    assert malicious_origin.status_code == 403

    trusted_origin = {"Origin": "http://127.0.0.1:3000"}
    created = client.post(
        "/api/chat/conversations",
        json={"id": "cookie-session", "title": "Cookie session"},
        headers=trusted_origin,
    )
    assert created.status_code == 200

    blocked_logout = client.post("/api/auth/logout")
    assert blocked_logout.status_code == 403
    assert client.get("/api/auth/me").status_code == 200

    logout = client.post("/api/auth/logout", headers=trusted_origin)
    assert logout.status_code == 200
    assert logout.get_json() == {"ok": True}
    assert "test_session=;" in logout.headers["Set-Cookie"]
    assert client.get_cookie("test_session") is None
    assert client.get("/api/auth/me").status_code == 401


def test_auth_browser_origins_and_secure_cookie_attributes(client, monkeypatch):
    from core.config.settings import settings

    credentials = {"email": "secure-cookie@example.com", "password": PASSWORD}
    untrusted = client.post(
        "/api/auth/signup",
        json=credentials,
        headers={"Origin": "https://evil.example"},
    )
    assert untrusted.status_code == 403
    assert untrusted.get_json() == {"error": "untrusted request Origin"}

    trusted_origin = {"Origin": "http://127.0.0.1:3000"}
    signup = client.post("/api/auth/signup", json=credentials, headers=trusted_origin)
    assert signup.status_code == 201
    token = signup.get_json()["verification_token"]
    assert client.get("/api/auth/verify", query_string={"token": token}).status_code == 200

    blocked_login = client.post(
        "/api/auth/login",
        json=credentials,
        headers={"Origin": "http://127.0.0.1:3000.attacker.test"},
    )
    assert blocked_login.status_code == 403

    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", True)
    login = client.post("/api/auth/login", json=credentials, headers=trusted_origin)
    assert login.status_code == 200
    assert "token" not in login.get_json()
    set_cookie = login.headers["Set-Cookie"]
    assert "Secure" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie


def test_bearer_auth_remains_csrf_exempt_for_non_browser_clients(client):
    session = _create_verified_session(client, "bearer-client@example.com")
    # Remove the browser cookie so this exercises bearer authentication alone.
    client.delete_cookie("test_session")

    response = client.post(
        "/api/chat/conversations",
        json={"id": "bearer-session", "title": "Bearer session"},
        headers=_bearer(session["token"]),
    )

    assert response.status_code == 200


def test_production_never_exposes_verification_secrets(client, monkeypatch):
    from core.config.settings import settings

    monkeypatch.setattr(settings, "IS_PRODUCTION", True)
    signup = client.post(
        "/api/auth/signup",
        json={"email": "production@example.com", "password": PASSWORD},
    )
    assert signup.status_code == 201
    assert "verification_token" not in signup.get_json()
    assert "verification_url" not in signup.get_json()

    resent = client.post(
        "/api/auth/resend-verification",
        json={"email": "production@example.com"},
    )
    assert resent.status_code == 200
    assert "verification_token" not in resent.get_json()
    assert "verification_url" not in resent.get_json()


def test_protected_api_rejects_missing_and_invalid_sessions(client):
    missing = client.get("/api/chat/conversations")
    invalid = client.get(
        "/api/chat/conversations",
        headers=_bearer("not-a-real-session"),
    )

    assert missing.status_code == 401
    assert missing.get_json() == {"error": "login required"}
    assert invalid.status_code == 401
    assert invalid.get_json() == {"error": "login required"}


def test_auto_chat_dispatches_rag_with_authenticated_user(client, monkeypatch):
    session = _create_verified_session(client, "rag-router@example.com")
    user_id = session["user"]["id"]

    from apps.api.routes import chat as chat_routes

    route = Mock(return_value={"type": "rag"})
    ask = Mock(return_value={"answer": "Grounded answer", "sources": ["guide.pdf"]})
    model_for = Mock(return_value="test-rag-model")
    monkeypatch.setattr(chat_routes.router, "route", route)
    monkeypatch.setattr(chat_routes.rag_agent, "ask", ask)
    monkeypatch.setattr(chat_routes.model_settings, "model_for", model_for)

    response = client.post(
        "/api/chat",
        json={"query": "What does the guide say?", "session_id": "chat-session"},
        headers=_bearer(session["token"]),
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "answer": "Grounded answer",
        "route": "rag",
        "session_id": "chat-session",
        "sources": ["guide.pdf"],
    }
    route.assert_called_once_with("What does the guide say?")
    model_for.assert_called_once_with("rag", user_id=user_id)
    ask.assert_called_once_with(
        "What does the guide say?",
        session_id="chat-session",
        model="test-rag-model",
        user_id=user_id,
    )


def test_auto_chat_stream_dispatches_rag_and_sets_route_headers(client, monkeypatch):
    session = _create_verified_session(client, "rag-stream@example.com")
    user_id = session["user"]["id"]

    from apps.api.routes import chat as chat_routes

    stream_ask = Mock(
        return_value=(iter(["Grounded ", "stream"]), "stream-session", "stream-model")
    )
    monkeypatch.setattr(chat_routes.router, "route", Mock(return_value={"type": "rag"}))
    monkeypatch.setattr(chat_routes.rag_agent, "stream_ask", stream_ask)
    monkeypatch.setattr(chat_routes.model_settings, "model_for", Mock(return_value="stream-model"))

    response = client.post(
        "/api/chat/stream",
        json={"query": "Stream the answer", "session_id": "stream-session"},
        headers=_bearer(session["token"]),
    )

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "Grounded stream"
    assert response.headers["X-Session-Id"] == "stream-session"
    assert response.headers["X-Route"] == "rag"
    assert response.headers["X-Model"] == "stream-model"
    stream_ask.assert_called_once_with(
        "Stream the answer",
        session_id="stream-session",
        model="stream-model",
        user_id=user_id,
    )


def test_dedicated_rag_endpoint_uses_rag_model_and_user(client, monkeypatch):
    session = _create_verified_session(client, "rag-endpoint@example.com")
    user_id = session["user"]["id"]

    from apps.api.routes import rag as rag_routes

    ask = Mock(return_value={"answer": "RAG answer", "session_id": "rag-session"})
    model_for = Mock(return_value="dedicated-rag-model")
    monkeypatch.setattr(rag_routes.rag_agent, "ask", ask)
    monkeypatch.setattr(rag_routes.model_settings, "model_for", model_for)

    response = client.post(
        "/api/rag/ask",
        json={"question": "Use my documents", "session_id": "rag-session"},
        headers=_bearer(session["token"]),
    )

    assert response.status_code == 200
    assert response.get_json()["route"] == "rag"
    model_for.assert_called_once_with("rag", user_id=user_id)
    ask.assert_called_once_with(
        "Use my documents",
        session_id="rag-session",
        model="dedicated-rag-model",
        user_id=user_id,
    )


def test_dedicated_bi_endpoint_uses_dataset_model_and_user(client, monkeypatch):
    session = _create_verified_session(client, "bi-endpoint@example.com")
    user_id = session["user"]["id"]

    from apps.api.routes import bi as bi_routes

    ask = Mock(return_value={"answer": "Revenue is 42", "rows": [{"revenue": 42}]})
    model_for = Mock(return_value="dedicated-bi-model")
    monkeypatch.setattr(bi_routes.bi_agent, "ask", ask)
    monkeypatch.setattr(bi_routes.model_settings, "model_for", model_for)

    response = client.post(
        "/api/bi/ask",
        json={
            "question": "What is revenue?",
            "dataset": "sales",
            "session_id": "bi-session",
        },
        headers=_bearer(session["token"]),
    )

    assert response.status_code == 200
    assert response.get_json()["route"] == "bi"
    model_for.assert_called_once_with("bi", user_id=user_id)
    ask.assert_called_once_with(
        "What is revenue?",
        session_id="bi-session",
        dataset_name="sales",
        model="dedicated-bi-model",
        user_id=user_id,
    )


def test_pdf_upload_validates_ingests_for_owner_and_removes_temp_file(
    client, monkeypatch, tmp_path
):
    session = _create_verified_session(client, "pdf-owner@example.com")
    user_id = session["user"]["id"]

    from apps.api.routes import rag as rag_routes

    seen = {}

    def ingest_pdf(path, filename, user_id):
        saved = Path(path)
        seen["path"] = saved
        seen["bytes"] = saved.read_bytes()
        seen["filename"] = filename
        seen["user_id"] = user_id
        return 3

    ingest = Mock(side_effect=ingest_pdf)
    monkeypatch.setattr(rag_routes.rag_agent, "documents", Mock(return_value=[]))
    monkeypatch.setattr(rag_routes.rag_agent, "ingest_pdf", ingest)

    response = client.post(
        "/api/rag/upload/pdf",
        data={"file": (io.BytesIO(b"%PDF-1.4\nmock document"), "notes.pdf")},
        content_type="multipart/form-data",
        headers=_bearer(session["token"]),
    )

    assert response.status_code == 200
    assert response.get_json() == {"filename": "notes.pdf", "chunks": 3}
    assert seen["bytes"].startswith(b"%PDF")
    assert seen["filename"] == "notes.pdf"
    assert seen["user_id"] == user_id
    assert seen["path"].parent == tmp_path / "uploads"
    assert not seen["path"].exists()

    rejected = client.post(
        "/api/rag/upload/pdf",
        data={"file": (io.BytesIO(b"not a PDF"), "fake.pdf")},
        content_type="multipart/form-data",
        headers=_bearer(session["token"]),
    )
    assert rejected.status_code == 400
    assert rejected.get_json()["error"] == "Invalid PDF file"
    assert ingest.call_count == 1


def test_url_ingestion_checks_capacity_and_passes_authenticated_user(client, monkeypatch):
    session = _create_verified_session(client, "url-owner@example.com")
    user_id = session["user"]["id"]

    from apps.api.routes import rag as rag_routes

    documents = Mock(return_value=[])
    ingest_url = Mock(return_value=4)
    monkeypatch.setattr(rag_routes.rag_agent, "documents", documents)
    monkeypatch.setattr(rag_routes.rag_agent, "ingest_url", ingest_url)

    response = client.post(
        "/api/rag/upload/url",
        json={"url": "https://example.com/guide"},
        headers=_bearer(session["token"]),
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "url": "https://example.com/guide",
        "chunks": 4,
    }
    documents.assert_called_once_with(user_id=user_id)
    ingest_url.assert_called_once_with(
        "https://example.com/guide",
        user_id=user_id,
    )

    monkeypatch.setattr(rag_routes.settings, "MAX_DOCUMENTS_PER_USER", 1)
    documents.return_value = [{"source": "existing.pdf"}]
    blocked = client.post(
        "/api/rag/upload/url",
        json={"url": "https://example.com/another-guide"},
        headers=_bearer(session["token"]),
    )
    assert blocked.status_code == 400
    assert "Document limit reached" in blocked.get_json()["error"]
    assert ingest_url.call_count == 1


def test_async_url_ingestion_returns_before_embedding_and_releases_reservation(client, monkeypatch):
    session = _create_verified_session(client, "async-url-owner@example.com")
    user_id = session["user"]["id"]

    from apps.api.routes import rag as rag_routes

    started = {}
    ingest_url = Mock(return_value=6)
    monkeypatch.setattr(rag_routes.rag_agent, "documents", Mock(return_value=[]))
    monkeypatch.setattr(rag_routes.rag_agent, "ingest_url", ingest_url)

    def start(label, fn, user_id):
        started.update(label=label, fn=fn, user_id=user_id)
        return {"id": "url-job-1", "status": "queued"}

    monkeypatch.setattr(rag_routes.jobs, "start", start)

    response = client.post(
        "/api/rag/upload/url/async",
        json={"url": "https://example.com/long-guide"},
        headers=_bearer(session["token"]),
    )

    assert response.status_code == 202
    assert response.get_json() == {
        "job_id": "url-job-1",
        "status": "queued",
        "url": "https://example.com/long-guide",
    }
    assert started["user_id"] == user_id
    assert started["label"] == "Ingest https://example.com/long-guide"
    assert ingest_url.call_count == 0

    assert started["fn"]() == {
        "url": "https://example.com/long-guide",
        "chunks": 6,
    }
    ingest_url.assert_called_once_with(
        "https://example.com/long-guide",
        user_id=user_id,
    )


def test_bi_csv_upload_selects_csv_loader_and_passes_owner(client, monkeypatch, tmp_path):
    session = _create_verified_session(client, "csv-owner@example.com")
    user_id = session["user"]["id"]

    from apps.api.routes import bi as bi_routes

    seen = {}

    def load_csv(path, name, user_id):
        saved = Path(path)
        seen.update(path=saved, content=saved.read_text(), name=name, user_id=user_id)
        return {"name": name, "rows": 2, "columns": ["amount"]}

    loader = Mock(side_effect=load_csv)
    monkeypatch.setattr(bi_routes.bi_agent, "load_csv", loader)

    response = client.post(
        "/api/bi/upload",
        data={
            "name": "sales",
            "file": (io.BytesIO(b"amount\n10\n20\n"), "sales.csv"),
        },
        content_type="multipart/form-data",
        headers=_bearer(session["token"]),
    )

    assert response.status_code == 200
    assert response.get_json() == {"name": "sales", "rows": 2, "columns": ["amount"]}
    assert seen["content"] == "amount\n10\n20\n"
    assert seen["name"] == "sales"
    assert seen["user_id"] == user_id
    assert seen["path"].parent == tmp_path / "uploads"


def test_rejected_bi_upload_removes_saved_temp_file(client, monkeypatch, tmp_path):
    session = _create_verified_session(client, "csv-rejected@example.com")
    from apps.api.routes import bi as bi_routes

    monkeypatch.setattr(
        bi_routes.bi_agent,
        "load_csv",
        Mock(side_effect=ValueError("Dataset exceeds the row limit")),
    )
    response = client.post(
        "/api/bi/upload",
        data={"file": (io.BytesIO(b"amount\n10\n20\n"), "too-large.csv")},
        content_type="multipart/form-data",
        headers=_bearer(session["token"]),
    )

    assert response.status_code == 400
    upload_dir = tmp_path / "uploads"
    assert not upload_dir.exists() or list(upload_dir.iterdir()) == []


def test_model_settings_get_and_update_are_scoped_to_authenticated_user(
    client, monkeypatch
):
    session = _create_verified_session(client, "model-owner@example.com")
    user_id = session["user"]["id"]

    from apps.api.routes import health as health_routes

    get_settings = Mock(return_value={"general": "initial-model"})
    update_settings = Mock(return_value={"general": "updated-model"})
    monkeypatch.setattr(health_routes.model_settings, "get", get_settings)
    monkeypatch.setattr(health_routes.model_settings, "update", update_settings)
    monkeypatch.setattr(health_routes.ollama, "health", Mock(return_value=False))

    fetched = client.get(
        "/api/settings/models",
        headers=_bearer(session["token"]),
    )
    updated = client.put(
        "/api/settings/models",
        json={"task_models": {"general": "updated-model"}},
        headers=_bearer(session["token"]),
    )

    assert fetched.status_code == 200
    assert fetched.get_json() == {
        "task_models": {"general": "initial-model"},
        "available_models": [],
    }
    assert updated.status_code == 200
    assert updated.get_json() == {
        "task_models": {"general": "updated-model"},
        "available_models": [],
    }
    get_settings.assert_called_once_with(user_id=user_id)
    update_settings.assert_called_once_with(
        {"general": "updated-model"},
        user_id=user_id,
    )


def test_conversations_cannot_be_read_deleted_or_overwritten_cross_user(client):
    owner = _create_verified_session(client, "conversation-owner@example.com")
    stranger = _create_verified_session(client, "conversation-stranger@example.com")
    owner_headers = _bearer(owner["token"])
    stranger_headers = _bearer(stranger["token"])
    conversation_id = "private-conversation"

    created = client.post(
        "/api/chat/conversations",
        json={"id": conversation_id, "title": "Owner title"},
        headers=owner_headers,
    )
    assert created.status_code == 200

    saved = client.put(
        f"/api/chat/conversations/{conversation_id}",
        json={
            "title": "Owner title",
            "messages": [{"role": "user", "content": "owner secret"}],
        },
        headers=owner_headers,
    )
    assert saved.status_code == 200

    assert client.get(
        f"/api/chat/conversations/{conversation_id}", headers=stranger_headers
    ).status_code == 404
    assert all(
        item["id"] != conversation_id
        for item in client.get(
            "/api/chat/conversations", headers=stranger_headers
        ).get_json()
    )

    overwrite = client.put(
        f"/api/chat/conversations/{conversation_id}",
        json={
            "title": "Stolen title",
            "messages": [{"role": "user", "content": "attacker content"}],
        },
        headers=stranger_headers,
    )
    assert overwrite.status_code == 404

    collision = client.post(
        "/api/chat/conversations",
        json={"id": conversation_id, "title": "Stolen title"},
        headers=stranger_headers,
    )
    assert collision.status_code == 409

    deleted = client.delete(
        f"/api/chat/conversations/{conversation_id}", headers=stranger_headers
    )
    assert deleted.status_code == 200

    still_owned = client.get(
        f"/api/chat/conversations/{conversation_id}", headers=owner_headers
    )
    assert still_owned.status_code == 200
    assert still_owned.get_json()["title"] == "Owner title"
    assert still_owned.get_json()["messages"][0]["content"] == "owner secret"


def test_document_listing_preview_and_delete_are_scoped_cross_user(
    client, monkeypatch, tmp_path
):
    owner = _create_verified_session(client, "document-owner@example.com")
    stranger = _create_verified_session(client, "document-stranger@example.com")

    from agents.rag_agent import rag_agent
    from infrastructure.vectorstore.faiss_store import FAISSStore

    store = FAISSStore(dim=2, index_path=str(tmp_path / "documents" / "index.faiss"))
    store.add(
        np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
        [
            {
                "source": "shared-name.pdf",
                "text": "owner-only document text",
                "user_id": owner["user"]["id"],
            },
            {
                "source": "shared-name.pdf",
                "text": "stranger-only document text",
                "user_id": stranger["user"]["id"],
            },
        ],
    )
    monkeypatch.setattr(rag_agent, "store", store)
    monkeypatch.setattr(rag_agent, "ensure_ready", Mock(return_value=rag_agent))

    owner_headers = _bearer(owner["token"])
    stranger_headers = _bearer(stranger["token"])
    owner_documents = client.get("/api/rag/documents", headers=owner_headers)
    stranger_documents = client.get("/api/rag/documents", headers=stranger_headers)

    assert owner_documents.status_code == 200
    assert owner_documents.get_json()[0]["preview"] == "owner-only document text"
    assert stranger_documents.status_code == 200
    assert stranger_documents.get_json()[0]["preview"] == "stranger-only document text"

    stranger_delete = client.delete(
        "/api/rag/documents/shared-name.pdf", headers=stranger_headers
    )
    assert stranger_delete.status_code == 200
    assert stranger_delete.get_json()["deleted_chunks"] == 1
    assert client.get("/api/rag/documents", headers=stranger_headers).get_json() == []

    owner_preview = client.get(
        "/api/rag/documents/shared-name.pdf", headers=owner_headers
    )
    assert owner_preview.status_code == 200
    assert owner_preview.get_json()["text"] == "owner-only document text"
