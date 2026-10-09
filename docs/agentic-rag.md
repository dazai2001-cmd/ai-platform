# Local agentic RAG

Local Qwen document Q&A now uses LangChain typed tools inside a LangGraph
retrieval loop. The existing RAG agent still owns authentication context,
conversation memory, analytics, and answer streaming. The final answer uses
the existing provider client and RAG grounding prompt.

This runs through the current Brain endpoints and the workspace's RAG action.
Restart the backend after installing the updated requirements.

## Flow

1. Read recent conversation history for the authenticated user and session.
2. Check short unresolved references before querying documents. Qwen can also
   ask one clarification question before search. Answer in the same session.
3. Qwen calls a typed search tool with a self-contained query. The application
   supplies the user ID; the model cannot choose a different document owner.
4. Qwen inspects excerpts and may search again. A retry expands the candidate
   pool and prefers unseen passages, retaining the earlier evidence for links
   across documents.
5. Validate Qwen's supporting source IDs against owned, retrieved excerpts.
   Explicit person-name requests also require a literal name in the selected
   evidence, preventing the tested role-only answer from ending the search.
6. Generate or stream a grounded answer with exact source citations. Return a
   clarification or abstention directly when the answer is not supported.

Questions about prior conversation can use recent user history as evidence
without document citations. History is not independent evidence for new
document facts.

## Configuration

Local Qwen RAG enables the loop by default:

```dotenv
RAG_AGENTIC_ENABLED=true
RAG_AGENTIC_MODELS=qwen3:8b
RAG_AGENTIC_MAX_SEARCHES=2
RAG_AGENTIC_MAX_MODEL_CALLS=4
RAG_AGENTIC_CONTEXT_TOKENS=4096
```

The call budget counts retrieval planning, followed by at most one answer
generation call. It applies per request. Setting RAG_AGENTIC_ENABLED=false
restores the existing single-search pipeline. Cloud runtime and Gemini /
OpenRouter models use that pipeline, keeping their current inference cost.
Other local models also keep that pipeline unless added to the comma-separated
RAG_AGENTIC_MODELS list after verifying their native tool support.

The planner checks an approximate input token budget including tool schemas.
It uses the last four history messages, capped at 500 characters each, and
up to 3,000 characters per search response. Answer context is capped at 4,000
characters; answer history uses four messages capped at 1,000 characters each.
This implementation does not summarize long conversations automatically.

GPU offloading follows the existing OLLAMA_NUM_GPU setting: 0 uses CPU;
-1 lets Ollama choose. The smoke test respects that setting.

Graph state exists for one request. Clarification turns persist through the
existing user/session memory service; the next request rebuilds the graph
from that history. The standalone experiment's SQLite graph checkpoints
are separate from this RAG integration. LangSmith tracing is disabled for
the local retrieval graph.

The person-name check is conservative and expects a literal name with a
capital letter in the excerpt. Lowercase-only names can lead to abstention.
The live examples are a small smoke test, not a benchmark over a large corpus.

## Run tests

Install the updated requirements in the project virtual environment. The new
framework versions are pinned in the root requirements file.

Offline graph and evaluator checks:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agentic_rag.py tests/test_rag_evaluation.py -q
```

Real Qwen, cached all-MiniLM-L6-v2 embeddings, and a separate FAISS fixture index:

```powershell
ollama serve
.\.venv\Scripts\python.exe scripts/test_agentic_rag.py
```

Use an already running Ollama server when available. The test forbids model
downloads and cloud embedding calls. It creates synthetic documents under
data/cache/agentic_rag and leaves the user's existing index intact. With
TOP_K=1, a follow-up must find a name by following a role across two documents.
Other cases cover a cited fact, an unsupported question, an unresolved
reference, and the answer stream. An extra document belongs to another user
and must never appear in results.

The machine-readable report is saved to
data/cache/agentic_rag/live-report.json. The script exits nonzero on failure.
Use --embedding-provider hashing only for a separate fallback test; the
default smoke test exercises the real cached semantic embedding model.

Non-streaming RAG responses retain answer, sources, and model, and add
needs_clarification, retrieved_contexts, and agentic retrieval metadata. The
streaming endpoint retains its plain-text token interface. Evaluation uses
the graph's selected context so retry retrieval and context metrics agree.

## Verified October 8, 2026

The combined RAG, API authentication, streaming, concurrency, configuration,
router, evaluator, and Ollama option checks passed: **108 tests**.

The real local Qwen smoke test passed **5/5** cases with cached semantic
embeddings, CPU-only Ollama, and a 4,096-token planner context:

| Case | Result | Time |
| --- | --- | --- |
| Cited launch date | 15 September 2026 from atlas_handbook.md | 35.3 s |
| Follow-up across two documents | Followed release captain to Avery Chen; retained both source IDs; two searches and four planning calls | 60.2 s |
| Missing CEO phone number | Abstained; no unsupported answer generated | 27.5 s |
| Unresolved launch reference | Asked which project/document before lookup | Under 1 ms |
| Answer streaming | Three remote days per week, citing people_policy.md | 35.8 s |

Times are single-run query measurements, including retrieval and model calls,
excluding embedding-model startup. They demonstrate extra CPU inference cost
for agentic planning. No hosted deployment or large-corpus/load benchmark was
performed.

The installed test environment used LangChain 1.2.10, LangChain Core 1.4.0,
LangGraph 1.0.10, Ollama adapter 1.1.0, FAISS 1.14.2, and
sentence-transformers 2.7.0. Existing FAISS / embedding dependencies were not
reinstalled to match the repository's older/different pins for this test.
