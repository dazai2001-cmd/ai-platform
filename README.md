# AI Platform

A local-first AI platform with RAG (2nd Brain), BI Dashboard, and smart routing, powered by Ollama.

## Agents

| Route | Default Model | Purpose |
|---|---|---|
| RAG / 2nd Brain | Qwen3 8b | Document Q&A, notes, URLs |
| BI Dashboard | Qwen3 8b | Data analysis, SQL, charts |
| Memory | Llama 3 | Conversation history questions |
| General | Mistral | Open chat and general questions |
| Router | Qwen3 8b | Classifies each query and attaches the response model |

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

# 3. Start everything
docker compose up --build

# 4. Open the app
open http://localhost:3000

# 5. Ingest PDFs (optional)
python scripts/ingest_pdfs.py data/raw/
```

## API Endpoints

If `API_AUTH_TOKEN` is set, send it as the `X-API-Token` header on every `/api/*` request.

| Method | Path | Description |
|---|---|---|
| POST | `/api/chat` | Auto-routed chat |
| POST | `/api/rag/ask` | RAG question |
| POST | `/api/rag/upload/pdf` | Upload PDF |
| POST | `/api/rag/upload/url` | Ingest URL |
| POST | `/api/rag/upload/text` | Save note |
| POST | `/api/bi/upload` | Upload CSV/Excel |
| POST | `/api/bi/ask` | BI question |
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
|-- services/           memory (redis), analytics
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
- The frontend includes Brain, BI Dashboard, Analytics, and Status views.

## Tests

```bash
python -m unittest discover -s tests
```
