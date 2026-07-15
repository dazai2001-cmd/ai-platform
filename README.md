# AI Platform

A full-stack AI platform that supports local-first Ollama inference and cloud deployment through Netlify, Render, Supabase PostgreSQL, Gemini, and OpenRouter.

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

The deployed version uses a same-origin Next.js proxy from Netlify to the Render backend so Secure HttpOnly authentication cookies remain reliable. Local development still supports Ollama-based inference and SQLite persistence.

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
- Supabase PostgreSQL persistence in cloud deployments, with SQLite retained for local development and tests
- Redis-backed memory/cache layer for Docker/local runtime

### Career Copilot

- Import PDF or Word (`.docx`) CVs into an editable profile, including OCR support for scanned PDFs
- Validate archive structure, decompressed size, compression ratio, file type, page count, and upload size before extraction
- Analyse CV fit against job descriptions
- Tailor CV content for a specific role
- Generate cover letters
- Generate application packs
- Save job descriptions
- Import job descriptions from URLs
- Search Adzuna, Reed, Remotive, and Arbeitnow based on CV/profile data
- Deduplicate listings and score job matches against the saved profile
- Separate strong 70+ matches and track found, saved, applied, and skipped jobs
- Stream long-running job search progress
- Generate match packs for strong job matches

### Frontend Experience

- Light, Valorant-inspired red/teal design system with consistent panels, typography, and states
- Responsive navigation and clearer loading, retry, offline/local-fallback, empty, and error feedback
- Workspace and General chat modes with tool shortcuts, conversation sync, cancellation, and stream timeouts
- Drag-and-drop CV import with extraction progress, metadata, editable text, and accessible controls

### Authentication

- Email/password signup and login
- Email verification flow
- Resend verification email support
- Secure HttpOnly cookie sessions with trusted-origin CSRF protection
- Optional API token protection using `X-API-Token`
- Auth-required mode for deployed usage
- Temporary testing notice that permits valid-looking made-up email addresses

### Analytics, Logging, and Monitoring

- Record privacy-conscious, tenant-scoped query events with configurable query-text retention
- Analytics dashboard showing total queries, success rate, average latency, p95 latency, recent queries, model usage, and agent usage
- Liveness and readiness endpoints for checking application dependencies
- Background job status endpoint

### CI and Quality Checks

- GitHub Actions CI for automated project checks
- Backend unit, integration, real-PostgreSQL, security, concurrency, and evaluation checks
- Frontend Vitest, type-check, and production-build checks
- GHCR multi-architecture image releases with provenance, SBOMs, and Trivy scanning
- Safer iteration through repeatable validation before deployment

### Security and Reliability Controls

- Optional API authentication using `X-API-Token`
- Login-required production mode with Secure HttpOnly cookie authentication
- Email verification support
- CORS configuration through environment variables
- File signature, size, archive, PDF, Word, CSV, and Excel validation
- SSRF-resistant URL ingestion
- Local/private/reserved IP blocking for URL ingestion
- URL response size limits
- Restricted content types for URL ingestion
- Request-size, tenant-storage, cloud-usage, and rate limits
- Startup configuration validation and security headers
- Hardened non-root production containers and private Redis networking
- Local/cloud runtime separation

---

## Tech Stack

### Backend

- Python
- Flask
- REST APIs
- Pandas
- PostgreSQL / Supabase
- SQLite for local development and the isolated BI query sandbox
- Redis
- FAISS
- Sentence Transformers
- PyMuPDF
- BeautifulSoup
- Requests
- psycopg / psycopg-pool
- python-docx
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
       |-- PostgreSQL App Store (cloud) / SQLite App Store (local)
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
| Career | Qwen3 8B | Job matching and application packs |
| Router | Qwen3 8B | Query classification and model selection |

In cloud mode, task models can be mapped to Gemini/OpenRouter model IDs.

Example cloud model IDs shown to the app:

```text
gemini:gemini-3.5-flash
openrouter:openrouter/free
```

---

## API Endpoints

If `API_AUTH_TOKEN` is set, clients must send it using the `X-API-Token` header.

If `AUTH_REQUIRED=true`, browser clients authenticate with the Secure HttpOnly session cookie created by the login flow. State-changing requests are restricted to trusted origins.

