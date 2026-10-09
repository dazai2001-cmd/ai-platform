"""Real Qwen-to-LTX smoke test with isolated state and a freshly encoded prompt."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, choices=range(1, 7), default=2)
    parser.add_argument("--host-worker", action="store_true", help="Exercise the Docker-style HTTP bridge on localhost.")
    parser.add_argument("--worker-url", default="http://127.0.0.1:5002")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    root = project / "data/cache/local_video_smoke" / uuid.uuid4().hex
    root.mkdir(parents=True)
    os.environ.update({"APP_ENV": "test", "AI_RUNTIME": "local", "DATABASE_URL": "", "DATABASE_AUTO_MIGRATE": "true",
        "SQLITE_PATH": str(root / "app.db"), "LOCAL_AGENT_PATH": str(root / "agent"), "LOCAL_MEDIA_PATH": str(root / "media"),
        "LOCAL_AGENT_ENABLED": "true", "LOCAL_AGENT_MODEL": "qwen3:8b", "OLLAMA_BASE_URL": args.ollama_url,
        "INDEX_PATH": str(root / "faiss.index"), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"})
    os.environ["LOCAL_MEDIA_WORKER_URL"] = args.worker_url if args.host_worker else ""
    from services.local_agent.agent_service import AgentService
    from services.local_agent.media_service import media_service
    from services.local_agent.media_models import catalogue
    from infrastructure.llm.ollama_client import ollama
    import psutil
    import requests

    model = catalogue("video")[0]
    if not model["ready"]:
        raise RuntimeError(model["reason"])
    if not ollama.health():
        raise RuntimeError("Start Ollama before running this local video smoke test.")
    owner = "video-smoke-owner"
    service = AgentService()
    media = media_service()
    started = time.monotonic()
    report = {"passed": False, "model": model["id"], "root": str(root), "minimum_available_ram_mib": 0,
              "platform": sys.platform, "host_worker": args.host_worker}
    try:
        query = ("Create a photorealistic video of gentle ocean waves rolling onto an empty sandy beach. "
                 "Static camera, soft morning light. Landscape 704 by 512, "
                 f"{args.seconds} seconds at 25 fps.")
        run = service.start(query, "video-smoke", owner)
        report["query"] = query
        deadline = time.monotonic() + 600
        last_status = None
        while time.monotonic() < deadline:
            run = service.get(run["id"], owner)
            if run["status"] != last_status:
                print(json.dumps({"phase": "qwen", "status": run["status"]}), flush=True)
                last_status = run["status"]
            if run["status"] not in {"queued", "running"}:
                break
            time.sleep(0.5)
        report["run"] = run
        if run["status"] != "complete" or not run.get("result", {}).get("media_job"):
            raise RuntimeError("Qwen did not queue a complete, explicit LTX request.")
        job_id = run["result"]["media_job"]["id"]
        report["planner_seconds"] = round(time.monotonic() - started, 2)
        # Observe the real handoff; do not replace unload or inference with mocks.
        response = requests.get(f"{ollama.base_url}/api/ps", timeout=15)
        response.raise_for_status()
        report["models_after_handoff"] = [m["name"] for m in response.json().get("models", [])]
        minimum = psutil.virtual_memory().available / 2**20
        deadline = time.monotonic() + 1200
        last_message = None
        while time.monotonic() < deadline:
            job = media.get(job_id, owner)
            minimum = min(minimum, psutil.virtual_memory().available / 2**20)
            if job["message"] != last_message:
                print(json.dumps({"phase": "video", "status": job["status"], "progress": job["progress"],
                                  "message": job["message"]}), flush=True)
                last_message = job["message"]
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(0.5)
        report["job"] = job
        report["minimum_available_ram_mib"] = round(minimum)
        if job["status"] != "succeeded":
            raise RuntimeError(job.get("error") or "Local video worker did not complete.")
        # Verify ownership, persistence and the artifact produced by the real queue.
        from services.local_agent.state import LocalState
        restored = LocalState(root / "agent").get("media", job_id, owner)
        assert restored["status"] == "succeeded"
        assert media.get(job_id, "other-owner") is None
        artifact = media.artifact_path(job)
        assert artifact.is_file() and artifact.stat().st_size > 0
        report.update(passed=True, artifact=str(artifact), artifact_bytes=artifact.stat().st_size)
    except Exception as exc:
        report["error"] = str(exc)[:500]
        if "run" in locals():
            service.cancel(run["id"], owner)
        if "job_id" in locals():
            media.cancel(job_id, owner)
    finally:
        service.pool.shutdown(wait=True)
        media.pool.shutdown(wait=True)
        report["seconds"] = round(time.monotonic() - started, 2)
        output = project / "data/cache/local_video_smoke" / (
            "docker-report.json" if args.host_worker and sys.platform == "linux" else
            "bridge-report.json" if args.host_worker else "live-report.json")
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "seconds": report["seconds"], "report": str(output),
                          "artifact": report.get("artifact"), "error": report.get("error")}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
