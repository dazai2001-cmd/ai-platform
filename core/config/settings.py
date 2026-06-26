import os
import json
from dotenv import load_dotenv
from core.config.constants import *

load_dotenv()


class Settings:
    # App
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
    APP_PUBLIC_URL = os.getenv("APP_PUBLIC_URL", "http://127.0.0.1:3000").rstrip("/")
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
        for model in os.getenv("GEMINI_MODELS", "gemini-2.0-flash").split(",")
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

    # Embeddings
    EMBED_MODEL = os.getenv("EMBED_MODEL", DEFAULT_EMBED_MODEL)

    # RAG
    CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", DEFAULT_CHUNK_SIZE))
    CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", DEFAULT_CHUNK_OVERLAP))
    TOP_K = int(os.getenv("TOP_K", DEFAULT_TOP_K))
    INDEX_PATH = os.getenv("INDEX_PATH", INDEX_PATH)
    MAX_URL_INGEST_BYTES = int(os.getenv("MAX_URL_INGEST_BYTES", "5242880"))

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


settings = Settings()
