"""Authenticated Docker Desktop connection to the local Windows media worker."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

from core.config.settings import settings


class HostMediaClient:
    def __init__(self):
        if settings.IS_CLOUD_RUNTIME or settings.IS_PRODUCTION:
            raise ValueError("The host media worker is available only in local development.")
        url = urlsplit(settings.LOCAL_MEDIA_WORKER_URL)
        if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "host.docker.internal"}
                or url.username or url.password or url.path or url.query or url.fragment):
            raise ValueError("Use a local http://host.docker.internal:5002 media worker URL.")
        if len(settings.LOCAL_MEDIA_WORKER_TOKEN) < 32:
            raise ValueError("Start scripts/start_local_media_host.py to configure the local worker token.")
        self.base = settings.LOCAL_MEDIA_WORKER_URL
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["Authorization"] = f"Bearer {settings.LOCAL_MEDIA_WORKER_TOKEN}"

    def close(self):
        self.session.close()

    @staticmethod
    def _job_path(job_id):
        if not isinstance(job_id, str) or len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise ValueError("Invalid media job ID.")
        return f"/jobs/{job_id}"

    def _request(self, method, path, **kwargs):
        try:
            response = self.session.request(method, self.base + path, timeout=kwargs.pop("timeout", (5, 15)),
                                            allow_redirects=False, **kwargs)
            if response.status_code >= 300:
                response.close()
                raise RuntimeError("The local host worker rejected the request. Check its token and worker log.")
            return response
        except requests.RequestException as exc:
            raise RuntimeError("The local host worker is unreachable. Start scripts/start_local_media_host.py on Windows.") from exc

    def _json(self, method, path, **kwargs):
        with self._request(method, path, **kwargs) as response:
            return response.json()

    def capabilities(self):
        return self._json("GET", "/capabilities", timeout=(5, 70))

    def submit(self, job_id, spec):
        return self._json("POST", self._job_path(job_id), json=spec)

    def get(self, job_id):
        return self._json("GET", self._job_path(job_id))

    def cancel(self, job_id):
        return self._json("POST", self._job_path(job_id) + "/cancel")

    def delete(self, job_id):
        return self._json("DELETE", self._job_path(job_id))

    def download(self, job_id, output: Path, kind: str):
        partial = output.with_suffix(output.suffix + ".partial")
        try:
            with self._request("GET", self._job_path(job_id) + "/artifact", stream=True, timeout=(5, 30)) as response:
                total = 0
                with partial.open("wb") as writer:
                    for chunk in response.iter_content(1024 * 1024):
                        total += len(chunk)
                        if total > 64 * 1024**2:
                            raise RuntimeError("The media preview exceeded its transfer limit.")
                        writer.write(chunk)
            with partial.open("rb") as reader:
                header = reader.read(12)
            valid = header.startswith(b"\x89PNG\r\n\x1a\n") if kind == "image" else header[4:8] == b"ftyp"
            if not valid:
                raise RuntimeError("The local worker did not return a valid media file.")
            partial.replace(output)
        finally:
            partial.unlink(missing_ok=True)


_lock = threading.Lock()
_cache: tuple[tuple[str, str], float, dict] | None = None


def host_capabilities() -> dict:
    global _cache
    if settings.IS_CLOUD_RUNTIME or settings.IS_PRODUCTION:
        return {"models": [], "workers": {}, "reason": "Host media is available only in local development."}
    key = (settings.LOCAL_MEDIA_WORKER_URL, settings.LOCAL_MEDIA_WORKER_TOKEN)
    with _lock:
        if _cache and _cache[0] == key and time.monotonic() < _cache[1]:
            return _cache[2]
        client = None
        try:
            client = HostMediaClient()
            result = client.capabilities()
            if not isinstance(result.get("models"), list) or not isinstance(result.get("workers"), dict):
                raise ValueError("Invalid worker capabilities.")
            lifetime = 30
        except (ValueError, RuntimeError):
            result = {"models": [], "workers": {}, "reason": "Start the Windows media host and recreate the Docker API after configuring its token."}
            lifetime = 3
        finally:
            if client:
                client.close()
        _cache = (key, time.monotonic() + lifetime, result)
        return result
