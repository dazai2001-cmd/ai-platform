# Local-First AI Platform

A full-stack local-first AI platform for document question answering, business intelligence, persistent memory, AI-assisted workflows, and multi-model chat.

The platform combines a Flask backend, Next.js frontend, Ollama-hosted local language models, FAISS semantic search, Redis/SQLite persistence, Docker-based deployment, logging, analytics, and security-focused API controls.

---

## Why I Built This

Most AI demos stop at a simple chatbot. I wanted to build something closer to a practical AI product: a system that can ingest documents and URLs, route queries to the right workflow, analyse datasets using natural language, remember conversations, expose useful APIs, and provide enough reliability controls to be demonstrated and tested properly.

This project helped me explore how AI applications are built beyond prompting alone, including retrieval, routing, backend APIs, frontend workflows, validation, security, observability, Docker deployment, and practical user experience.

---

## Key Features

### AI Chat and Model Routing

- Auto-routed chat interface for general, RAG, BI, and memory-related queries
- Query router that classifies user requests and selects the appropriate agent
- Configurable model mapping for different tasks
- Local model execution through Ollama
- Streaming responses for chat-style interaction

### Document RAG / Second Brain

- Upload and ingest PDFs
- Ingest public URLs and plain text notes
- Chunk documents and generate embeddings
- Store vectors in FAISS with JSON metadata
- Retrieve relevant chunks for grounded answers
- OCR fallback for scanned or image-only PDFs using OCRmyPDF and Tesseract
- Document library with previews, chunk counts, and delete functionality

### Natural-Language Business Intelligence

- Upload CSV and Excel datasets
- Ask business questions in natural language
- Generate SQL-style analysis queries
- Validate generated SQL to allow only safe single-statement SELECT queries
- Block dangerous SQL keywords such as DROP, DELETE, INSERT, UPDATE, and ALTER
- Execute queries against uploaded datasets
- Return rows and chart-ready results for visualisation

### Persistent Memory

- Store conversation messages by session
- Persist user facts and memory entries
- Retrieve previous conversation history for context-aware responses
- SQLite-backed persistence for chat, memory, settings, and app state

### Analytics, Logging, and Monitoring

- Record query events with session ID, query text, selected agent, selected model, latency, success/failure status, error messages, and timestamp
- Analytics dashboard showing total queries, success rate, average latency, p95 latency, recent queries, model usage, and agent usage
- Health endpoint for checking model/backend availability

### Security and Reliability Controls

- Optional API authentication using X-API-Token
- CORS configuration through environment variables
- File upload extension validation
- PDF, CSV, and Excel magic-byte validation
- SSRF-resistant URL ingestion
- Local/private/reserved IP blocking for URL ingestion
- URL response size limits
- Restricted content types for URL ingestion
- Docker Compose setup for repeatable local deployment

---

## Tech Stack

### Backend

- Python
- Flask
- REST APIs
- Pandas
- pandasql
- SQLite
- Redis
- FAISS
- Sentence Transformers
- PyMuPDF
- OCRmyPDF
- Tesseract
- Ollama

### Frontend

- Next.js
- React
- TypeScript
- Tailwind CSS
- Recharts
- Lucide React

### Infrastructure and Tooling

- Docker
- Docker Compose
- Git
- Linux/WSL
- Cloudflare Tunnel for public demo access
- Local Ollama model hosting

---

## Architecture Overview

```text
User
 |
 |-- Next.js Frontend
 |     |-- Chat
 |     |-- Brain / Document Library
 |     |-- BI Dashboard
 |     |-- Memory
 |     |-- Analytics
 |     |-- Settings
 |
 |-- Flask API
       |-- Query Router
       |-- RAG Agent
       |-- BI Agent
       |-- Memory Service
       |-- Analytics Service
       |-- Health Routes
       |
       |-- FAISS Vector Store
       |-- SQLite App Store
       |-- Redis Memory Layer
       |-- Ollama Local Models
```

---

## Agents and Routing

| Route | Default Model | Purpose |
|---|---|---|
| RAG / Second Brain | Qwen3 8B | Document Q&A, notes, URLs |
| BI Dashboard | Qwen3 8B | Dataset analysis, SQL, charts |
| Memory | Llama 3 | Conversation history and saved facts |
| General | Mistral | General-purpose chat |
| Router | Qwen3 8B | Query classification and model selection |