### Auth

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/auth/signup` | Create account |
| POST | `/api/auth/login` | Login and set the session cookie |
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
| DELETE | `/api/bi/datasets/<name>` | Delete dataset and release its quota |
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
| POST | `/api/career/profile/import` | Extract and save a PDF or Word CV |
| DELETE | `/api/career/profile` | Delete saved CV profile |
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

### Memory

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/memory` | List memory session IDs |
| GET | `/api/memory/<session_id>` | Read one session's messages |
| DELETE | `/api/memory/<session_id>` | Clear one session's messages |
| GET | `/api/memory/sessions` | List memory sessions |
| GET | `/api/memory/facts` | List saved facts |
| POST | `/api/memory/facts` | Add a saved fact |
| DELETE | `/api/memory/facts/<id>` | Delete a saved fact |

### Jobs, Health, Analytics, Settings

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/jobs/<job_id>` | Read background job status |
| GET | `/api/health` | Health check |
| GET | `/api/health/live` | Process liveness check |
| GET | `/api/health/ready` | Dependency readiness check |
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
|-- docs/                   operational and evaluation documentation
|-- domain/                 RAG, BI and routing pipelines
|-- evaluation/             deterministic RAG evaluation harness and fixtures
|-- frontend/nextjs-app/    Next.js frontend
|-- infrastructure/         embeddings, LLM client and FAISS store
|-- services/               auth, memory, analytics, storage, career and app services
|-- scripts/                ingestion and utility scripts
|-- tests/                  validation and smoke tests
|-- docker-compose.yml
|-- docker-compose.prod.yml hardened single-host production stack
|-- Dockerfile
|-- supabase/migrations/    versioned PostgreSQL schema migrations
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

## Production Container Deployment

The production stack is deliberately a single-host foundation: Gunicorn serves
the API, Next.js runs from its standalone production output, Redis is private to
an internal Docker network, and only the frontend is published to the host. Both
application containers run as non-root users with read-only root filesystems,
dropped Linux capabilities, bounded logs, health checks, and persistent named
volumes.

Create the production environment file and fill every required value:

```bash
cp .env.production.example .env.production
```

At minimum, set a random `SECRET_KEY`, the Supabase PostgreSQL `DATABASE_URL`,
an HTTPS `APP_PUBLIC_URL` and matching `CORS_ORIGINS`, `RESEND_API_KEY`, a
verified `EMAIL_FROM`, and either Gemini or OpenRouter credentials. Production
startup intentionally fails if PostgreSQL, authentication, secure cookies,
shared Redis rate limiting, HTTPS origins, privacy-conscious analytics, or
verification email delivery are not configured safely.
`API_AUTH_TOKEN` is optional defense in depth between the Next.js server and API;
when set, it is injected by the server-side proxy and is never exposed as a
`NEXT_PUBLIC_*` browser value.

Build and start the stack:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml build
docker compose --env-file .env.production -f docker-compose.prod.yml up -d
docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

The frontend defaults to `127.0.0.1:3000`. Put Caddy, nginx, a cloud load
balancer, or another TLS-terminating reverse proxy in front of that address.
Set `TRUST_PROXY_HOPS` only when you know the exact number of proxies that set
the forwarded client address. The example uses `1` for the documented TLS edge
proxy -> Next.js -> API topology. Configure that edge proxy to overwrite, rather
than append, client-supplied `X-Forwarded-For`; use `0` only when there is no
trusted forwarding proxy. The API and Redis do not publish host ports.

To consume images published by GitHub Actions instead of building locally, set:

```env
API_IMAGE=ghcr.io/<owner>/<repository>-api:sha-<commit>
FRONTEND_IMAGE=ghcr.io/<owner>/<repository>-frontend:sha-<commit>
```

Then run `docker compose --env-file .env.production -f docker-compose.prod.yml
pull` before `up -d`. Stopping with `docker compose ... down` preserves both
named volumes. PostgreSQL backups are managed in Supabase. Back up the
`app_data` volume before upgrades; it contains FAISS indexes, uploaded data, and
analytics. Do not scale the API beyond one replica until those remaining stores
and background jobs move to managed shared services.

### Supabase PostgreSQL persistence

Copy the pooled PostgreSQL connection URI from the Supabase project's Connect
panel into the backend's `DATABASE_URL`. Prefer the session-pooler URI on port
5432 for a persistent Render service that needs IPv4 connectivity. The URI is a backend secret: never
place it in Netlify or in a `NEXT_PUBLIC_*` variable. Production uses encrypted
connections and a small client pool by default:

```env
DATABASE_URL=postgresql://<pooled-user>:<password>@<pooler-host>:5432/postgres
DATABASE_SCHEMA=app_private
DATABASE_SSLMODE=require
DATABASE_POOL_MIN_SIZE=1
DATABASE_POOL_MAX_SIZE=5
```

On startup, the API obtains a PostgreSQL advisory lock and applies its versioned,
idempotent schema migrations. Application tables are created in `app_private`,
which is not exposed through Supabase's public Data API. The app continues to
use its own verified-email sessions; enabling this database does not switch it
to Supabase Auth. Existing SQLite rows are not copied automatically.

### Container release pipeline

The `Release container images` workflow runs the complete CI workflow before it
publishes anything. Pushes to `main`, semantic tags such as `v1.2.0`, and manual
runs produce GHCR API and frontend images for `linux/amd64` and `linux/arm64`.
Images first receive immutable full-commit `sha-*` tags. Only after both images
publish successfully are the matching semantic-version or `latest` tags promoted,
which keeps mutable releases paired and leaves a reliable rollback reference.
BuildKit cache, OCI provenance, an SBOM, and Trivy vulnerability reports are
included; fixable critical vulnerabilities stop publication.

No registry secret is required for GHCR in the same repository: the workflow
uses the automatically provided `GITHUB_TOKEN` with `packages: write`. Repository
settings must allow Actions to create packages and upload code-scanning results.
Runtime provider, email, and application secrets belong on the deployment host
or in its secret manager, never in image build arguments.

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
- Supabase PostgreSQL for shared relational application state
- Gemini/OpenRouter for hosted LLM inference
- Resend for production email verification

Backend environment variables for Render:

```env
AI_RUNTIME=cloud
AUTH_REQUIRED=true
APP_PUBLIC_URL=https://symphonious-cat-2873fe.netlify.app
CORS_ORIGINS=https://symphonious-cat-2873fe.netlify.app

