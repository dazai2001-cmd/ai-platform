# AI Platform

A local-first AI workspace for general chat, persistent memory, document RAG, natural-language analytics, and AI-assisted job searching. The application uses a Flask API, Next.js frontend, SQLite, Redis, FAISS, Ollama, and optional OpenAI-compatible model providers.

## Features

- General chat with streaming responses and persistent conversation history
- 2nd Brain with PDF, URL, and text ingestion backed by FAISS retrieval
- OCR fallback for scanned and image-only PDFs using OCRmyPDF and Tesseract
- Document Library with previews, chunk counts, deletion, and background ingestion progress
- Persistent, editable user memory and saved preferences
- BI analysis with validated SQL and generated visualisations
- Career Agent with saved profiles, job discovery, fit scoring, duplicate removal, and application packs
- Per-agent model selection from the Settings page
- Markdown exports for chat and career outputs

## Agents

| Route | Default Model | Purpose |
|---|---|---|
| RAG / 2nd Brain | Qwen3 8b | Document Q&A, notes, URLs |
| BI Dashboard | Qwen3 8b | Data analysis, SQL, charts |
| Memory | Llama 3 | Conversation history questions |
| General | Mistral | Open chat and general questions |
| Router | Qwen3 8b | Classifies each query and attaches the response model |

The defaults are configurable. Qwen, Llama, and Mistral run locally through Ollama. The platform also supports OpenAI-compatible providers, including the free `z-ai/glm-5.2-free` model through ZenMux. GLM 5.2 is optional rather than a default because free endpoints may enforce strict rate limits; failed or rate-limited requests can fall back to a local Ollama model.

## Quick Start

```bash
# 1. Pull models in Ollama
ollama pull qwen3:8b
ollama pull llama3
ollama pull mistral
ollama pull nomic-embed-text

# 2. Copy env file
cp .env.example .env

# Optional: protect API routes when exposed beyond localhost
# set API_AUTH_TOKEN in .env

# Optional: add job provider and OpenAI-compatible API keys to .env
# Never commit the populated .env file

# 3. Start everything
docker compose up --build

# 4. Open the app
open http://localhost:3000

# 5. Ingest PDFs (optional)
python scripts/ingest_pdfs.py data/raw/
```

## Optional GLM 5.2 Provider

The LLM client can call an OpenAI-compatible API alongside Ollama. To expose GLM 5.2 in Agent Settings, configure the following values in `.env`:

```dotenv
OPENAI_COMPAT_BASE_URL=https://zenmux.ai/api/v1
OPENAI_COMPAT_API_KEY=your_key_here
OPENAI_COMPAT_MODELS=z-ai/glm-5.2-free
OPENAI_COMPAT_FALLBACK_MODEL=qwen3:8b
OPENAI_COMPAT_COOLDOWN_SECONDS=300
```

API keys remain server-side and `.env` is ignored by git. When the hosted provider returns a rate-limit or availability error, the client temporarily cools it down and uses the configured Ollama fallback.

## Career Agent

The Career Agent saves a CV/profile and search preferences in SQLite, searches supported job feeds, removes duplicate listings, and scores jobs against the profile. Jobs scoring 70 or higher are placed in Matches; lower or unscored jobs remain in Found. Applied, skipped, and saved states persist across restarts.

Supported discovery sources:

- Adzuna, when `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` are configured
- Reed, when `REED_API_KEY` is configured
- Remotive
- Arbeitnow

Scoring runs as a persistent background queue so progress can be shown one job at a time and resumed after an API restart. Application packs are generated only for matched jobs and can be exported as Markdown.

## API Endpoints

If `API_AUTH_TOKEN` is set, send it as the `X-API-Token` header on every `/api/*` request.

| Method | Path | Description |
|---|---|---|
| POST | `/api/chat` | Auto-routed chat |
| POST | `/api/chat/stream` | Streaming routed chat |
| GET | `/api/chat/conversations` | Persistent chat list |
| POST | `/api/rag/ask` | RAG question |
| POST | `/api/rag/ask/stream` | Streaming RAG answer |
| POST | `/api/rag/upload/pdf` | Upload PDF |
| POST | `/api/rag/upload/pdf/async` | Background PDF/OCR ingestion |
| POST | `/api/rag/upload/url` | Ingest URL |
| POST | `/api/rag/upload/text` | Save note |
| GET | `/api/rag/documents` | Document Library contents |
| POST | `/api/bi/upload` | Upload CSV/Excel |
| POST | `/api/bi/ask` | BI question |
| POST | `/api/career/jobs/search` | Search configured job sources |
| POST | `/api/career/jobs/score-batches` | Queue persistent fit scoring |
| GET | `/api/career/jobs` | List tracked jobs |
| GET | `/api/health` | Health check |
| GET | `/api/analytics/summary` | Query stats |

## Model Routing

Response model selection has one owner: `QueryRouter.route(query)`.
It classifies the user query as `rag`, `bi`, `memory`, or `general`, then attaches the response model from `TASK_MODELS_JSON`.
Agents receive that selected model instead of choosing their own.

## Project Structure

```text
ai-platform/
|-- core/               config, prompts, schemas
|-- infrastructure/     ollama client, embedder, faiss
|-- application/        chunker, ingestion, retriever
|-- domain/             rag pipeline, bi pipeline, router
|-- agents/             rag_agent, bi_agent, critic_agent, planner_agent
|-- services/           career search, memory, analytics, storage
|-- apps/api/           flask routes
|-- frontend/           next.js app
|-- scripts/            ingest, test
`-- data/               raw, processed, indexes
```

## Safety Notes

- CORS defaults to local frontend origins only. Change `CORS_ORIGINS` deliberately.
- Uploaded files are stored under `data/uploads/`, which is ignored by git.
- URL ingestion only accepts public HTTP(S) text/HTML responses and caps response size.
- FAISS metadata is stored as JSON. Legacy pickle metadata is not loaded.
- BI datasets are reloaded from `BI_MANIFEST_PATH` after API restarts.
- API keys belong in `.env`; only blank examples are committed.
- The frontend includes Chat, Brain, BI Dashboard, Career, Memory, Settings, Analytics, and Status views.

## Tests

```bash
python -m pytest -q

cd frontend/nextjs-app
npx tsc --noEmit
```
