# AI Platform

A full-stack AI platform that supports both local-first Ollama inference and cloud deployment through Render, Netlify, Gemini, and OpenRouter.

The platform includes document question answering, business intelligence, persistent memory, AI-assisted workflows, career/job tools, authentication, analytics, CI checks, and multi-model chat.

Live demo:

```text
https://symphonious-cat-2873fe.netlify.app/auth?next=%2Fchat
```

---

## Why I Built This

Most AI demos stop at a simple chatbot. I wanted to build something closer to a practical AI product: a system that can ingest documents and URLs, route queries to the right workflow, analyse datasets using natural language, remember conversations, support authentication, expose useful APIs, run automated checks, and work both locally and in the cloud.

This project helped me explore how AI applications are built beyond prompting alone, including retrieval, routing, backend APIs, frontend workflows, validation, security, observability, Docker deployment, cloud deployment, CI, and practical user experience.

---

## Live Demo

The platform is deployed with:

- Frontend: Netlify
- Backend API: Render
- Runtime: Cloud LLM mode using Gemini/OpenRouter
- Authentication: Email/password login with optional email verification

Live frontend:

```text
https://symphonious-cat-2873fe.netlify.app/auth?next=%2Fchat
```

The deployed version uses a hosted frontend connected to a Render backend. Local development still supports Ollama-based local model inference.

---

## Key Features

### AI Chat and Model Routing

- Auto-routed chat interface for general, RAG, BI, memory, and workflow-related queries
- Query router that classifies user requests and selects the appropriate agent
- Configurable model mapping for different tasks
- Local model execution through Ollama
- Cloud model execution through Gemini/OpenRouter
- Streaming responses for chat-style interaction

### Document RAG / Second Brain

- Upload and ingest PDFs
- Ingest public URLs and plain text notes
- Chunk documents and generate embeddings
- Store vectors in FAISS with JSON metadata
- Retrieve relevant chunks for grounded answers
- Document library with previews, chunk counts, and delete functionality
- Optional OCR support can be added with OCRmyPDF/Tesseract for scanned PDFs

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
- Redis-backed memory/cache layer for Docker/local runtime

### Career Copilot

- Analyse CV fit against job descriptions
- Tailor CV content for a specific role
- Generate cover letters
- Generate application packs
- Save job descriptions
- Import job descriptions from URLs
- Search for jobs based on CV/profile data
- Score job matches
- Track application status
- Stream long-running job search progress
- Generate match packs for strong job matches

### Authentication

- Email/password signup and login
- Email verification flow
- Resend verification email support
- Bearer-token authenticated sessions
- Optional API token protection using `X-API-Token`
- Auth-required mode for deployed usage

### Analytics, Logging, and Monitoring

- Record query events with session ID, query text, selected agent, selected model, latency, success/failure status, error messages, and timestamp
- Analytics dashboard showing total queries, success rate, average latency, p95 latency, recent queries, model usage, and agent usage
- Health endpoint for checking backend/model availability
- Background job status endpoint

### CI and Quality Checks

- GitHub Actions CI for automated project checks
- Backend test workflow support
- Frontend type-check/build workflow support
- Safer iteration through repeatable validation before deployment

### Security and Reliability Controls

- Optional API authentication using `X-API-Token`
- Optional login-required mode
- Bearer-token session authentication
- Email verification support
- CORS configuration through environment variables
- File upload extension validation
- PDF, CSV, and Excel validation
- SSRF-resistant URL ingestion
- Local/private/reserved IP blocking for URL ingestion
- URL response size limits
- Restricted content types for URL ingestion
- Docker Compose setup for repeatable local deployment
- Local/cloud runtime separation

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
- BeautifulSoup
- Requests
- Ollama
- Gemini API
- OpenRouter API
- Resend email API

### Frontend

- Next.js
- React
- TypeScript
- Tailwind CSS
- Recharts
- Lucide React
- Axios

### Infrastructure and Tooling

- Docker
- Docker Compose
- Git
- GitHub Actions
- Linux/WSL
- Netlify
- Render
- Cloudflare Tunnel for optional demo access
- Local Ollama model hosting
- Hosted cloud LLM providers

---

## Architecture Overview

