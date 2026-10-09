"""Persistent, bounded queue with cancellable isolated model processes."""
from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.config.settings import settings
from services.local_agent.media_models import MediaRequest, catalogue, worker_python
from services.local_agent.state import LocalState

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


class MediaService:
    def __init__(self, state: LocalState | None = None):
        self.state = state or LocalState()
        self.root = Path(settings.LOCAL_MEDIA_PATH).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-media")
        self.lock = threading.RLock()

    def submit(self, spec: MediaRequest, user_id: str, request_key: str) -> dict:
        job_id = hashlib.sha256(f"{user_id}:{request_key}".encode()).hexdigest()[:32]
        with self.lock:
            self.state.expire_active("media", settings.LOCAL_MEDIA_TIMEOUT_SECONDS + 60)
            existing = self.state.get("media", job_id, user_id)
            if existing:
                return existing
            model = next((m for m in catalogue(spec.kind) if m["id"] == spec.model_id), None)
            if not model or not model["ready"]:
                raise ValueError(f"{spec.model_id} is unavailable: {(model or {}).get('reason', 'Unknown model.')}")
            storage_bytes = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
            if storage_bytes >= settings.LOCAL_MEDIA_MAX_STORAGE_MB * 1024**2:
                raise ValueError("Local media storage is full. Delete previous outputs before generating more.")
            now = time.time()
            job = {"id": job_id, "kind": spec.kind, "spec": spec.model_dump(), "status": "queued",
                   "progress": 0, "message": "Queued for the local media worker", "created_at": now,
                   "updated_at": now, "artifact_url": None, "error": None, "worker_active": False}
            # Serialize admissions across local API processes, including the global queue limit.
            with self.state.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                active = conn.execute("SELECT COUNT(*) FROM local_items WHERE kind='media' AND json_extract(payload, '$.status') IN ('queued','running')").fetchone()[0]
                if active >= settings.LOCAL_MEDIA_MAX_QUEUED:
                    raise ValueError("The local media queue is full. Wait for a job to finish.")
                conn.execute("INSERT INTO local_items VALUES ('media', ?, ?, ?, ?)",
                             (job_id, user_id, json.dumps(job), now))
            self.pool.submit(self._run, job_id, user_id)
            return job

    def get(self, job_id: str, user_id: str) -> dict | None:
        job = self.state.get("media", job_id, user_id)
        if job and job["status"] in {"queued", "running"} and time.time() - job["updated_at"] > settings.LOCAL_MEDIA_TIMEOUT_SECONDS + 60:
            job = self._update(job_id, user_id, status="interrupted", worker_active=False, message="The worker stopped. Submit a new generation to retry.")
        return job

    def list(self, user_id: str):
        return [self.get(job["id"], user_id) for job in self.state.list("media", user_id)]

    def _update(self, job_id: str, user_id: str, **updates) -> dict:
        return self.state.update("media", job_id, user_id, **updates)

    def cancel(self, job_id: str, user_id: str):
        job = self.get(job_id, user_id)
        if not job:
            return None
        if job["status"] not in TERMINAL:
            return self._update(job_id, user_id, status="cancelled", message="Cancelled")
        return job

    def delete(self, job_id: str, user_id: str):
        job = self.get(job_id, user_id)
        if not job:
            return False
        if job["status"] not in TERMINAL:
            raise ValueError("Cancel the generation before deleting its output.")
        if job.get("worker_active"):
            raise ValueError("Wait for the cancelled worker to stop before deleting its output.")
        output = self.artifact_path(job)
        for path in (output, self.root / f"{job_id}.request.json", self.root / f"{job_id}.progress.jsonl"):
            path.unlink(missing_ok=True)
        self.state.delete("media", job_id, user_id)
        return True

    def artifact_path(self, job: dict) -> Path:
        extension = "png" if job["kind"] == "image" else "mp4"
        # IDs are application-generated hashes, never filenames supplied by the model/user.
        if len(job["id"]) != 32 or any(c not in "0123456789abcdef" for c in job["id"]):
            raise ValueError("Invalid artifact ID")
        return self.root / f"{job['id']}.{extension}"

    def _claim(self, job_id: str, user_id: str) -> bool:
        self.state.expire_active("media", settings.LOCAL_MEDIA_TIMEOUT_SECONDS + 60)
        with self.state.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute("SELECT COUNT(*) FROM local_items WHERE kind='media' AND (json_extract(payload,'$.status')='running' OR json_extract(payload,'$.worker_active')=1)").fetchone()[0]
            if active:
                return False
            job = self.state.get("media", job_id, user_id)
            if not job or job["status"] != "queued":
                return False
            job.update(status="running", progress=1, message="Starting local worker", worker_active=True, updated_at=time.time())
            conn.execute("UPDATE local_items SET payload=?, updated_at=? WHERE kind='media' AND id=? AND user_id=?",
                         (json.dumps(job), job["updated_at"], job_id, user_id))
            return True

    def _run(self, job_id: str, user_id: str):
        deadline = time.monotonic() + settings.LOCAL_MEDIA_TIMEOUT_SECONDS
        while not self._claim(job_id, user_id):
            job = self.get(job_id, user_id)
            if not job or job["status"] in TERMINAL:
                return
            if time.monotonic() >= deadline:
                self._update(job_id, user_id, status="failed", error="Timed out waiting for the local worker.")
                return
            time.sleep(0.25)
        job = self.get(job_id, user_id)
        try:
            spec = MediaRequest.model_validate(job["spec"])
        except ValueError:
            self._update(job_id, user_id, status="failed", worker_active=False, message="Generation failed",
                         error="This saved request uses unsupported media settings. Create a new preview.")
            return
        if settings.LOCAL_MEDIA_WORKER_URL:
            self._run_remote(job_id, user_id, spec, deadline)
            return
        request_file = self.root / f"{job_id}.request.json"
        progress_file = self.root / f"{job_id}.progress.jsonl"
        request_file.write_text(spec.model_dump_json(), encoding="utf-8")
        output = self.artifact_path(job)
        model_path = settings.LOCAL_IMAGE_MODEL_PATH if spec.kind == "image" else settings.LOCAL_VIDEO_MODEL_PATH
        script = Path(__file__).resolve().parents[2] / "scripts" / "local_media_worker.py"
        process = None
        try:
            with progress_file.open("w", encoding="utf-8") as log:
                command = [worker_python(spec.kind), str(script), "--request", str(request_file),
                    "--model-path", str(Path(model_path).resolve()), "--output", str(output),
                    "--device", "cuda" if spec.kind == "video" else settings.LOCAL_MEDIA_DEVICE]
                if spec.kind == "video":
                    command += ["--config-path", str(Path(settings.LOCAL_VIDEO_CONFIG_PATH).resolve()),
                                "--text-path", str(Path(settings.LOCAL_VIDEO_TEXT_PATH).resolve()),
                                "--offload-path", str(Path(settings.LOCAL_VIDEO_OFFLOAD_PATH).resolve()),
                                "--min-available-ram-mb", str(settings.LOCAL_MEDIA_MIN_AVAILABLE_RAM_MB)]
                process = subprocess.Popen(command, stdout=log, stderr=log,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                offset = 0
                while process.poll() is None:
                    current = self.get(job_id, user_id)
                    if current["status"] == "cancelled":
                        self._stop_process(process)
                        return
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Local generation exceeded its time budget.")
                    with progress_file.open(encoding="utf-8", errors="replace") as reader:
                        reader.seek(offset)
                        lines = reader.read().splitlines()
                        offset = reader.tell()
                        for line in lines:
                            try:
                                progress = json.loads(line)
                                if "progress" in progress:
                                    self._update(job_id, user_id, progress=progress["progress"], message=progress["message"])
                            except (ValueError, KeyError):
                                pass
                    self._update(job_id, user_id)
                    time.sleep(0.5)
            if process.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
                error = "The local model could not generate an output. Check the worker log and reduce resource usage."
                for line in progress_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        # tqdm on stderr can precede a JSON event on the same
                        # line. Parse the event without losing resource errors.
                        event = json.loads(line[line.index("{"):])
                        if isinstance(event, dict) and isinstance(event.get("error"), str):
                            error = event["error"][:500]
                    except ValueError:
                        pass
                raise RuntimeError(error)
            self._update(job_id, user_id, status="succeeded", progress=100, message="Complete",
                         artifact_url=f"/api/local/media/{job_id}/artifact")
        except Exception as exc:
            self._update(job_id, user_id, status="failed", message="Generation failed", error=str(exc)[:500])
        finally:
            if process is not None and process.poll() is None:
                self._stop_process(process)
            final_job = self.get(job_id, user_id)
            if final_job:
                self._update(job_id, user_id, status=final_job["status"], worker_active=False)
            if not final_job or final_job["status"] != "succeeded":
                output.unlink(missing_ok=True)

    def _run_remote(self, job_id, user_id, spec, deadline):
        from services.local_agent.media_bridge import HostMediaClient
        client = None
        output = self.artifact_path(self.get(job_id, user_id))
        submitted = False
        try:
            client = HostMediaClient()
            # Mark before sending: a lost response must still cancel a job the
            # host may have accepted. Its stable request key prevents replay.
            submitted = True
            client.submit(job_id, spec.model_dump())
            while True:
                current = self.get(job_id, user_id)
                if not current or current["status"] == "cancelled":
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Local generation exceeded its time budget.")
                remote = client.get(job_id)
                status = remote["status"]
                if status == "succeeded" and not remote.get("worker_active"):
                    client.download(job_id, output, spec.kind)
                    self._update(job_id, user_id, status="succeeded", progress=100, message="Complete",
                                 artifact_url=f"/api/local/media/{job_id}/artifact")
                    break
                if status in TERMINAL and status != "succeeded":
                    raise RuntimeError(remote.get("error") or "The host media worker stopped.")
                self._update(job_id, user_id, progress=remote.get("progress", 1),
                             message=remote.get("message") or "Working on the Windows media host")
                time.sleep(0.5)
        except Exception as exc:
            self._update(job_id, user_id, status="failed", message="Generation failed", error=str(exc)[:500])
        finally:
            if client and submitted:
                try:
                    final = self.get(job_id, user_id)
                    if not final or final["status"] != "succeeded":
                        client.cancel(job_id)
                    # Hold the application lease until the host process stops.
                    cleanup_deadline = time.monotonic() + 15
                    while time.monotonic() < cleanup_deadline:
                        if not client.get(job_id).get("worker_active"):
                            if final and final["status"] in {"succeeded", "cancelled"}:
                                client.delete(job_id)
                            break
                        time.sleep(0.25)
                except (RuntimeError, ValueError):
                    pass  # The host's own bounded queue/timeout still applies.
            if client:
                client.close()
            final = self.get(job_id, user_id)
            if final:
                self._update(job_id, user_id, status=final["status"], worker_active=False)
            if not final or final["status"] != "succeeded":
                output.unlink(missing_ok=True)

    @staticmethod
    def _stop_process(process):
        # Windows venv python.exe can redirect to a child interpreter. Stop
        # the complete owned worker tree so cancellation releases GPU memory.
        try:
            import psutil
            owned = psutil.Process(process.pid)
            processes = [*reversed(owned.children(recursive=True)), owned]
            for child in processes:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            psutil.wait_procs(processes, timeout=15)
        except (ImportError, TypeError):
            process.kill()
        except psutil.NoSuchProcess:
            pass
        process.wait(timeout=15)


_service: MediaService | None = None
_service_lock = threading.Lock()


def media_service() -> MediaService:
    global _service
    with _service_lock:
        if _service is None or _service.state.root != Path(settings.LOCAL_AGENT_PATH).resolve():
            _service = MediaService()
        return _service
