from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from limits import parse
from limits.errors import ConfigurationError as LimitsConfigurationError

from core.config.settings import settings


class ConfigurationError(RuntimeError):
    """Raised when the application cannot start safely with its current settings."""


@dataclass(frozen=True)
class ConfigurationIssue:
    name: str
    message: str
    severity: str = "error"


def configured_model_allowlist(config=settings) -> set[str]:
    if config.IS_CLOUD_RUNTIME:
        models: set[str] = set()
        if config.GEMINI_API_KEY:
            models.update(f"gemini:{model}" for model in config.GEMINI_MODELS)
        if config.OPENROUTER_API_KEY:
            models.update(f"openrouter:{model}" for model in config.OPENROUTER_MODELS)
        return models
    return set(config.LOCAL_ALLOWED_MODELS)


def active_model_configuration_valid(config=settings) -> bool:
    if not config.IS_CLOUD_RUNTIME and not config.IS_PRODUCTION:
        return True
    allowed = configured_model_allowlist(config)
    selected = [config.ROUTER_MODEL, *config.TASK_MODELS.values()]
    if config.IS_CLOUD_RUNTIME:
        selected.append(config.CLOUD_DEFAULT_MODEL)
    return bool(allowed) and all(isinstance(model, str) and model in allowed for model in selected)