DATABASE_URL=postgresql://<pooled-user>:<password>@<pooler-host>:5432/postgres
DATABASE_SCHEMA=app_private
DATABASE_SSLMODE=require

GEMINI_API_KEY=your_gemini_key
GEMINI_MODELS=gemini-3.5-flash

OPENROUTER_API_KEY=your_openrouter_key
OPENROUTER_MODELS=openrouter/free

RESEND_API_KEY=your_resend_key
EMAIL_FROM=AI Platform <verify@your-domain.com>
SEND_VERIFICATION_EMAILS=true

SECRET_KEY=change-me-in-production
RATE_LIMIT_STORAGE_URI=redis://your-managed-redis-url
TRUST_PROXY_HOPS=1
```

Use Supabase's IPv4-compatible session pooler on port `5432` for a persistent
Render backend. Application tables live in the private `app_private` schema and
the app continues to use its own authentication rather than Supabase Auth.
Existing SQLite users, chats, CVs, and jobs are intentionally not copied.

Frontend environment variables for Netlify:

```env
NEXT_PUBLIC_API_URL=
NEXT_PUBLIC_AUTH_REQUIRED=true
API_INTERNAL_URL=https://your-render-service.onrender.com
API_AUTH_TOKEN=<same-server-boundary-token-as-render>
```

Keep `NEXT_PUBLIC_API_URL` empty in the hosted frontend. Browser requests stay
same-origin and the Next.js server-only proxy forwards them to `API_INTERNAL_URL`;
this is required for the Secure HttpOnly session cookie and keeps the optional
API boundary token out of browser JavaScript.

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
GEMINI_MODELS=gemini-3.5-flash

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
EMBEDDING_PROVIDER=local
EMBED_MODEL=all-MiniLM-L6-v2
GEMINI_EMBED_MODEL=gemini-embedding-2
EMBED_DIM=384
EMBED_BATCH_SIZE=32
```

Use `EMBEDDING_PROVIDER=gemini` on memory-constrained cloud services. This
keeps SentenceTransformers/PyTorch local while the hosted API uses the Gemini
embedding endpoint with the same API key as chat.

### Redis

```env
REDIS_URL=redis://redis:6379
```

### Application Database

Leave `DATABASE_URL` blank for local SQLite. Production requires an encrypted
Supabase/PostgreSQL connection:

