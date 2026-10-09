from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.config.settings import settings
from services.local_agent import media_service as media_module
from services.local_agent.media_models import MediaRequest, catalogue, worker_python
from services.local_agent.media_service import MediaService
from services.local_agent.state import LocalState


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_MEDIA_PATH", str(tmp_path / "media"))
    monkeypatch.setattr(settings, "LOCAL_MEDIA_MAX_QUEUED", 4)
    monkeypatch.setattr(settings, "LOCAL_MEDIA_MAX_STORAGE_MB", 2)
    monkeypatch.setattr(media_module, "catalogue", Mock(return_value=[{"id": "sd-turbo", "kind": "image", "ready": True}]))
    instance = MediaService(LocalState(tmp_path / "state"))
    instance.pool.shutdown(wait=True)
    instance.pool = Mock()
    return instance


def spec():
    return MediaRequest(kind="image", prompt="A mountain lake", style="watercolor", width=512, height=512, model_id="sd-turbo")


def test_repeated_checkpoint_submission_is_idempotent_and_owner_scoped(service):
    first = service.submit(spec(), "owner", "run:tool")
    second = service.submit(spec(), "owner", "run:tool")
    assert first["id"] == second["id"]
    assert service.pool.submit.call_count == 1
    assert service.get(first["id"], "other") is None
    other = service.submit(spec(), "other", "run:tool")
    assert other["id"] != first["id"]


def test_unavailable_model_never_queues_a_job(service, monkeypatch):
    monkeypatch.setattr(media_module, "catalogue", Mock(return_value=[{"id": "sd-turbo", "ready": False, "reason": "Model weights are not installed."}]))
    with pytest.raises(ValueError, match="unavailable"):
        service.submit(spec(), "owner", "unavailable")
    assert service.list("owner") == []
    service.pool.submit.assert_not_called()


