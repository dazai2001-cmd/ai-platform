"""Start the Windows media worker used by the local Docker Compose API."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import sys


def ensure_token(project: Path):
    from dotenv import dotenv_values, set_key
    env_file = project / ".env"
    token = os.getenv("LOCAL_MEDIA_WORKER_TOKEN") or dotenv_values(env_file).get("LOCAL_MEDIA_WORKER_TOKEN")
    if not token:
        token = secrets.token_hex(32)
        env_file.touch(exist_ok=True)
        set_key(str(env_file), "LOCAL_MEDIA_WORKER_TOKEN", token, quote_mode="never")
        print("Configured the local worker token in .env. Recreate the Docker API to load it.", flush=True)
    if len(token) < 32:
        raise ValueError("LOCAL_MEDIA_WORKER_TOKEN must contain at least 32 characters.")
    return token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=["127.0.0.1", "0.0.0.0"], default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5002)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    token = ensure_token(project)
    # Host state has its own SQLite file; Windows and Linux never concurrently
    # open the application's database through a Docker bind mount.
    host_root = Path(os.getenv("LOCAL_MEDIA_HOST_PATH", str(project / "data/cache/local_media_host"))).resolve()
    os.environ.update({"LOCAL_MEDIA_WORKER_URL": "", "LOCAL_MEDIA_WORKER_TOKEN": token,
                       "LOCAL_AGENT_PATH": str(host_root / "state"), "LOCAL_MEDIA_PATH": str(host_root / "outputs"),
                       "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "LANGSMITH_TRACING": "false"})
    from services.local_agent.media_host import create_host_app
    from werkzeug.serving import WSGIRequestHandler, make_server

    class QuietHandler(WSGIRequestHandler):
        def log_request(self, code="-", size="-"):
            pass

    app = create_host_app()
    server = make_server(args.host, args.port, app, threaded=True, request_handler=QuietHandler)
    print(f"Local media host listening on port {args.port}; requests require the local worker token.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service = app.extensions["media_service"]
        from services.local_agent.media_host import OWNER
        for job in service.list(OWNER):
            if job["status"] in {"queued", "running"}:
                service.cancel(job["id"], OWNER)
        service.pool.shutdown(wait=True)
        server.server_close()


if __name__ == "__main__":
    main()
