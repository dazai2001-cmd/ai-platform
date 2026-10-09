"""Start the Windows media host, then the usual local Docker Compose stack."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

import requests
from start_local_media_host import ensure_token


def main():
    project = Path(__file__).resolve().parents[1]
    token = ensure_token(project)
    session = requests.Session()
    session.trust_env = False
    session.headers["Authorization"] = f"Bearer {token}"

    def ready():
        try:
            response = session.get("http://127.0.0.1:5002/health", timeout=2)
            if response.status_code == 401:
                raise RuntimeError("Port 5002 has a worker with a different token. Restart that worker with this checkout's .env.")
            response.raise_for_status()
            return response.json().get("ok") is True
        except requests.RequestException:
            return False

    try:
        if not ready():
            log_dir = project / "data/cache/local_media_host"
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / "host.log").open("a", encoding="utf-8") as log:
                process = subprocess.Popen([sys.executable, str(project / "scripts/start_local_media_host.py")],
                    cwd=project, stdout=log, stderr=log, env={**os.environ, "LOCAL_MEDIA_WORKER_TOKEN": token},
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline = time.monotonic() + 30
            while not ready():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(f"The Windows worker could not start. Read {log_dir / 'host.log'}.")
                time.sleep(0.5)
        print("Windows media host ready. Starting local Docker Compose.", flush=True)
        # Forward the token even when it came from the caller's environment.
        return subprocess.run(["docker", "compose", "up", "--build", "-d"], cwd=project,
                              env={**os.environ, "LOCAL_MEDIA_WORKER_TOKEN": token}).returncode
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