```env
DATABASE_URL=
DATABASE_SCHEMA=app_private
DATABASE_SSLMODE=require
DATABASE_CONNECT_TIMEOUT_SECONDS=10
DATABASE_POOL_MIN_SIZE=1
DATABASE_POOL_MAX_SIZE=5
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
AUTH_COOKIE_NAME=ai_platform_session
AUTH_COOKIE_SECURE=false
AUTH_COOKIE_SAMESITE=Lax

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
MAX_UPLOAD_BYTES=52428800
MAX_CV_UPLOAD_BYTES=10485760
MAX_DATASET_UPLOAD_BYTES=26214400
MAX_DOCUMENTS_PER_USER=100
BI_MANIFEST_PATH=data/processed/bi_datasets.json
RATE_LIMIT_ENABLED=true
RATE_LIMIT_STORAGE_URI=memory://
```

Production should use shared Redis for rate limiting, secure cookies, an HTTPS
`APP_PUBLIC_URL`, explicit `CORS_ORIGINS`, and privacy-conscious analytics. See
`.env.production.example` for the complete hardened configuration.

---

## Testing

Run backend tests:

```bash
python -m pytest -q
```

Real PostgreSQL integration tests run when `TEST_POSTGRES_URL` is provided; CI
starts an isolated PostgreSQL service automatically.

Run the deterministic RAG evaluation:

```bash
python scripts/evaluate_rag.py --mode fixture
```

Validate environment configuration without printing secrets:

```bash
python scripts/check_config.py
```

Run frontend tests, type checks, and a production build:

```bash
cd frontend/nextjs-app
npm test
npm run typecheck
npm run build
```

Current test coverage includes:

- API integration and authentication boundaries
- PostgreSQL/SQLite compatibility and versioned migrations
- PDF/DOCX CV extraction, OCR metadata, and malicious archive rejection
- request, rate, upload, dataset, and tenant-storage limits
- hardened BI SQL execution and bounded result handling
- concurrent RAG persistence and deterministic retrieval evaluation
- privacy-conscious analytics and per-user isolation
- chat loading, retries, streaming timeouts, API proxying, CV import, and auth UI behavior

---

## CI

This repository includes GitHub Actions CI to help validate the project before deployment.

The CI workflow runs:

- backend tests plus real PostgreSQL integration coverage
- frontend Vitest, type checks, and production builds on Node 22
- configuration and production-readiness validation

This helps catch broken routes, failing tests, and frontend type errors before changes are merged or deployed.

After CI succeeds, the release workflow can publish paired API/frontend images
for `linux/amd64` and `linux/arm64` to GHCR. Immutable commit tags, OCI
provenance, SBOMs, and Trivy vulnerability reports support repeatable rollback
and supply-chain review; fixable critical vulnerabilities stop publication.

---

## Security Notes

This project includes several safety controls, but it is still a portfolio/demo project rather than a production SaaS product.

Implemented controls:

- optional server-to-server API boundary token for `/api/*` routes
- login-required production mode with Secure HttpOnly cookies and trusted-origin CSRF checks
- production email verification without exposing verification secrets to browsers
- explicit CORS origins, security headers, and proxy-hop validation
- file signature, archive, decompression, page-count, upload-size, and tenant-quota checks
- SSRF-resistant URL ingestion with redirect, content-type, IP-range, and response-size validation
- per-route rate limits backed by shared Redis in production
- tenant-scoped chat, memory, career, settings, RAG, and privacy-conscious analytics data
- read-only BI SQL validation, timeouts, input limits, and bounded results
- startup guards for secrets, TLS database connections, authentication, provider allow-lists, and production privacy settings
- non-root, read-only production containers with dropped capabilities and private Redis networking

Recommended before production use:

- add role-based permissions beyond the current user boundary
- store deployment secrets in a managed secret manager
- move FAISS indexes, uploaded data, analytics files, and background jobs to managed shared services before horizontal scaling
- add production-grade tracing, monitoring, and alerting
- use a dedicated least-privilege PostgreSQL role and decide on defense-in-depth RLS policies
- regularly test Supabase and `app_data` backup/restore procedures

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
- FAISS indexes, uploaded files, analytics files, and in-process background jobs remain local to one API host, so the API should stay at one replica until they move to shared services.
- The live Supabase schema starts clean; existing local SQLite accounts and application records are not migrated automatically.

---

## Future Improvements

- Add architecture diagram image
- Add demo video link
- Add screenshots of Chat, Brain, BI, Career and Analytics pages
- Add latency benchmarking across models
- Add model/provider comparison
- Add user roles and permissions
- Add export options for BI reports, CV packs and RAG answers
- Move vector/file storage to managed shared services
- Add a durable background worker queue for long-running jobs
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
