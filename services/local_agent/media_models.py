"""Fixed, validated media registry. A catalogue entry alone never implies readiness."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from core.config.settings import settings


class MediaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["image", "video"]
    prompt: str = Field(min_length=3, max_length=2000)
    style: str = Field(min_length=2, max_length=120)
    width: int = Field(ge=256, le=768)
    height: int = Field(ge=256, le=768)
    model_id: Literal["sd-turbo", "ltx-video-2b-distilled"]
    seconds: int | None = Field(default=None, ge=1, le=6)
    fps: int | None = Field(default=None, ge=25, le=25)
    seed: int = Field(default=0, ge=0, le=2**31-1)

    @model_validator(mode="after")
    def validate_resource_limits(self):
        if not self.prompt.strip() or not self.style.strip():
            raise ValueError("A subject and visual style are required.")
        if self.width % 64 or self.height % 64:
            raise ValueError("Dimensions must be multiples of 64.")
        if self.kind == "image":
            if self.model_id != "sd-turbo" or self.width * self.height > 512 * 512:
                raise ValueError("SD Turbo images are limited to 262144 pixels (for example 512 x 512).")
            if self.seconds is not None or self.fps is not None:
                raise ValueError("Image requests must not contain video duration or frame rate.")
        else:
            if self.model_id != "ltx-video-2b-distilled" or max(self.width, self.height) > 704 or self.width * self.height > 704 * 512:
                raise ValueError("LTX previews are limited to 704 x 512 pixels, or another shape within 360448 pixels.")
            if self.seconds is None or self.fps is None:
                raise ValueError("Video duration and frame rate are required.")
        return self

    @property
    def frames(self):
        # LTX expects 8n+1 frames. Round up so the requested duration is covered.
        return ((self.seconds * self.fps - 1 + 7) // 8) * 8 + 1 if self.kind == "video" else 1


def worker_python(kind: str = "image") -> str:
    root = Path(__file__).resolve().parents[2]
    suffix = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    if kind == "video":
        if settings.LOCAL_VIDEO_PYTHON:
            return str(Path(settings.LOCAL_VIDEO_PYTHON).resolve())
        for name in (".venv-video", ".venv-video-test"):
            candidate = root / name / suffix
            if candidate.is_file():
                return str(candidate)
    if settings.LOCAL_MEDIA_PYTHON:
        return str(Path(settings.LOCAL_MEDIA_PYTHON).resolve())
    bundled = root / ".venv-media" / suffix
    return str(bundled) if bundled.is_file() else sys.executable


_probe_lock = threading.Lock()
_probe_cache: dict[str, tuple[float, dict]] = {}


def worker_info(kind: str = "image") -> dict:
    if settings.LOCAL_MEDIA_WORKER_URL:
        from services.local_agent.media_bridge import host_capabilities
        remote = host_capabilities()
        return {**remote["workers"].get(kind, {"available": False, "cuda": False,
                 "reason": remote.get("reason", "The host worker is unavailable.")}), "bridge": True}
    executable = worker_python(kind)
    with _probe_lock:
        cached = _probe_cache.get(executable)
        # Library/GPU availability is stable during a worker lifetime. Avoid
        # importing Torch in extra probe processes while a preview is running.
        if cached and (cached[1].get("available") or time.monotonic() - cached[0] < 3):
            return dict(cached[1])
        info = _probe_worker(executable)
        _probe_cache[executable] = (time.monotonic(), info)
        return dict(info)


def _probe_worker(executable: str) -> dict:
    script = Path(__file__).resolve().parents[2] / "scripts" / "local_media_worker.py"
    try:
        result = subprocess.run([executable, str(script), "--probe"], capture_output=True, text=True,
                                timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
        if result.returncode == 0 and lines:
            return json.loads(lines[-1])
    except subprocess.TimeoutExpired:
        return {"available": False, "cuda": False, "reason": "The media environment took too long to load. Retry when the laptop is less busy."}
    except (OSError, ValueError):
        pass
    return {"available": False, "cuda": False, "reason": "Install requirements-local-media.txt in a separate environment."}


def _installed(path: str, kind: str) -> bool:
    root = Path(path)
    required = ["model_index.json", "scheduler/scheduler_config.json", "tokenizer/tokenizer_config.json",
                "text_encoder/config.json", "vae/config.json"]
    required += ["unet/config.json"] if kind == "image" else ["transformer/config.json"]
    components = ["text_encoder", "vae", "unet" if kind == "image" else "transformer"]
    return all((root / name).is_file() for name in required) and all(
        any((root / component).glob("*.safetensors")) for component in components)


def _ltx_installation() -> tuple[bool, str]:
    checkpoint = Path(settings.LOCAL_VIDEO_MODEL_PATH)
    config = Path(settings.LOCAL_VIDEO_CONFIG_PATH)
    text = Path(settings.LOCAL_VIDEO_TEXT_PATH)
    if not checkpoint.is_file() or checkpoint.stat().st_size == 0:
        return False, "LTX video checkpoint is not installed."
    if not all((config / name).is_file() for name in (
        "model_index.json", "scheduler/scheduler_config.json", "transformer/config.json", "vae/config.json"
    )):
        return False, "LTX pipeline configuration is not installed."
    if not all((text / name).is_file() for name in (
        "text_encoder/config.json", "tokenizer/tokenizer_config.json", "tokenizer/spiece.model"
    )):
        return False, "LTX text encoder and tokenizer are not installed."
    folder = (text / "text_encoder").resolve()
    try:
        single = folder / "model.safetensors"
        if single.is_file():
            weights = [single]
        else:
            index = json.loads((folder / "model.safetensors.index.json").read_text(encoding="utf-8"))
            names = set(index["weight_map"].values())
            weights = [(folder / name).resolve() for name in names]
            for weight in weights:
                weight.relative_to(folder)
        if not weights or any(not path.is_file() or path.stat().st_size == 0 for path in weights):
            raise ValueError("Missing weights")
    except (OSError, ValueError, KeyError, TypeError):
        return False, "LTX text encoder weights are not installed completely."
    return True, "Ready"


def catalogue(kind: str = "all", info: dict | None = None) -> list[dict]:
    remote = None
    if settings.LOCAL_MEDIA_WORKER_URL and info is None:
        from services.local_agent.media_bridge import host_capabilities
        remote = host_capabilities()
    entries = [
        ("sd-turbo", "image", "stabilityai/sd-turbo", settings.LOCAL_IMAGE_MODEL_PATH,
         "Fast image previews; square 512 x 512 or another shape within 262144 pixels.", "https://huggingface.co/stabilityai/sd-turbo#model-description"),
        ("ltx-video-2b-distilled", "video", "Lightricks/LTX-Video", settings.LOCAL_VIDEO_MODEL_PATH,
         "Experimental silent LTX previews, 1-6 seconds at 25 fps. Motion and geometry may be imperfect.",
         "https://huggingface.co/Lightricks/LTX-Video/blob/8984fa25007f376c1a299016d0957a37a2f797bb/LTX-Video-Open-Weights-License-0.X.txt"),
    ]
    models = []
    for model_id, media_type, repo, path, description, license_url in entries:
        if kind not in {"all", media_type}:
            continue
        worker = info if info is not None else worker_info(media_type)
        if remote is not None:
            host_model = next((model for model in remote["models"] if model.get("id") == model_id), {})
            installed = bool(host_model.get("installed"))
            missing_reason = host_model.get("reason") or remote.get("reason", "The host model is unavailable.")
        elif media_type == "image":
            installed, missing_reason = _installed(path, media_type), "Model weights are not installed."
        else:
            installed, missing_reason = _ltx_installation()
        device_ok = settings.LOCAL_MEDIA_DEVICE == "cpu" or worker.get("cuda", False)
        if media_type == "video":
            device_ok = worker.get("cuda", False) and worker.get("bf16", False) and worker.get("ltx", False)
        ready = bool(installed and worker.get("available") and device_ok and
                     (remote is None or host_model.get("ready")))
        reason = "Ready" if ready else (missing_reason if not installed else
                 worker.get("reason") or "LTX requires requirements-local-video.txt and a CUDA GPU with BF16 support.")
        models.append({"id": model_id, "kind": media_type, "repository": repo, "installed": installed,
                       "ready": ready, "description": description, "reason": reason, "license_url": license_url,
                       "device": "cuda" if media_type == "video" else settings.LOCAL_MEDIA_DEVICE,
                       **({"fps": 25, "max_seconds": 6, "experimental": True} if media_type == "video" else {})})
    return models