```text
User
 |
 |-- Netlify / Local Next.js Frontend
 |     |-- Chat
 |     |-- Brain / Document Library
 |     |-- BI Dashboard
 |     |-- Memory
 |     |-- Career Copilot
 |     |-- Analytics
 |     |-- Settings
 |     |-- Auth
 |
 |-- Render / Local Flask API
       |-- Auth Routes
       |-- Query Router
       |-- RAG Agent
       |-- BI Agent
       |-- Memory Service
       |-- Career Services
       |-- Analytics Service
       |-- Background Jobs
       |-- Health Routes
       |
       |-- FAISS Vector Store
       |-- SQLite App Store
       |-- Redis Memory Layer
       |-- Ollama Local Models
       |-- Gemini/OpenRouter Cloud Models
```

---

## Agents and Routing

| Route | Local Default Model | Purpose |
|---|---|---|
| RAG / Second Brain | Qwen3 8B | Document Q&A, notes, URLs |
| BI Dashboard | Qwen3 8B | Dataset analysis, SQL, charts |
| Memory | Llama 3 | Conversation history and saved facts |
| General | Mistral | General-purpose chat |
| Router | Qwen3 8B | Query classification and model selection |

In cloud mode, task models can be mapped to Gemini/OpenRouter model IDs.

Example cloud model IDs shown to the app:

```text
gemini:gemini-2.0-flash
openrouter:openrouter/free
```

---

## API Endpoints

If `API_AUTH_TOKEN` is set, clients must send it using the `X-API-Token` header.

If `AUTH_REQUIRED=true`, protected routes require a Bearer token from the login flow.

### Auth

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/auth/signup` | Create account |
| POST | `/api/auth/login` | Login and receive session token |
| GET | `/api/auth/verify` | Verify email token |
| POST | `/api/auth/resend-verification` | Resend verification email |
| GET | `/api/auth/me` | Read current authenticated user |
| POST | `/api/auth/logout` | Logout current session |

### Chat

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/chat` | Auto-routed chat |
| POST | `/api/chat/stream` | Streaming routed chat |
| POST | `/api/chat/general` | General chat |
| POST | `/api/chat/general/stream` | Streaming general chat |
| GET | `/api/chat/conversations` | List saved conversations |
| POST | `/api/chat/conversations` | Create conversation |
| GET | `/api/chat/conversations/<id>` | Read conversation |
| PUT | `/api/chat/conversations/<id>` | Save conversation messages |
| DELETE | `/api/chat/conversations/<id>` | Delete conversation |

### RAG / Second Brain

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/rag/ask` | Ask a RAG question |
| POST | `/api/rag/ask/stream` | Streaming RAG answer |
| POST | `/api/rag/upload/pdf` | Upload and ingest PDF |
| POST | `/api/rag/upload/pdf/async` | Background PDF ingestion |
| POST | `/api/rag/upload/url` | Ingest public URL |
| POST | `/api/rag/upload/text` | Save and ingest text note |
| GET | `/api/rag/stats` | RAG index stats |
| GET | `/api/rag/documents` | Document library |
| GET | `/api/rag/documents/<source>` | Document preview |
| DELETE | `/api/rag/documents/<source>` | Delete document chunks |

### Business Intelligence

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/bi/upload` | Upload CSV/Excel dataset |
| GET | `/api/bi/datasets` | List loaded datasets |
| GET | `/api/bi/datasets/<name>/sample` | Dataset sample |
| POST | `/api/bi/ask` | Ask dataset question |

### Career Copilot

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/career/analyze` | Analyse CV-job fit |
| POST | `/api/career/tailor` | Tailor CV for a role |
| POST | `/api/career/cover-letter` | Draft cover letter |
| POST | `/api/career/pack` | Generate application pack |
| GET | `/api/career/preferences` | Get job preferences |
| PUT | `/api/career/preferences` | Save job preferences |
| GET | `/api/career/profile` | Get saved CV profile |
| PUT | `/api/career/profile` | Save CV profile |
| GET | `/api/career/jobs` | List saved jobs |
| POST | `/api/career/jobs` | Save job manually |
| POST | `/api/career/jobs/import-url` | Import job from URL |
| POST | `/api/career/jobs/search` | Search matching jobs |
| POST | `/api/career/jobs/search/stream` | Stream job search |
| POST | `/api/career/jobs/<id>/score` | Score saved job |
| POST | `/api/career/jobs/<id>/pack` | Generate match pack |
| POST | `/api/career/jobs/score-batches` | Create scoring batch |
| GET | `/api/career/jobs/score-batches/current` | Get current scoring batch |
| GET | `/api/career/jobs/score-batches/<id>` | Get scoring batch status |
| POST | `/api/career/jobs/score-batches/<id>/cancel` | Cancel scoring batch |
| PUT | `/api/career/jobs/<id>/status` | Update job status |
| DELETE | `/api/career/jobs/<id>` | Delete saved job |

### Jobs, Health, Analytics, Settings

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/jobs/<job_id>` | Read background job status |
| GET | `/api/health` | Health check |
| POST | `/api/health/warmup` | Warm up selected model |
| GET | `/api/analytics/summary` | Usage and latency summary |
| GET | `/api/analytics/recent` | Recent query events |
| GET | `/api/settings/models` | Current model settings |
| PUT | `/api/settings/models` | Update model settings |
| DELETE | `/api/settings/models` | Reset model settings |

