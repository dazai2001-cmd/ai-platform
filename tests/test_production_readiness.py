from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.api.main import create_app
from core.config.settings import settings
from core.config.validation import ConfigurationError, configuration_issues, validate_settings
from infrastructure.llm.ollama_client import ollama
from services.settings.model_settings_service import ModelSettingsService


def _config(**overrides):
    values = {
        name: getattr(settings, name)
        for name in dir(settings)
        if name.isupper() and not name.startswith("_")
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_production_mode_fails_closed_even_with_local_models():
    config = _config(
        APP_ENV="production",
        IS_PRODUCTION=True,
        AI_RUNTIME="local",
        IS_CLOUD_RUNTIME=False,
        SECRET_KEY="dev-secret-key",
        AUTH_REQUIRED=False,
        RATE_LIMIT_ENABLED=True,
        RATE_LIMIT_STORAGE_URI="memory://",
        APP_PUBLIC_URL="http://app.example.test",
        CORS_ORIGINS=["http://app.example.test"],
        AUTH_COOKIE_SECURE=False,
        ANALYTICS_STORE_QUERY_TEXT=True,
        SEND_VERIFICATION_EMAILS=False,
        RESEND_API_KEY="",
        DEBUG=True,
    )

    with pytest.raises(ConfigurationError) as raised:
        validate_settings(config)

    message = str(raised.value)
    for name in (
        "SECRET_KEY",
        "AUTH_REQUIRED",
        "RATE_LIMIT_STORAGE_URI",
        "APP_PUBLIC_URL",
        "AUTH_COOKIE_SECURE",
        "ANALYTICS_STORE_QUERY_TEXT",
        "DATABASE_URL",
        "SEND_VERIFICATION_EMAILS",
        "RESEND_API_KEY",
        "DEBUG",
    ):
        assert name in message


def test_valid_production_configuration_with_postgres_passes():
    config = _config(
        APP_ENV="production",
        IS_PRODUCTION=True,
        AI_RUNTIME="local",
        IS_CLOUD_RUNTIME=False,
        SECRET_KEY="x" * 32,
        AUTH_REQUIRED=True,
        RATE_LIMIT_ENABLED=True,
        RATE_LIMIT_STORAGE_URI="redis://redis:6379",
        APP_PUBLIC_URL="https://app.example.test",
        CORS_ORIGINS=["https://app.example.test"],
        AUTH_COOKIE_SECURE=True,
        AUTH_COOKIE_SAMESITE="Lax",
        ANALYTICS_STORE_QUERY_TEXT=False,
        SEND_VERIFICATION_EMAILS=True,
        RESEND_API_KEY="configured",
        DEBUG=False,
        TRUST_PROXY_HOPS=1,
        DATABASE_URL="postgresql://app:secret@db.example.test/postgres",
        DATABASE_SSLMODE="require",
    )

    assert not [issue for issue in configuration_issues(config) if issue.severity == "error"]
    validate_settings(config)


def test_production_rejects_active_models_outside_the_allowlist():
    config = _config(
        APP_ENV="production",
        IS_PRODUCTION=True,
        AI_RUNTIME="local",
        IS_CLOUD_RUNTIME=False,
        SECRET_KEY="x" * 32,
        AUTH_REQUIRED=True,
        RATE_LIMIT_ENABLED=True,
        RATE_LIMIT_STORAGE_URI="redis://redis:6379",
        APP_PUBLIC_URL="https://app.example.test",
        CORS_ORIGINS=["https://app.example.test"],
        AUTH_COOKIE_SECURE=True,
        ANALYTICS_STORE_QUERY_TEXT=False,
        SEND_VERIFICATION_EMAILS=True,
        RESEND_API_KEY="configured",
        DEBUG=False,
        TRUST_PROXY_HOPS=1,
        LOCAL_ALLOWED_MODELS=["allowed-model"],
        ROUTER_MODEL="unapproved-model",
        TASK_MODELS={"general": "allowed-model"},
    )

    with pytest.raises(ConfigurationError, match="MODEL_ALLOWLIST"):
        validate_settings(config)


def test_api_security_headers_and_request_ids_are_applied():
    with patch.object(settings, "IS_PRODUCTION", True):
        app = create_app({"TESTING": True, "SKIP_CONFIG_VALIDATION": True, "RATELIMIT_ENABLED": False})
        client = app.test_client()
        supplied = client.get("/", headers={"X-Request-ID": "safe-request-123"})
        generated = client.get("/", headers={"X-Request-ID": "unsafe request with spaces"})

    assert supplied.headers["X-Request-ID"] == "safe-request-123"
    assert generated.headers["X-Request-ID"] != "unsafe request with spaces"
    assert supplied.headers["X-Content-Type-Options"] == "nosniff"
    assert supplied.headers["X-Frame-Options"] == "DENY"
    assert supplied.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in supplied.headers["Content-Security-Policy"]
    assert supplied.headers["Strict-Transport-Security"].startswith("max-age=31536000")


def test_health_endpoints_separate_liveness_and_readiness():
    app = create_app({"TESTING": True, "SKIP_CONFIG_VALIDATION": True, "RATELIMIT_ENABLED": False})
    client = app.test_client()

    assert client.get("/api/health/live").status_code == 200
    with (
        patch("apps.api.routes.health._database_ready", return_value=True),
        patch("apps.api.routes.health._rate_limit_store_ready", return_value=True),
        patch("apps.api.routes.health._model_provider_ready", return_value=False),
    ):
        response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.get_json()["checks"] == {
        "database": True,
        "rate_limit_store": True,
        "model_provider": False,
    }


def test_public_health_response_does_not_expose_provider_configuration():
    app = create_app({"TESTING": True, "SKIP_CONFIG_VALIDATION": True, "RATELIMIT_ENABLED": False})
    with (
        patch("apps.api.routes.health._database_ready", return_value=True),
        patch("apps.api.routes.health._rate_limit_store_ready", return_value=True),
        patch("apps.api.routes.health._model_provider_ready", return_value=True),
    ):
        response = app.test_client().get("/api/health")

    assert response.status_code == 200
    payload = response.get_json()
    assert set(payload) == {"status", "runtime", "version", "commit", "checks"}
    assert "provider_status" not in payload
    assert "task_models" not in payload
    assert "models" not in payload


def test_cloud_provider_models_are_allow_listed():
    with (
        patch.object(settings, "IS_CLOUD_RUNTIME", True),
        patch.object(settings, "GEMINI_API_KEY", "configured"),
        patch.object(settings, "GEMINI_MODELS", ["gemini-2.0-flash"]),
        patch.object(settings, "OPENROUTER_API_KEY", ""),
        patch.object(settings, "OPENROUTER_MODELS", []),
    ):
        assert ollama._provider_for("gemini:gemini-2.0-flash") == ("gemini", "gemini-2.0-flash")
        with pytest.raises(ValueError, match="allow-list"):
            ollama._provider_for("gemini:unapproved-expensive-model")
        with pytest.raises(ValueError, match="configured provider"):
            ollama._provider_for("unprefixed-model")


def test_production_local_models_are_allow_listed():
    with (
        patch.object(settings, "IS_CLOUD_RUNTIME", False),
        patch.object(settings, "IS_PRODUCTION", True),
        patch.object(settings, "LOCAL_ALLOWED_MODELS", ["qwen3:8b"]),
    ):
        assert ollama._provider_for("qwen3:8b") == ("ollama", "qwen3:8b")
        with pytest.raises(ValueError, match="allow-list"):
            ollama._provider_for("unapproved:70b")


def test_authenticated_users_receive_configured_task_model_overrides():
    service = ModelSettingsService.__new__(ModelSettingsService)
    with (
        patch.object(settings, "IS_CLOUD_RUNTIME", True),
        patch.object(settings, "CLOUD_DEFAULT_MODEL", "gemini:default"),
        patch.object(settings, "TASK_MODEL_OVERRIDES", {"general": "gemini:override"}),
        patch("services.settings.model_settings_service.db.query", return_value=[]),
    ):
        assert service.get(user_id="authenticated-user")["general"] == "gemini:override"


def test_local_production_model_choices_are_filtered_and_invalid_updates_fail():
    service = ModelSettingsService.__new__(ModelSettingsService)
    with (
        patch.object(settings, "IS_CLOUD_RUNTIME", False),
        patch.object(settings, "IS_PRODUCTION", True),
        patch.object(settings, "LOCAL_ALLOWED_MODELS", ["allowed-model"]),
        patch("apps.api.routes.health.ollama.health", return_value=True),
        patch(
            "apps.api.routes.health.ollama.list_models",
            return_value=["allowed-model", "installed-but-disallowed"],
        ),
    ):
        from apps.api.routes.health import _available_models

        assert _available_models() == ["allowed-model"]
        with pytest.raises(ValueError, match="allow-list"):
            service.update({"general": "installed-but-disallowed"}, user_id="user")
