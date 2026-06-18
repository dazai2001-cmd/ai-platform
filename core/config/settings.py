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

    # Ollama
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180"))
    LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "768"))

    # Models
    ROUTER_MODEL = os.getenv("ROUTER_MODEL", MODEL_QWEN)
    TASK_MODELS = {
        **DEFAULT_TASK_MODEL_MAP,
        **json.loads(os.getenv("TASK_MODELS_JSON", "{}")),
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
