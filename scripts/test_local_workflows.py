"""Real Qwen + real local image smoke test, with separate data and no cloud calls."""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/cache/local_workflows/live-report.json")
    parser.add_argument("--skip-image", action="store_true")
    args = parser.parse_args()
    root = Path("data/cache/local_workflows") / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    overrides = {"APP_ENV": "test", "AI_RUNTIME": "local", "DATABASE_URL": "", "DATABASE_AUTO_MIGRATE": "true",
        "SQLITE_PATH": str(root / "app.db"), "LOCAL_AGENT_PATH": str(root / "agent"), "LOCAL_MEDIA_PATH": str(root / "media"),
        "LOCAL_AGENT_ENABLED": "true", "LOCAL_AGENT_MODEL": "qwen3:8b", "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
        "LOCAL_MEDIA_DEVICE": "cpu", "EMBEDDING_PROVIDER": "local", "EMBED_DIM": "384", "INDEX_PATH": str(root / "faiss.index"),
        "RAG_AGENTIC_ENABLED": "true", "RAG_AGENTIC_MODELS": "qwen3:8b", "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    os.environ.update(overrides)
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from services.local_agent.agent_service import AgentService
    from services.local_agent.media_service import media_service
    from services.local_agent.state import LocalState
    from services.local_agent.media_models import catalogue
    from agents.rag_agent import rag_agent
    from infrastructure.llm.ollama_client import ollama
    if not ollama.health():
        raise RuntimeError("Start the local Ollama service before running these live tests.")
    service = AgentService(LocalState(root / "agent"))
    owner = "local-smoke-owner"
    cases = []

    def wait(run):
        deadline = time.monotonic() + 600
        last_status = None
        while time.monotonic() < deadline:
            run = service.get(run["id"], owner)
            if run["status"] != last_status:
                print(json.dumps({"run": run["id"], "status": run["status"]}), flush=True)
                last_status = run["status"]
            if run["status"] not in {"running", "queued"}:
                return run
            time.sleep(1)
        raise TimeoutError("The Qwen task exceeded the live test time budget.")

    def check(name, run, passed, started):
        case = {"name": name, "passed": bool(passed), "latency_ms": round((time.monotonic() - started) * 1000), "run": run}
        cases.append(case)
        print(json.dumps({"case": name, "passed": case["passed"], "latency_ms": case["latency_ms"], "result": run.get("result"), "error": run.get("error")}), flush=True)

    started = time.monotonic()
    run = wait(service.start("Use the calculator to work out 2 seconds multiplied by 8 frames per second.", "calculator", owner))
    check("native_calculator", run, run["status"] == "complete" and "16" in (run.get("result") or {}).get("answer", "") and
          any(t["tool"] == "calculator" for t in run["trace"]), started)

    started = time.monotonic()
    run = wait(service.start("Create an image of a cat.", "cat", owner))
    check("clarification_before_generation", run, run["status"] == "awaiting_input" and bool(run.get("question")) and not media_service().list(owner), started)
    if not args.skip_image and run["status"] == "awaiting_input":
        started = time.monotonic()
        # Reopening the service demonstrates a durable pause, rather than relying on a live graph object.
        reopened = AgentService(LocalState(root / "agent"))
        resumed = reopened.resume(run["id"], "Watercolor style, square 512 by 512. Keep the cat as the subject and use SD Turbo.", owner)
        run = wait(resumed)
        job = (run.get("result") or {}).get("media_job")
        deadline = time.monotonic() + 600
        while job and job["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(1)
            job = media_service().get(job["id"], owner)
        passed = bool(job and job["status"] == "succeeded" and media_service().get(job["id"], "other-owner") is None)
        if passed:
            from PIL import Image
            artifact = media_service().artifact_path(job)
            with Image.open(artifact) as image:
                passed = image.size == (512, 512) and image.format == "PNG"
            run["image_path"] = str(artifact.resolve())
        run["media_result"] = job
        check("resumed_real_local_image", run, passed, started)

    started = time.monotonic()
    # Keep the unavailable-model case deterministic even after LTX is installed.
    from core.config.settings import settings
    video_path = settings.LOCAL_VIDEO_MODEL_PATH
    try:
        settings.LOCAL_VIDEO_MODEL_PATH = str(root / "not-installed.safetensors")
        run = wait(service.start("Check available video models. Can you generate a realistic square 512 by 512 ocean-waves video, 2 seconds at 25 fps? If no video model is ready, explain why.", "video", owner))
        videos = [m for m in catalogue("video") if m["ready"]]
    finally:
        settings.LOCAL_VIDEO_MODEL_PATH = video_path
    check("unavailable_video_is_truthful", run, not videos and run["status"] == "complete" and
          any(t["tool"] == "media_models" for t in run["trace"]) and
          "not" in (run.get("result") or {}).get("answer", "").lower() and
          not any(t.get("action") == "general_chat" or t["tool"] == "generate_media" and t["status"] == "queued" for t in run["trace"]), started)

    rag_agent.ingest_text("Project Cedar is scheduled to launch on 22 October 2026. Morgan Patel is the release lead.", "cedar-handbook.md", user_id=owner)
    rag_agent.ingest_text("Project Cedar launches in 2040. SECRET-OTHER-USER.", "private-other.md", user_id="other-owner")
    started = time.monotonic()
    run = wait(service.start("Use my documents to find when Project Cedar is scheduled to launch. Cite the source.", "rag", owner))
    result = run.get("result") or {}
    answer = result.get("answer", "")
    check("orchestrated_real_rag", run, run["status"] == "complete" and "22" in answer and "October" in answer and
          any(s.get("source") == "cedar-handbook.md" for s in result.get("sources", [])) and "SECRET-OTHER-USER" not in answer and
          any(t.get("action") == "rag_ask" for t in run["trace"]), started)

    report = {"model": "qwen3:8b", "image_model": "sd-turbo", "media_device": "cpu", "cases": cases,
              "passed": all(c["passed"] for c in cases), "isolated_data": str(root.resolve())}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "report": str(output.resolve())}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
