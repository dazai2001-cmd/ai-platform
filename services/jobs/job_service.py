import threading
import time
import uuid
from typing import Callable


class JobService:
    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, label: str, fn: Callable[[], dict | int | None], user_id: str = "local") -> dict:
        job_id = uuid.uuid4().hex
        now = time.time()
        job = {
            "id": job_id,
            "label": label,
            "user_id": user_id,
            "status": "queued",
            "progress": 0,
            "message": "Queued",
            "result": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._jobs[job_id] = job

        thread = threading.Thread(target=self._run, args=(job_id, fn), daemon=True)
        thread.start()
        return job

    def get(self, job_id: str, user_id: str = "local") -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job and job.get("user_id", "local") != user_id:
                return None
            return dict(job) if job else None

    def _update(self, job_id: str, **updates):
        with self._lock:
            if job_id not in self._jobs:
                return
            self._jobs[job_id].update(updates)
            self._jobs[job_id]["updated_at"] = time.time()

    def _run(self, job_id: str, fn: Callable[[], dict | int | None]):
        self._update(job_id, status="running", progress=20, message="Processing")
        try:
            result = fn()
            self._update(
                job_id,
                status="succeeded",
                progress=100,
                message="Complete",
                result=result,
            )
        except Exception as e:
            self._update(
                job_id,
                status="failed",
                progress=100,
                message="Failed",
                error=str(e),
            )


jobs = JobService()