def configuration_issues(config=settings) -> list[ConfigurationIssue]:
    issues: list[ConfigurationIssue] = []

    if config.APP_ENV not in {"development", "test", "production"}:
        issues.append(ConfigurationIssue("APP_ENV", "must be development, test, or production"))

    if config.AI_RUNTIME not in {"local", "cloud"}:
        issues.append(ConfigurationIssue("AI_RUNTIME", "must be either 'local' or 'cloud'"))

    positive_values = {
        "MAX_UPLOAD_BYTES": config.MAX_UPLOAD_BYTES,
        "MAX_JSON_BYTES": config.MAX_JSON_BYTES,
        "MAX_PROMPT_CHARS": config.MAX_PROMPT_CHARS,
        "MAX_TEXT_INGEST_CHARS": config.MAX_TEXT_INGEST_CHARS,
        "MAX_SOURCE_CHARS": config.MAX_SOURCE_CHARS,
        "MAX_CHUNKS_PER_DOCUMENT": config.MAX_CHUNKS_PER_DOCUMENT,
        "MAX_CHUNKS_PER_USER": config.MAX_CHUNKS_PER_USER,
        "MAX_PDF_PAGES": config.MAX_PDF_PAGES,
        "MAX_CV_UPLOAD_BYTES": config.MAX_CV_UPLOAD_BYTES,
        "MAX_CV_PDF_PAGES": config.MAX_CV_PDF_PAGES,
        "MAX_CV_DOCX_ARCHIVE_FILES": config.MAX_CV_DOCX_ARCHIVE_FILES,
        "MAX_CV_DOCX_UNCOMPRESSED_BYTES": config.MAX_CV_DOCX_UNCOMPRESSED_BYTES,
        "MAX_CV_DOCX_COMPRESSION_RATIO": config.MAX_CV_DOCX_COMPRESSION_RATIO,
        "MAX_DATASET_ROWS": config.MAX_DATASET_ROWS,
        "MAX_DATASET_COLUMNS": config.MAX_DATASET_COLUMNS,
        "MAX_DATASET_UPLOAD_BYTES": config.MAX_DATASET_UPLOAD_BYTES,
        "MAX_DATASET_MEMORY_BYTES": config.MAX_DATASET_MEMORY_BYTES,
        "MAX_DATASET_MEMORY_BYTES_PER_USER": config.MAX_DATASET_MEMORY_BYTES_PER_USER,
        "MAX_DATASETS_PER_USER": config.MAX_DATASETS_PER_USER,
        "MAX_DATASET_STORAGE_BYTES_PER_USER": config.MAX_DATASET_STORAGE_BYTES_PER_USER,
        "MAX_DATASET_STORAGE_BYTES_TOTAL": config.MAX_DATASET_STORAGE_BYTES_TOTAL,
        "BI_MAX_CONCURRENT_QUERIES": config.BI_MAX_CONCURRENT_QUERIES,
        "BI_QUERY_SLOT_TIMEOUT_SECONDS": config.BI_QUERY_SLOT_TIMEOUT_SECONDS,
        "MAX_EXCEL_UNCOMPRESSED_BYTES": config.MAX_EXCEL_UNCOMPRESSED_BYTES,
        "MAX_EXCEL_ARCHIVE_FILES": config.MAX_EXCEL_ARCHIVE_FILES,
        "MAX_EXCEL_COMPRESSION_RATIO": config.MAX_EXCEL_COMPRESSION_RATIO,
        "BI_SQL_TIMEOUT_MS": config.BI_SQL_TIMEOUT_MS,
        "BI_MAX_SQL_CHARS": config.BI_MAX_SQL_CHARS,
        "BI_MAX_RESULT_CELL_BYTES": config.BI_MAX_RESULT_CELL_BYTES,
        "MAX_DOCUMENTS_PER_USER": config.MAX_DOCUMENTS_PER_USER,
        "ANALYTICS_MAX_FILE_BYTES": config.ANALYTICS_MAX_FILE_BYTES,
        "ANALYTICS_RETENTION_DAYS": config.ANALYTICS_RETENTION_DAYS,
        "CHUNK_SIZE": config.CHUNK_SIZE,
        "TOP_K": config.TOP_K,
        "EMBED_DIM": getattr(config, "EMBED_DIM", 384),
        "EMBED_BATCH_SIZE": getattr(config, "EMBED_BATCH_SIZE", 32),
        "EMBED_RETRY_BASE_SECONDS": getattr(config, "EMBED_RETRY_BASE_SECONDS", 1),
        "EMBED_RETRY_MAX_SECONDS": getattr(config, "EMBED_RETRY_MAX_SECONDS", 60),
        "DATABASE_CONNECT_TIMEOUT_SECONDS": config.DATABASE_CONNECT_TIMEOUT_SECONDS,
        "DATABASE_POOL_MIN_SIZE": config.DATABASE_POOL_MIN_SIZE,
        "DATABASE_POOL_MAX_SIZE": config.DATABASE_POOL_MAX_SIZE,
    }
    for name, value in positive_values.items():
        if value <= 0:
            issues.append(ConfigurationIssue(name, "must be greater than zero"))

    if config.CHUNK_OVERLAP < 0 or config.CHUNK_OVERLAP >= config.CHUNK_SIZE:
        issues.append(ConfigurationIssue("CHUNK_OVERLAP", "must be non-negative and smaller than CHUNK_SIZE"))

    embedding_provider = getattr(config, "EMBEDDING_PROVIDER", "local")
    if embedding_provider not in {"local", "gemini", "hashing"}:
        issues.append(ConfigurationIssue(
            "EMBEDDING_PROVIDER",
            "must be 'local', 'gemini', or 'hashing'",
        ))
    if getattr(config, "EMBED_MAX_RETRIES", 4) < 0:
        issues.append(ConfigurationIssue(
            "EMBED_MAX_RETRIES",
            "must be zero or greater",
        ))
    if (
        getattr(config, "EMBED_RETRY_BASE_SECONDS", 1)
        > getattr(config, "EMBED_RETRY_MAX_SECONDS", 60)
    ):
        issues.append(ConfigurationIssue(
            "EMBED_RETRY_BASE_SECONDS",
            "must not be greater than EMBED_RETRY_MAX_SECONDS",
        ))
    if embedding_provider == "gemini" and not config.GEMINI_API_KEY:
        issues.append(ConfigurationIssue(
            "GEMINI_API_KEY",
            "is required when Gemini embeddings are enabled",
        ))

    if config.TRUST_PROXY_HOPS < 0:
        issues.append(ConfigurationIssue("TRUST_PROXY_HOPS", "must be zero or greater"))

    if config.DATABASE_POOL_MIN_SIZE > config.DATABASE_POOL_MAX_SIZE:
        issues.append(ConfigurationIssue(
            "DATABASE_POOL_MIN_SIZE",
            "must not be greater than DATABASE_POOL_MAX_SIZE",
        ))

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", config.DATABASE_SCHEMA):
        issues.append(ConfigurationIssue(
            "DATABASE_SCHEMA",
            "must be a simple PostgreSQL identifier",
        ))

    if config.DATABASE_SSLMODE not in {
        "disable", "allow", "prefer", "require", "verify-ca", "verify-full"
    }:
        issues.append(ConfigurationIssue(
            "DATABASE_SSLMODE",
            "must be a valid PostgreSQL SSL mode",
        ))

    if config.DATABASE_URL:
        parsed_database_url = urlsplit(config.DATABASE_URL)
        if parsed_database_url.scheme.lower() not in {"postgres", "postgresql"} or not parsed_database_url.hostname:
            issues.append(ConfigurationIssue(
                "DATABASE_URL",
                "must be a PostgreSQL connection URL",
            ))

    for name in ("RATE_LIMIT_AUTH", "RATE_LIMIT_AI", "RATE_LIMIT_UPLOAD"):
        expression = getattr(config, name)
        try:
            parsed = parse(expression)
            if parsed.amount <= 0:
                raise ValueError
        except (LimitsConfigurationError, TypeError, ValueError):
            issues.append(ConfigurationIssue(name, "must be a valid positive rate-limit expression"))

    if not config.RATE_LIMIT_STORAGE_URI.startswith(("memory://", "redis://", "rediss://")):
        issues.append(ConfigurationIssue(
            "RATE_LIMIT_STORAGE_URI",
            "must use memory://, redis://, or rediss:// storage",
        ))

    if config.SEND_VERIFICATION_EMAILS and not config.RESEND_API_KEY:
        issues.append(ConfigurationIssue("RESEND_API_KEY", "is required when verification emails are enabled"))

    if config.AUTH_COOKIE_SAMESITE not in {"Lax", "Strict", "None"}:
        issues.append(ConfigurationIssue("AUTH_COOKIE_SAMESITE", "must be Lax, Strict, or None"))
    if config.AUTH_COOKIE_SAMESITE == "None" and not config.AUTH_COOKIE_SECURE:
        issues.append(ConfigurationIssue("AUTH_COOKIE_SECURE", "must be enabled when SameSite=None"))

    if config.IS_CLOUD_RUNTIME:
        has_gemini = bool(config.GEMINI_API_KEY and config.GEMINI_MODELS)
        has_openrouter = bool(config.OPENROUTER_API_KEY and config.OPENROUTER_MODELS)
        if not (has_gemini or has_openrouter):
            issues.append(ConfigurationIssue("cloud_model", "cloud runtime requires a configured Gemini or OpenRouter model"))

    if (config.IS_CLOUD_RUNTIME or config.IS_PRODUCTION) and not active_model_configuration_valid(config):
        issues.append(ConfigurationIssue(
            "MODEL_ALLOWLIST",
            "every active router, default, and task model must be in the configured allow-list",
        ))

    hardened_runtime = config.IS_CLOUD_RUNTIME or config.IS_PRODUCTION
    if hardened_runtime:
        if config.SECRET_KEY == "dev-secret-key" or len(config.SECRET_KEY) < 32:
            issues.append(ConfigurationIssue("SECRET_KEY", "a hardened runtime requires a non-default secret of at least 32 characters"))
        if not config.AUTH_REQUIRED:
            issues.append(ConfigurationIssue("AUTH_REQUIRED", "must be enabled in hardened runtimes"))
        if not config.RATE_LIMIT_ENABLED:
            issues.append(ConfigurationIssue("RATE_LIMIT_ENABLED", "must be enabled in hardened runtimes"))
        if config.RATE_LIMIT_STORAGE_URI == "memory://":
            issues.append(ConfigurationIssue(
                "RATE_LIMIT_STORAGE_URI",
                "memory storage is per-process; use Redis for multi-instance deployments",
                severity="error" if config.IS_PRODUCTION else "warning",
            ))
        if config.TRUST_PROXY_HOPS == 0:
            issues.append(ConfigurationIssue(
                "TRUST_PROXY_HOPS",
                "set the exact number of trusted reverse-proxy hops for per-client auth limits",
                severity="warning",
            ))
        if not config.APP_PUBLIC_URL.startswith("https://"):
            issues.append(ConfigurationIssue(
                "APP_PUBLIC_URL",
                "must use HTTPS in production" if config.IS_PRODUCTION else "should use HTTPS in cloud runtime",
                severity="error" if config.IS_PRODUCTION else "warning",
            ))
        if any(origin == "*" for origin in config.CORS_ORIGINS):
            issues.append(ConfigurationIssue("CORS_ORIGINS", "must not contain a wildcard in hardened runtimes"))
        if not config.CORS_ORIGINS:
            issues.append(ConfigurationIssue("CORS_ORIGINS", "must contain at least one frontend origin in hardened runtimes"))

    if config.IS_PRODUCTION:
        if not config.DATABASE_URL:
            issues.append(ConfigurationIssue(
                "DATABASE_URL",
                "is required in production; use the Supabase PostgreSQL connection URL",
            ))
        if config.DATABASE_SSLMODE not in {"require", "verify-ca", "verify-full"}:
            issues.append(ConfigurationIssue(
                "DATABASE_SSLMODE",
                "must require encrypted PostgreSQL connections in production",
            ))
        if config.DEBUG:
            issues.append(ConfigurationIssue("DEBUG", "must be disabled in production"))
        if not config.AUTH_COOKIE_SECURE:
            issues.append(ConfigurationIssue("AUTH_COOKIE_SECURE", "must be enabled in production"))
        if config.ANALYTICS_STORE_QUERY_TEXT:
            issues.append(ConfigurationIssue("ANALYTICS_STORE_QUERY_TEXT", "must be disabled in production"))
        if not config.SEND_VERIFICATION_EMAILS:
            issues.append(ConfigurationIssue("SEND_VERIFICATION_EMAILS", "must be enabled in production"))
        if not config.RESEND_API_KEY:
            issues.append(ConfigurationIssue("RESEND_API_KEY", "is required in production"))
        insecure_origins = [origin for origin in config.CORS_ORIGINS if not origin.startswith("https://")]
        if insecure_origins:
            issues.append(ConfigurationIssue("CORS_ORIGINS", "production origins must use HTTPS"))

    return issues


def validate_settings(config=settings) -> None:
    errors = [issue for issue in configuration_issues(config) if issue.severity == "error"]
    if not errors:
        return
    details = "; ".join(f"{issue.name}: {issue.message}" for issue in errors)
    raise ConfigurationError(f"Invalid application configuration: {details}")