The router classifies incoming queries and attaches the correct task model. This keeps model selection centralised rather than scattered across individual agents.

---

## API Endpoints

If API_AUTH_TOKEN is set, clients must send it using the X-API-Token header.

| Method | Endpoint | Description |
|---|---|---|
| POST | /api/chat | Auto-routed chat |
| POST | /api/chat/stream | Streaming routed chat |
| POST | /api/chat/general | General chat |
| POST | /api/chat/general/stream | Streaming general chat |
| GET | /api/chat/conversations | List saved conversations |
| POST | /api/chat/conversations | Create conversation |
| GET | /api/chat/conversations/<id> | Read conversation |
| PUT | /api/chat/conversations/<id> | Save conversation messages |
| DELETE | /api/chat/conversations/<id> | Delete conversation |
| POST | /api/rag/ask | Ask a RAG question |
| POST | /api/rag/ask/stream | Streaming RAG answer |
| POST | /api/rag/upload/pdf | Upload and ingest PDF |
| POST | /api/rag/upload/pdf/async | Background PDF ingestion |
| POST | /api/rag/upload/url | Ingest public URL |
| POST | /api/rag/upload/text | Save and ingest text note |
| GET | /api/rag/stats | RAG index stats |
| GET | /api/rag/documents | Document library |
| GET | /api/rag/documents/<source> | Document preview |
| DELETE | /api/rag/documents/<source> | Delete document chunks |
| POST | /api/bi/upload | Upload CSV/Excel dataset |
| GET | /api/bi/datasets | List loaded datasets |
| GET | /api/bi/datasets/<name>/sample | Dataset sample |
| POST | /api/bi/ask | Ask dataset question |
| GET | /api/health | Health check |
| POST | /api/health/warmup | Warm up selected model |
| GET | /api/analytics/summary | Usage and latency summary |
| GET | /api/analytics/recent | Recent query events |
| GET | /api/settings/models | Current model settings |
| PUT | /api/settings/models | Update model settings |
| DELETE | /api/settings/models | Reset model settings |

---

## Project Structure

```text
ai-platform/
|-- agents/                 RAG, BI, general and critic agents
|-- application/            ingestion, chunking and retrieval logic
|-- apps/api/               Flask API routes and app entrypoint
|-- core/                   config, constants, prompts and schemas
|-- data/                   raw files, processed data and indexes
|-- domain/                 RAG, BI and routing pipelines
|-- frontend/nextjs-app/    Next.js frontend
|-- infrastructure/         embeddings, LLM client and FAISS store
|-- services/               memory, analytics, storage and app services
|-- scripts/                ingestion and utility scripts
|-- tests/                  validation and smoke tests
|-- docker-compose.yml
|-- Dockerfile
|-- requirements.txt
`-- README.md
```

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/dazai2001-cmd/ai-platform.git
cd ai-platform
```

### 2. Install and start Ollama

Install Ollama from the official Ollama website, then pull the required models:

```bash
ollama pull qwen3:8b
ollama pull llama3
ollama pull mistral
ollama pull nomic-embed-text
```

### 3. Create the environment file

```bash
cp .env.example .env
```

Optional API protection:

```env
API_AUTH_TOKEN=your_token_here
```

### 4. Start the platform

```bash
docker compose up --build
```

Open the frontend:

```text
http://localhost:3000
```

The backend runs on:

```text
http://localhost:5000
```

---

## Running Without Docker

### Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m apps.api.main
```

### Frontend

```bash
cd frontend/nextjs-app
npm install
npm run dev
```

---

## Demo Access

The platform is designed to run locally with Docker and Ollama. For demonstration purposes, it can be exposed publicly using Cloudflare Tunnel while keeping the AI backend and model inference local.

Example wording:

```text
Frontend/API exposed through Cloudflare Tunnel for demo access.
LLM inference remains local through Ollama.
```

This is useful for external testing without requiring full cloud deployment of the model backend.

---

## Testing

Run backend tests:

```bash
python -m unittest discover -s tests
```

If using pytest:

```bash
python -m pytest -q
```

Run frontend TypeScript checks:

```bash
cd frontend/nextjs-app
npx tsc --noEmit
```

Current test coverage includes:

- fake PDF upload rejection
- localhost/private IP URL rejection
- SQL validation
- chart generation
- BI response cleanup
- model routing
- API routing boundaries

---

## Security Notes

This project includes several safety controls, but it is still a portfolio/demo project rather than a production SaaS product.

Implemented controls:

- API token protection for /api/* routes
- restricted CORS origins
- file extension validation
- file magic-byte validation
- URL ingestion restrictions
- local/private/reserved IP blocking
- redirect validation
- URL response size limits
- safe JSON metadata storage for FAISS
- SQL validation for BI queries

Recommended before production use:

- add proper user authentication and roles
- add rate limiting
- add request logging middleware
- add secret management
- move from local storage to managed storage
- add CI/CD pipeline
- add cloud deployment
- add observability with external monitoring
- add stronger RAG evaluation
- add automated frontend tests

---

## Known Limitations

- The main deployment is local-first rather than fully cloud-hosted.
- Ollama models require local compute resources.
- RAG quality depends on document quality, chunking and retrieval relevance.
- BI SQL generation is validated but still depends on model output quality.
- Cloudflare Tunnel demo access depends on the local machine being online.
- No full GitHub Actions CI pipeline is currently included on the main branch.
- No Kubernetes or large-scale model-serving infrastructure is included.

---

## Future Improvements

- Add GitHub Actions CI for backend tests and frontend build checks
- Add full cloud deployment option
- Add architecture diagram image
- Add demo video link
- Add screenshots of Chat, Brain, BI and Analytics pages
- Add RAG evaluation scripts for retrieval quality and answer faithfulness
- Add latency benchmarking across models
- Add model/provider comparison
- Add user accounts and role-based access
- Add export options for BI reports and RAG answers

---

## What I Learned

This project helped me understand that building useful AI products involves much more than calling a language model. I learned how to combine retrieval, routing, APIs, persistent memory, dataset analysis, validation, frontend workflows, Docker deployment, logging, monitoring and security controls into one working system.

It also improved my ability to use AI-assisted development tools such as Codex and Claude Code to debug, refactor and iterate software while still manually reviewing, testing and understanding the code.

---

## Author

**Rahul Stanly Keecheril**

- GitHub: [dazai2001-cmd](https://github.com/dazai2001-cmd)
- LinkedIn: [Rahul Stanly Keecheril](https://www.linkedin.com/in/rahul-stanly-keecheril-77b455178/)
## Cloud deployment notes

For local development, keep `AI_RUNTIME=local` and use Ollama models such as
`qwen3:8b`, `llama3:latest`, and `mistral:latest`.

For Render or another hosted backend, set `AI_RUNTIME=cloud`. In cloud mode the
API hides Ollama models and routes LLM calls to Gemini/OpenRouter model IDs:

```env
AI_RUNTIME=cloud
AUTH_REQUIRED=true
APP_PUBLIC_URL=https://symphonious-cat-2873fe.netlify.app
CORS_ORIGINS=https://symphonious-cat-2873fe.netlify.app

GEMINI_API_KEY=...
GEMINI_MODELS=gemini-2.0-flash

OPENROUTER_API_KEY=...
OPENROUTER_MODELS=google/gemini-2.0-flash-exp:free

RESEND_API_KEY=...
EMAIL_FROM=AI Platform <verify@your-domain.com>
SEND_VERIFICATION_EMAILS=true
```

Model IDs shown to the app are provider-prefixed:

```text
gemini:gemini-2.0-flash
openrouter:google/gemini-2.0-flash-exp:free
```

Frontend-only variables belong in Netlify:

```env
NEXT_PUBLIC_API_URL=https://your-render-service.onrender.com
NEXT_PUBLIC_AUTH_REQUIRED=true
```

If `SEND_VERIFICATION_EMAILS=true` and `RESEND_API_KEY` is configured, signup
and resend-verification requests send real email links through Resend. Local
development defaults to showing the verification link on the auth page so the
flow can be tested without an email provider.