def test_queue_cap_counts_other_users_and_processes(service, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_MEDIA_MAX_QUEUED", 1)
    service.submit(spec(), "owner", "first")
    with pytest.raises(ValueError, match="queue is full"):
        service.submit(spec(), "other", "second")


def test_claim_serializes_workers_globally(service):
    first = service.submit(spec(), "owner", "first")
    second = service.submit(spec(), "other", "second")
    assert service._claim(first["id"], "owner")
    assert not service._claim(second["id"], "other")
    service._update(first["id"], "owner", status="succeeded", worker_active=False)
    assert service._claim(second["id"], "other")


def test_cancelled_queue_does_not_start_a_process(service, monkeypatch):
    job = service.submit(spec(), "owner", "cancel")
    popen = Mock()
    monkeypatch.setattr(media_module.subprocess, "Popen", popen)
    service.cancel(job["id"], "owner")
    service._run(job["id"], "owner")
    popen.assert_not_called()
    service._update(job["id"], "owner", status="succeeded")
    assert service.get(job["id"], "owner")["status"] == "cancelled"


def test_completed_artifact_delete_cannot_touch_foreign_or_arbitrary_files(service):
    job = service.submit(spec(), "owner", "delete")
    path = service.artifact_path(job)
    path.write_bytes(b"a-test-output")
    service._update(job["id"], "owner", status="succeeded")
    assert not service.delete(job["id"], "other")
    assert path.is_file()
    assert service.delete(job["id"], "owner")
    assert not path.exists()
    with pytest.raises(ValueError):
        service.artifact_path({"id": "../secrets", "kind": "image"})


def test_active_output_requires_cancellation_before_delete(service):
    job = service.submit(spec(), "owner", "active")
    with pytest.raises(ValueError, match="Cancel"):
        service.delete(job["id"], "owner")


def test_cancelled_worker_holds_its_lease_until_process_stops(service):
    first = service.submit(spec(), "owner", "cancel-running")
    second = service.submit(spec(), "owner", "next")
    assert service._claim(first["id"], "owner")
    service.cancel(first["id"], "owner")
    with pytest.raises(ValueError, match="worker to stop"):
        service.delete(first["id"], "owner")
    assert not service._claim(second["id"], "owner")
    service._update(first["id"], "owner", status="cancelled", worker_active=False)
    assert service._claim(second["id"], "owner")


def test_deleted_cancelled_queue_is_safe_when_executor_later_picks_it_up(service, monkeypatch):
    job = service.submit(spec(), "owner", "delete-queue")
    service.cancel(job["id"], "owner")
    service.delete(job["id"], "owner")
    popen = Mock()
    monkeypatch.setattr(media_module.subprocess, "Popen", popen)
    service._run(job["id"], "owner")
    popen.assert_not_called()


def test_stale_cancelled_worker_releases_lease_without_resurrecting_job(service):
    first = service.submit(spec(), "owner", "stale-cancel")
    assert service._claim(first["id"], "owner")
    service.cancel(first["id"], "owner")
    with service.state.connect() as conn:
        conn.execute("UPDATE local_items SET updated_at=? WHERE kind='media' AND id=?",
                     (time.time() - settings.LOCAL_MEDIA_TIMEOUT_SECONDS - 61, first["id"]))
    second = service.submit(spec(), "owner", "after-stale")
    assert service._claim(second["id"], "owner")
    assert service.get(first["id"], "owner")["status"] == "cancelled"


def test_worker_timeout_kills_process_and_removes_partial_output(service, monkeypatch):
    job = service.submit(spec(), "owner", "timeout")
    output = service.artifact_path(job)
    output.write_bytes(b"partial")
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(media_module.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(settings, "LOCAL_MEDIA_TIMEOUT_SECONDS", 0)
    service._run(job["id"], "owner")
    process.kill.assert_called_once()
    assert service.get(job["id"], "owner")["status"] == "failed"
    assert not output.exists()


def test_empty_component_directories_are_not_reported_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_IMAGE_MODEL_PATH", str(tmp_path))
    for filename in ["model_index.json", "scheduler/scheduler_config.json", "tokenizer/tokenizer_config.json",
                     "text_encoder/config.json", "vae/config.json", "unet/config.json"]:
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    models = catalogue("image", info={"available": True, "cuda": False})
    assert not models[0]["installed"] and not models[0]["ready"]


def test_running_job_after_expired_worker_reports_interruption(service):
    job = service.submit(spec(), "owner", "interrupted")
    job.update(status="running", updated_at=time.time() - settings.LOCAL_MEDIA_TIMEOUT_SECONDS - 61)
    service.state.put("media", job, "owner")
    assert service.get(job["id"], "owner")["status"] == "interrupted"


@pytest.fixture
def ltx_install(tmp_path, monkeypatch):
    checkpoint = tmp_path / "ltx.safetensors"
    checkpoint.write_bytes(b"checkpoint")
    monkeypatch.setattr(settings, "LOCAL_VIDEO_MODEL_PATH", str(checkpoint))
    monkeypatch.setattr(settings, "LOCAL_VIDEO_CONFIG_PATH", str(tmp_path / "config"))
    monkeypatch.setattr(settings, "LOCAL_VIDEO_TEXT_PATH", str(tmp_path / "text"))
    for name in ("model_index.json", "scheduler/scheduler_config.json", "transformer/config.json", "vae/config.json"):
        path = tmp_path / "config" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    for name in ("text_encoder/config.json", "tokenizer/tokenizer_config.json", "tokenizer/spiece.model"):
        path = tmp_path / "text" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    folder = tmp_path / "text" / "text_encoder"
    (folder / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"a": "first.safetensors", "b": "second.safetensors"}}))
    (folder / "first.safetensors").write_bytes(b"first")
    return folder


def test_ltx_readiness_requires_every_text_encoder_shard_and_cuda(ltx_install, monkeypatch):
    info = {"available": True, "cuda": True, "bf16": True, "ltx": True}
    incomplete = catalogue("video", info=info)[0]
    assert not incomplete["installed"] and not incomplete["ready"]
    assert "text encoder weights" in incomplete["reason"]
    (ltx_install / "second.safetensors").write_bytes(b"second")
    monkeypatch.setattr(settings, "LOCAL_MEDIA_DEVICE", "cpu")
    ready = catalogue("video", info=info)[0]
    assert ready["installed"] and ready["ready"] and ready["device"] == "cuda"
    assert not catalogue("video", info={**info, "cuda": False})[0]["ready"]
    assert not catalogue("video", info={**info, "bf16": False})[0]["ready"]
    assert not catalogue("video", info={**info, "ltx": False})[0]["ready"]