---

## Project Structure

```text
ai-platform/
|-- .github/workflows/      GitHub Actions CI workflows
|-- agents/                 RAG, BI, general and critic agents
|-- application/            ingestion, chunking and retrieval logic
|-- apps/api/               Flask API routes and app entrypoint
|-- core/                   config, constants, prompts and schemas
|-- data/                   raw files, processed data and indexes
|-- domain/                 RAG, BI and routing pipelines
|-- frontend/nextjs-app/    Next.js frontend
|-- infrastructure/         embeddings, LLM client and FAISS store
|-- services/               auth, memory, analytics, storage, career and app services
|-- scripts/                ingestion and utility scripts
|-- tests/                  validation and smoke tests
|-- docker-compose.yml
|-- Dockerfile
|-- requirements.txt
|-- .env.example
`-- README.md
```

---

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/dazai2001-cmd/ai-platform.git
cd ai-platform
```

### 2. Install and Start Ollama

Install Ollama from the official Ollama website, then pull the required local models:

```bash
ollama pull qwen3:8b
ollama pull llama3
ollama pull mistral
ollama pull nomic-embed-text
```

### 3. Create the Environment File

```bash
cp .env.example .env
```

Optional API protection:

```env
API_AUTH_TOKEN=your_token_here
```

Optional login/auth mode:

```env
AUTH_REQUIRED=true
SECRET_KEY=change-me-in-production
```

### 4. Start the Platform

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

On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
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

## Local vs Cloud Runtime

This project supports two runtime modes.

---

### Local Mode

Local mode runs the backend, frontend, Redis, FAISS/SQLite storage, and Ollama-hosted models on your machine.

```env
AI_RUNTIME=local
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

Recommended local task model mapping:

```env
TASK_MODELS_JSON={"rag":"qwen3:8b","bi":"qwen3:8b","memory":"llama3:latest","general":"mistral:latest"}
ROUTER_MODEL=qwen3:8b
```

Run locally with:

```bash
docker compose up --build
```

---

### Cloud Mode

Cloud mode is designed for hosted deployment. The current deployment uses:

- Netlify for the Next.js frontend
- Render for the Flask API backend
- Gemini/OpenRouter for hosted LLM inference
- Resend for optional email verification

Backend environment variables for Render:

```env
AI_RUNTIME=cloud
AUTH_REQUIRED=true
APP_PUBLIC_URL=https://symphonious-cat-2873fe.netlify.app
CORS_ORIGINS=https://symphonious-cat-2873fe.netlify.app

GEMINI_API_KEY=your_gemini_key
GEMINI_MODELS=gemini-2.0-flash

OPENROUTER_API_KEY=your_openrouter_key
OPENROUTER_MODELS=openrouter/free

RESEND_API_KEY=your_resend_key
EMAIL_FROM=AI Platform <verify@your-domain.com>
SEND_VERIFICATION_EMAILS=true

SECRET_KEY=change-me-in-production
```

Frontend environment variables for Netlify:

```env
NEXT_PUBLIC_API_URL=https://your-render-service.onrender.com
NEXT_PUBLIC_AUTH_REQUIRED=true
```

Live frontend:

```text
https://symphonious-cat-2873fe.netlify.app/auth?next=%2Fchat
```

---

## Environment Variables

### Core Runtime

```env
AI_RUNTIME=local
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_TIMEOUT_SECONDS=180
LLM_MAX_TOKENS=768
```

### Cloud LLMs

```env
GEMINI_API_KEY=
GEMINI_MODELS=gemini-2.0-flash

