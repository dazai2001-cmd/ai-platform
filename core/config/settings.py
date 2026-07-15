import os
import json
from dotenv import load_dotenv
from core.config.constants import *

load_dotenv()


class Settings:
    # App
    APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
    IS_PRODUCTION = APP_ENV == "production"
    APP_VERSION = os.getenv("APP_VERSION", "dev").strip() or "dev"
    GIT_SHA = os.getenv("GIT_SHA", "unknown").strip()[:40] or "unknown"
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    PORT = int(os.getenv("PORT", "5000"))
    CORS_ORIGINS = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
        if origin.strip()
    ]
    API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "").strip()
    AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "false").lower() == "true"
    AUTH_SESSION_DAYS = int(os.getenv("AUTH_SESSION_DAYS", "14"))
    AUTH_VERIFICATION_HOURS = int(os.getenv("AUTH_VERIFICATION_HOURS", "24"))
    AUTH_COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "ai_platform_session").strip() or "ai_platform_session"
    AUTH_COOKIE_SECURE = os.getenv(
        "AUTH_COOKIE_SECURE",
        "true" if IS_PRODUCTION else "false",
    ).lower() == "true"
    AUTH_COOKIE_SAMESITE = os.getenv("AUTH_COOKIE_SAMESITE", "Lax").strip().title() or "Lax"
    APP_PUBLIC_URL = os.getenv("APP_PUBLIC_URL", "http://127.0.0.1:3000").rstrip("/")
    RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
    EMAIL_FROM = os.getenv("EMAIL_FROM", "AI Platform <onboarding@resend.dev>").strip()
    SEND_VERIFICATION_EMAILS = os.getenv(
        "SEND_VERIFICATION_EMAILS",
        "true" if os.getenv("AI_RUNTIME", "local").strip().lower() == "cloud" else "false",
    ).lower() == "true"
    AI_RUNTIME = os.getenv("AI_RUNTIME", "local").strip().lower()
    IS_CLOUD_RUNTIME = AI_RUNTIME == "cloud"

    # Ollama
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180"))
    LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "768"))

    # Cloud LLM providers
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
    GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    GEMINI_MODELS = [
        model.strip()
        for model in os.getenv("GEMINI_MODELS", "gemini-3.5-flash").split(",")
        if model.strip()
    ]
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
    OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    OPENROUTER_MODELS = [
        model.strip()
        for model in os.getenv("OPENROUTER_MODELS", "").split(",")
        if model.strip()
    ]
    CLOUD_DEFAULT_MODEL = (
        os.getenv("CLOUD_DEFAULT_MODEL", "").strip()
        or (f"gemini:{GEMINI_MODELS[0]}" if GEMINI_API_KEY and GEMINI_MODELS else "")
        or (f"openrouter:{OPENROUTER_MODELS[0]}" if OPENROUTER_API_KEY and OPENROUTER_MODELS else "")
        or MODEL_GEMINI_FLASH
    )

    # Models
    ROUTER_MODEL = os.getenv("ROUTER_MODEL", CLOUD_DEFAULT_MODEL if IS_CLOUD_RUNTIME else MODEL_QWEN)
    TASK_MODEL_OVERRIDES = json.loads(os.getenv("CLOUD_TASK_MODELS_JSON" if IS_CLOUD_RUNTIME else "TASK_MODELS_JSON", "{}"))
    TASK_MODELS = {
        **(dict.fromkeys(DEFAULT_CLOUD_TASK_MODEL_MAP, CLOUD_DEFAULT_MODEL) if IS_CLOUD_RUNTIME else DEFAULT_TASK_MODEL_MAP),
        **TASK_MODEL_OVERRIDES,
    }
    LOCAL_ALLOWED_MODELS = [
        model.strip()
        for model in os.getenv(
            "LOCAL_ALLOWED_MODELS",
            ",".join(sorted({ROUTER_MODEL, *TASK_MODELS.values()})),
        ).split(",")
        if model.strip()
    ]

    # Embeddings
    EMBEDDING_PROVIDER = os.getenv(
        "EMBEDDING_PROVIDER",
        "hashing" if IS_CLOUD_RUNTIME else "local",
    ).strip().lower()
    EMBED_MODEL = os.getenv("EMBED_MODEL", DEFAULT_EMBED_MODEL)
    GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-2").strip()
    EMBED_DIM = int(os.getenv(
        "EMBED_DIM",
        "1024" if EMBEDDING_PROVIDER == "hashing" else str(EMBED_DIM),
    ))
    EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "100"))
    EMBED_MAX_RETRIES = int(os.getenv("EMBED_MAX_RETRIES", "4"))
    EMBED_RETRY_BASE_SECONDS = float(os.getenv("EMBED_RETRY_BASE_SECONDS", "1"))
    EMBED_RETRY_MAX_SECONDS = float(os.getenv("EMBED_RETRY_MAX_SECONDS", "60"))

    # RAG
    CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", DEFAULT_CHUNK_SIZE))
    CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", DEFAULT_CHUNK_OVERLAP))
    TOP_K = int(os.getenv("TOP_K", DEFAULT_TOP_K))
    INDEX_PATH = os.getenv(
        "INDEX_PATH",
        (
            f"data/indexes/faiss-{GEMINI_EMBED_MODEL}-{EMBED_DIM}.index"
            if EMBEDDING_PROVIDER == "gemini"
            else (
                f"data/indexes/faiss-hashing-v1-{EMBED_DIM}.index"
                if EMBEDDING_PROVIDER == "hashing"
                else INDEX_PATH
            )
        ),
    )
    MAX_URL_INGEST_BYTES = int(os.getenv("MAX_URL_INGEST_BYTES", "5242880"))
    MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
    MAX_JSON_BYTES = int(os.getenv("MAX_JSON_BYTES", str(1024 * 1024)))
    MAX_PROMPT_CHARS = int(os.getenv("MAX_PROMPT_CHARS", "12000"))
    MAX_TEXT_INGEST_CHARS = int(os.getenv("MAX_TEXT_INGEST_CHARS", "200000"))
    MAX_SOURCE_CHARS = int(os.getenv("MAX_SOURCE_CHARS", "512"))
    MAX_CHUNKS_PER_DOCUMENT = int(os.getenv("MAX_CHUNKS_PER_DOCUMENT", "2000"))
    MAX_CHUNKS_PER_USER = int(os.getenv("MAX_CHUNKS_PER_USER", "20000"))
    MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "250"))
    MAX_CV_UPLOAD_BYTES = int(os.getenv("MAX_CV_UPLOAD_BYTES", str(10 * 1024 * 1024)))
    MAX_CV_PDF_PAGES = int(os.getenv("MAX_CV_PDF_PAGES", "25"))
    MAX_CV_DOCX_ARCHIVE_FILES = int(os.getenv("MAX_CV_DOCX_ARCHIVE_FILES", "250"))
    MAX_CV_DOCX_UNCOMPRESSED_BYTES = int(
        os.getenv("MAX_CV_DOCX_UNCOMPRESSED_BYTES", str(25 * 1024 * 1024))
    )
    MAX_CV_DOCX_COMPRESSION_RATIO = float(os.getenv("MAX_CV_DOCX_COMPRESSION_RATIO", "100"))
    MAX_DATASET_ROWS = int(os.getenv("MAX_DATASET_ROWS", "100000"))
    MAX_DATASET_COLUMNS = int(os.getenv("MAX_DATASET_COLUMNS", "200"))
    MAX_DATASET_UPLOAD_BYTES = int(os.getenv("MAX_DATASET_UPLOAD_BYTES", str(25 * 1024 * 1024)))
    MAX_DATASET_MEMORY_BYTES = int(os.getenv("MAX_DATASET_MEMORY_BYTES", str(128 * 1024 * 1024)))
    MAX_DATASET_MEMORY_BYTES_PER_USER = int(
        os.getenv("MAX_DATASET_MEMORY_BYTES_PER_USER", str(256 * 1024 * 1024))
    )
    MAX_DATASETS_PER_USER = int(os.getenv("MAX_DATASETS_PER_USER", "10"))
    MAX_DATASET_STORAGE_BYTES_PER_USER = int(
        os.getenv("MAX_DATASET_STORAGE_BYTES_PER_USER", str(100 * 1024 * 1024))
    )
    MAX_EXCEL_UNCOMPRESSED_BYTES = int(
        os.getenv("MAX_EXCEL_UNCOMPRESSED_BYTES", str(100 * 1024 * 1024))
    )
    MAX_EXCEL_ARCHIVE_FILES = int(os.getenv("MAX_EXCEL_ARCHIVE_FILES", "1000"))
    MAX_EXCEL_COMPRESSION_RATIO = float(os.getenv("MAX_EXCEL_COMPRESSION_RATIO", "100"))
    BI_SQL_TIMEOUT_MS = int(os.getenv("BI_SQL_TIMEOUT_MS", "1000"))
    BI_MAX_SQL_CHARS = int(os.getenv("BI_MAX_SQL_CHARS", "10000"))
    BI_MAX_RESULT_CELL_BYTES = int(os.getenv("BI_MAX_RESULT_CELL_BYTES", str(1024 * 1024)))
    MAX_DOCUMENTS_PER_USER = int(os.getenv("MAX_DOCUMENTS_PER_USER", "100"))

    # API abuse controls. Use a shared Redis URI in multi-instance deployments.
    RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
    RATE_LIMIT_STORAGE_URI = os.getenv("RATE_LIMIT_STORAGE_URI", "memory://").strip() or "memory://"
    RATE_LIMIT_AUTH = os.getenv("RATE_LIMIT_AUTH", "5 per minute")
    RATE_LIMIT_AI = os.getenv("RATE_LIMIT_AI", "10 per minute")
    RATE_LIMIT_UPLOAD = os.getenv("RATE_LIMIT_UPLOAD", "5 per minute")
    # Never trust forwarded client IPs implicitly. Deployments behind a known
    # proxy must opt in with the exact number of trusted hops.
    TRUST_PROXY_HOPS = int(os.getenv("TRUST_PROXY_HOPS", "0"))

    # Privacy-conscious analytics. Production stores hashes and bounded
    # metadata by default; local development can retain query text for the UI.
    ANALYTICS_STORE_QUERY_TEXT = os.getenv(
        "ANALYTICS_STORE_QUERY_TEXT",
        "false" if IS_PRODUCTION else "true",
    ).lower() == "true"
    ANALYTICS_MAX_FILE_BYTES = int(os.getenv("ANALYTICS_MAX_FILE_BYTES", str(10 * 1024 * 1024)))
    ANALYTICS_RETENTION_DAYS = int(os.getenv("ANALYTICS_RETENTION_DAYS", "30"))

    # Career search providers
    ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "").strip()
    ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "").strip()
    REED_API_KEY = os.getenv("REED_API_KEY", "").strip()
    CAREER_CLOUD_MAX_SCORE_BATCH_JOBS = int(os.getenv("CAREER_CLOUD_MAX_SCORE_BATCH_JOBS", "10"))
    CAREER_CLOUD_DAILY_SCORE_LIMIT = int(os.getenv("CAREER_CLOUD_DAILY_SCORE_LIMIT", "25"))
    CAREER_CLOUD_DAILY_PACK_LIMIT = int(os.getenv("CAREER_CLOUD_DAILY_PACK_LIMIT", "5"))

    # Memory
    REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
    MAX_MEMORY_MESSAGES = int(os.getenv("MAX_MEMORY_MESSAGES", MAX_MEMORY_MESSAGES))
    MEMORY_TTL = int(os.getenv("MEMORY_TTL", MEMORY_TTL_SECONDS))

    # Paths
    RAW_DATA_PATH = os.getenv("RAW_DATA_PATH", RAW_DATA_PATH)
    PROCESSED_DATA_PATH = os.getenv("PROCESSED_DATA_PATH", PROCESSED_DATA_PATH)
    UPLOAD_PATH = os.getenv("UPLOAD_PATH", "data/uploads")
    BI_MANIFEST_PATH = os.getenv("BI_MANIFEST_PATH", "data/processed/bi_datasets.json")
    SQLITE_PATH = os.getenv("SQLITE_PATH", "data/processed/app.db")

    # Application database. A PostgreSQL/Supabase connection URL selects the
    # shared database backend; leaving it blank preserves SQLite for local
    # development and isolated tests. App tables live outside Supabase's
    # Data API-exposed public schema by default.
    DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
    DATABASE_SCHEMA = os.getenv("DATABASE_SCHEMA", "app_private").strip() or "app_private"
    DATABASE_SSLMODE = os.getenv(
        "DATABASE_SSLMODE",
        "require" if IS_PRODUCTION else "prefer",
    ).strip().lower()
    DATABASE_CONNECT_TIMEOUT_SECONDS = int(os.getenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "10"))
    DATABASE_POOL_MIN_SIZE = int(os.getenv("DATABASE_POOL_MIN_SIZE", "1"))
    DATABASE_POOL_MAX_SIZE = int(os.getenv("DATABASE_POOL_MAX_SIZE", "5"))


settings = Settings()