def test_video_and_image_workers_use_separate_configured_environments(tmp_path, monkeypatch):
    image = tmp_path / "image-python"
    video = tmp_path / "video-python"
    monkeypatch.setattr(settings, "LOCAL_MEDIA_PYTHON", str(image))
    monkeypatch.setattr(settings, "LOCAL_VIDEO_PYTHON", str(video))
    assert worker_python("image") == str(image.resolve())
    assert worker_python("video") == str(video.resolve())


def test_ltx_queue_uses_cuda_and_surfaces_worker_memory_error(service, monkeypatch):
    monkeypatch.setattr(media_module, "catalogue", Mock(return_value=[{"id": "ltx-video-2b-distilled", "ready": True}]))
    request = MediaRequest(kind="video", prompt="Ocean waves", style="realistic", width=704, height=512,
                           model_id="ltx-video-2b-distilled", seconds=2, fps=25)
    job = service.submit(request, "owner", "ltx-test")
    process = Mock(returncode=3)
    process.poll.return_value = 3
    def launch(command, **kwargs):
        assert "--config-path" in command and "--text-path" in command
        assert command[command.index("--device") + 1] == "cuda"
        kwargs["stdout"].write("100%|########| 8/8 " + json.dumps({"error": "Video stopped because system RAM is low."}) + "\n")
        kwargs["stdout"].flush()
        return process
    monkeypatch.setattr(media_module.subprocess, "Popen", launch)
    service._run(job["id"], "owner")
    failed = service.get(job["id"], "owner")
    assert failed["status"] == "failed" and "system RAM is low" in failed["error"]
    assert not failed["worker_active"] and not service.artifact_path(failed).exists()


def test_old_queued_model_fails_without_leaving_a_worker_lease(service, monkeypatch):
    job = service.submit(spec(), "owner", "old-model")
    job["spec"].update(kind="video", model_id="wan-2.1-1.3b", seconds=2, fps=8)
    service.state.put("media", job, "owner")
    popen = Mock()
    monkeypatch.setattr(media_module.subprocess, "Popen", popen)
    service._run(job["id"], "owner")
    failed = service.get(job["id"], "owner")
    assert failed["status"] == "failed" and not failed["worker_active"]
    assert "unsupported media settings" in failed["error"]
    popen.assert_not_called()


def test_successful_media_probe_is_reused_for_the_worker_lifetime(monkeypatch):
    from services.local_agent import media_models
    monkeypatch.setattr(settings, "LOCAL_MEDIA_WORKER_URL", "")
    monkeypatch.setattr(media_models, "_probe_cache", {})
    monkeypatch.setattr(media_models, "worker_python", lambda kind: "test-python")
    probe = Mock(return_value={"available": True, "cuda": False})
    monkeypatch.setattr(media_models, "_probe_worker", probe)
    clock = Mock(return_value=0)
    monkeypatch.setattr(media_models.time, "monotonic", clock)
    assert media_models.worker_info()["available"]
    clock.return_value = 1000
    assert media_models.worker_info()["available"]
    probe.assert_called_once()


def test_transient_media_probe_failure_is_retried_quickly(monkeypatch):
    from services.local_agent import media_models
    monkeypatch.setattr(settings, "LOCAL_MEDIA_WORKER_URL", "")
    monkeypatch.setattr(media_models, "_probe_cache", {})
    monkeypatch.setattr(media_models, "worker_python", lambda kind: "test-python")
    probe = Mock(side_effect=[{"available": False}, {"available": True}])
    monkeypatch.setattr(media_models, "_probe_worker", probe)
    clock = Mock(return_value=0)
    monkeypatch.setattr(media_models.time, "monotonic", clock)
    assert not media_models.worker_info()["available"]
    clock.return_value = 4
    assert media_models.worker_info()["available"]
    assert probe.call_count == 2


def test_probe_timeout_explains_loading_delay_instead_of_missing_packages(monkeypatch):
    from services.local_agent import media_models
    monkeypatch.setattr(media_models.subprocess, "run", Mock(side_effect=media_models.subprocess.TimeoutExpired("test-python", 30)))
    info = media_models._probe_worker("test-python")
    assert not info["available"]
    assert "took too long" in info["reason"]
    assert "Install" not in info["reason"]
