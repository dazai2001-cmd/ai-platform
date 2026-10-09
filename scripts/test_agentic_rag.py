"""Live local RAG smoke test with an isolated FAISS index and known documents."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--embedding-provider", choices=("local", "hashing"), default="local")
    parser.add_argument("--output", type=Path, default=ROOT / "data/cache/agentic_rag/live-report.json")
    args = parser.parse_args()
    run_directory = ROOT / "data/cache/agentic_rag" / str(uuid4())
    run_directory.mkdir(parents=True, exist_ok=True)
    # No application database, user's vector index, cloud API, or downloads.
    os.environ.update({
        "APP_ENV": "test", "AI_RUNTIME": "local", "DATABASE_URL": "",
        "DATABASE_AUTO_MIGRATE": "true", "SQLITE_PATH": str(run_directory / "app.sqlite"),
        "EMBEDDING_PROVIDER": args.embedding_provider,
        "EMBED_DIM": "384" if args.embedding_provider == "local" else "1024",
        "INDEX_PATH": str(run_directory / "faiss.index"),
        "RAG_AGENTIC_ENABLED": "true", "RAG_AGENTIC_MODELS": args.model,
        "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
        "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
    })
    from application.retrieval.retriever import Retriever
    from core.config.settings import settings
    from domain.rag.pipeline import QAPipeline
    from infrastructure.embeddings.embedder import Embedder
    from infrastructure.vectorstore.faiss_store import FAISSStore

    print(f"Loading {args.embedding_provider} embeddings (local model must already be cached)...", flush=True)
    embedder = Embedder()
    store = FAISSStore(dim=embedder.dim, index_path=str(run_directory / "faiss.index"))
    documents = [
        ("atlas_handbook.md", "Project Atlas is scheduled to launch on 15 September 2026. The release captain approves the Atlas launch.", "rag-smoke"),
        ("release_directory.md", "The release captain is Avery Chen. The backup engineer is Jordan Reed.", "rag-smoke"),
        ("people_policy.md", "The flexible working policy permits employees to work remotely for up to three days per week.", "rag-smoke"),
        ("other_user_private.md", "Project Atlas launches on 1 January 2040. The PRIVATE-OTHER-USER-CODE is OMEGA-SECRET.", "other-user"),
    ]
    vectors = embedder.embed_batch([text for _, text, _ in documents])
    store.add_and_save(vectors, [{"source": source, "text": text, "user_id": owner}
                                 for source, text, owner in documents])
    retriever = Retriever(embedder, store)
    pipeline = QAPipeline(retriever)
    cases = []
    history = []

    def run_case(name, question, expected, *, prior=None, source=None, clarification=False):
        print(f"Running {name}...", flush=True)
        started = time.monotonic()
        result = pipeline.ask(question, history=prior, model=args.model, user_id="rag-smoke")
        lowered = result["answer"].lower()
        checks = {
            "expected_answer": all(part.lower() in lowered for part in expected),
            "owner_isolation": "OMEGA-SECRET" not in str(result) and "other_user_private.md" not in str(result) and "2040" not in lowered,
            "agentic_flow": result.get("agentic", {}).get("enabled") is True,
            "clarification": bool(result.get("needs_clarification")) == clarification,
        }
        if source:
            checks["expected_source"] = source in {item["source"] for item in result["sources"]}
        if clarification:
            checks["no_search_before_clarification"] = result["agentic"]["searches"] == 0
        case = {"name": name, "question": question, "latency_ms": round((time.monotonic() - started) * 1000),
                "passed": all(checks.values()), "checks": checks, "result": result}
        cases.append(case)
        print(f"{name}: {'PASS' if case['passed'] else 'FAIL'} ({case['latency_ms']} ms) {result['answer']}", flush=True)
        return result

    try:
        # A single hit makes the follow-up genuinely require another search:
        # handbook -> release captain -> name in the directory.
        settings.TOP_K = 1
        first = run_case("grounded_answer", "When is Project Atlas scheduled to launch?",
                         ["15 September 2026"], source="atlas_handbook.md")
        history = [{"role": "user", "content": "When is Project Atlas scheduled to launch?"},
                   {"role": "assistant", "content": first["answer"]}]
        run_case("followup_multihop", "Who approves that launch? Give the person's name.",
                 ["Avery Chen"], prior=history, source="release_directory.md")
        run_case("unsupported_question", "What is the personal phone number of the Project Atlas CEO?",
                 ["don't have enough information"])
        run_case("clarification", "When does it launch?", [], clarification=True)

        print("Running streaming_answer...", flush=True)
        started = time.monotonic()
        answer = "".join(pipeline.stream_ask(
            "How many remote working days are available each week?",
            model=args.model, user_id="rag-smoke",
        ))
        passed = "three" in answer.lower() and "people_policy.md" in answer and "OMEGA-SECRET" not in answer
        cases.append({"name": "streaming_answer", "passed": passed,
                      "latency_ms": round((time.monotonic() - started) * 1000), "answer": answer})
        print(f"streaming_answer: {'PASS' if passed else 'FAIL'} {answer}", flush=True)
    except Exception as exc:
        # This standalone test contains only synthetic documents and local calls.
        import traceback
        traceback.print_exc()
        cases.append({"name": "runtime_error", "passed": False, "error": f"{type(exc).__name__}: {exc}"})
    report = {
        "model": args.model, "embedding_provider": embedder.provider, "embedding_model": embedder.model_name,
        "context_tokens": settings.RAG_AGENTIC_CONTEXT_TOKENS, "top_k": settings.TOP_K,
        "passed": bool(cases) and all(case["passed"] for case in cases),
        "cases": cases, "fixture_index": str(run_directory),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {args.output}", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