OPENROUTER_API_KEY=
OPENROUTER_MODELS=openrouter/free
```

### Model Routing

```env
TASK_MODELS_JSON={"rag":"qwen3:8b","bi":"qwen3:8b","memory":"llama3:latest","general":"mistral:latest"}
CLOUD_TASK_MODELS_JSON={}
ROUTER_MODEL=qwen3:8b
```

### Embeddings

```env
EMBED_MODEL=all-MiniLM-L6-v2
```

### Redis

```env
REDIS_URL=redis://redis:6379
```

### RAG Settings

```env
CHUNK_SIZE=500
CHUNK_OVERLAP=100
TOP_K=5
```

### Auth and App Settings

```env
DEBUG=false
PORT=5000
SECRET_KEY=change-me-in-production
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
APP_PUBLIC_URL=http://127.0.0.1:3000

AUTH_REQUIRED=false
AUTH_SESSION_DAYS=14
AUTH_VERIFICATION_HOURS=24

API_AUTH_TOKEN=
```

### Email Verification

```env
RESEND_API_KEY=
EMAIL_FROM=AI Platform <onboarding@resend.dev>
SEND_VERIFICATION_EMAILS=false
```

### Limits and Storage

```env
MAX_URL_INGEST_BYTES=5242880
BI_MANIFEST_PATH=data/processed/bi_datasets.json
```

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
npm run typecheck
```

Or:

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

## CI

This repository includes GitHub Actions CI to help validate the project before deployment.

The CI workflow can be used to run checks such as:

- backend tests
- frontend type checks
- frontend build checks
- basic project validation

This helps catch broken routes, failing tests, and frontend type errors before changes are merged or deployed.

---

## Security Notes

This project includes several safety controls, but it is still a portfolio/demo project rather than a production SaaS product.

Implemented controls:

- API token protection for `/api/*` routes
- optional login-required mode
- Bearer-token session authentication
- email verification support
- restricted CORS origins
- file extension validation
- file validation for uploaded documents/datasets
- URL ingestion restrictions
- local/private/reserved IP blocking
- redirect validation
- URL response size limits
- safe JSON metadata storage for FAISS
- SQL validation for BI queries
- cloud/local runtime separation

Recommended before production use:

- add proper role-based access control
- add rate limiting
- add request logging middleware
- add managed secret storage
- move from local SQLite/FAISS storage to managed storage where needed
- add production-grade observability
- add stronger RAG evaluation
- add automated frontend tests beyond type checks
- add backup/restore strategy for user data

---

## Known Limitations

- The main project was designed as a local-first platform, with cloud mode added for hosted demos.
- The public demo depends on free-tier Render/Netlify availability and may experience cold starts.
- Local Ollama models require local compute resources.
- RAG quality depends on document quality, chunking, embeddings, and retrieval relevance.
- BI SQL generation is validated but still depends on model output quality.
- Cloud mode depends on external provider limits, keys, and availability.
- Free OpenRouter/Gemini usage may be rate-limited.
- Cloudflare Tunnel demo access depends on the local machine being online if used.
- No Kubernetes or large-scale model-serving infrastructure is included.
- OCR for scanned PDFs may require additional system dependencies such as Tesseract/OCRmyPDF.

---

## Future Improvements

- Add full production cloud deployment option
- Add architecture diagram image
- Add demo video link
- Add screenshots of Chat, Brain, BI, Career and Analytics pages
- Add RAG evaluation scripts for retrieval quality and answer faithfulness
- Add latency benchmarking across models
- Add model/provider comparison
- Add user roles and permissions
- Add export options for BI reports, CV packs and RAG answers
- Add stronger frontend tests
- Add background worker queue for long-running jobs
- Add database migrations
- Add production monitoring and alerting

---

## What I Learned

This project helped me understand that building useful AI products involves much more than calling a language model. I learned how to combine retrieval, routing, APIs, persistent memory, dataset analysis, validation, authentication, frontend workflows, Docker deployment, cloud deployment, logging, monitoring, CI and security controls into one working system.

It also improved my ability to use AI-assisted development tools such as Codex and Claude Code to debug, refactor and iterate software while still manually reviewing, testing and understanding the code.

---

## Author

**Rahul Stanly Keecheril**

- GitHub: [dazai2001-cmd](https://github.com/dazai2001-cmd)
- LinkedIn: [Rahul Stanly Keecheril](https://www.linkedin.com/in/rahul-stanly-keecheril-77b455178/)
