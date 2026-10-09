from __future__ import annotations

import hashlib
from unittest.mock import Mock

import pytest

from core.config.settings import settings
from services.local_agent import media_models, media_service as media_module
from services.local_agent.media_bridge import HostMediaClient
from services.local_agent.media_host import OWNER, create_host_app
from services.local_agent.media_models import MediaRequest
from services.local_agent.media_service import MediaService
from services.local_agent.state import LocalState

TOKEN = "test-local-worker-token-" + "x" * 40
JOB = "a" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"test-output"


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "IS_CLOUD_RUNTIME", False)
    monkeypatch.setattr(settings, "IS_PRODUCTION", False)
    monkeypatch.setattr(settings, "LOCAL_MEDIA_WORKER_URL", "")
    monkeypatch.setattr(settings, "LOCAL_MEDIA_WORKER_TOKEN", TOKEN)
    monkeypatch.setattr(settings, "LOCAL_MEDIA_PATH", str(tmp_path / "media"))
    monkeypatch.setattr(media_module, "catalogue", Mock(return_value=[{"id": "sd-turbo", "ready": True}]))
    media = MediaService(LocalState(tmp_path / "state"))
    media.pool.shutdown(wait=True)
    media.pool = Mock()
    return media


def spec():
    return MediaRequest(kind="image", model_id="sd-turbo", prompt="An ocean bay", style="watercolor", width=512, height=512)


def test_host_requires_token_and_private_connection_and_rejects_arbitrary_ids(service):
    client = create_host_app(service).test_client()
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"Authorization": "Bearer incorrect"}).status_code == 401
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/health", headers=auth).json == {"ok": True}
    assert client.get("/health", headers=auth, environ_overrides={"REMOTE_ADDR": "8.8.8.8"}).status_code == 401
    assert client.post("/jobs/arbitrary", headers=auth, json=spec().model_dump()).status_code == 400
    invalid = {**spec().model_dump(), "model_path": "secrets"}
    assert client.post(f"/jobs/{JOB}", headers=auth, json=invalid).status_code == 400
    assert service.list(OWNER) == []


def test_host_idempotent_queue_artifact_and_delete_require_inactive_worker(service):
    client = create_host_app(service).test_client()
    auth = {"Authorization": f"Bearer {TOKEN}"}
    path = f"/jobs/{JOB}"
    assert client.get(path + "/artifact", headers=auth).status_code == 404
    assert client.post(path, headers=auth, json=spec().model_dump()).json["status"] == "queued"
    assert client.post(path, headers=auth, json=spec().model_dump()).json["status"] == "queued"
    assert service.pool.submit.call_count == 1
    host_id = hashlib.sha256(f"{OWNER}:{JOB}".encode()).hexdigest()[:32]
    job = service.get(host_id, OWNER)
    service.artifact_path(job).write_bytes(PNG)
    service._update(host_id, OWNER, status="running", worker_active=True)
    assert client.post(path + "/cancel", headers=auth).json["status"] == "cancelled"
    assert client.delete(path, headers=auth).status_code == 409
    service._update(host_id, OWNER, status="cancelled", worker_active=False)
    assert client.delete(path, headers=auth).json == {"deleted": True}
    assert client.get(path, headers=auth).status_code == 404


@pytest.mark.parametrize("cloud,production", [(True, False), (False, True)])
def test_host_and_client_are_denied_in_cloud_and_production(service, monkeypatch, cloud, production):
    monkeypatch.setattr(settings, "IS_CLOUD_RUNTIME", cloud)
    monkeypatch.setattr(settings, "IS_PRODUCTION", production)
    with pytest.raises(ValueError, match="local development"):
        create_host_app(service)
    with pytest.raises(ValueError, match="local development"):
        HostMediaClient()


@pytest.mark.parametrize("url", ["https://example.com", "http://example.com", "http://localhost/jobs", "http://token@localhost:5002", "http://localhost:5002?token=x"])
def test_client_does_not_send_worker_credentials_to_nonlocal_urls(service, monkeypatch, url):
    monkeypatch.setattr(settings, "LOCAL_MEDIA_WORKER_URL", url)
    with pytest.raises(ValueError, match="local.*URL"):
        HostMediaClient()


def test_docker_readiness_uses_host_weights_instead_of_windows_paths(service, monkeypatch):
    from services.local_agent import media_bridge
    monkeypatch.setattr(settings, "LOCAL_MEDIA_WORKER_URL", "http://host.docker.internal:5002")
    monkeypatch.setattr(media_bridge, "host_capabilities", lambda: {
        "models": [{"id": "ltx-video-2b-distilled", "installed": True, "ready": True}],
        "workers": {"video": {"available": True, "cuda": True, "bf16": True, "ltx": True}},
    })
    monkeypatch.setattr(media_models, "_ltx_installation", Mock(side_effect=AssertionError("Docker must not open native weights")))
    model = media_models.catalogue("video")[0]
    assert model["ready"] and model["installed"]


def test_bridge_transfers_artifact_to_owner_store_and_cleans_host(service, monkeypatch):
    from services.local_agent import media_bridge
    monkeypatch.setattr(settings, "LOCAL_MEDIA_WORKER_URL", "http://host.docker.internal:5002")
    client = Mock()
    client.get.return_value = {"status": "succeeded", "worker_active": False}
    client.download.side_effect = lambda _job, output, _kind: output.write_bytes(PNG)
    monkeypatch.setattr(media_bridge, "HostMediaClient", lambda: client)
    popen = Mock()
    monkeypatch.setattr(media_module.subprocess, "Popen", popen)
    job = service.submit(spec(), "owner", "bridge-output")
    service._run(job["id"], "owner")
    result = service.get(job["id"], "owner")
    assert result["status"] == "succeeded" and not result["worker_active"]
    assert result["artifact_url"] == f"/api/local/media/{job['id']}/artifact"
    assert service.get(job["id"], "other-owner") is None
    assert service.artifact_path(result).read_bytes() == PNG
    client.delete.assert_called_once_with(job["id"])
    client.cancel.assert_not_called()
    popen.assert_not_called()


def test_bridge_cancel_waits_for_host_and_discards_partial_artifact(service, monkeypatch):
    from services.local_agent import media_bridge
    monkeypatch.setattr(settings, "LOCAL_MEDIA_WORKER_URL", "http://host.docker.internal:5002")
    client = Mock()
    monkeypatch.setattr(media_bridge, "HostMediaClient", lambda: client)
    monkeypatch.setattr(media_module.time, "sleep", lambda _seconds: None)
    job = service.submit(spec(), "owner", "bridge-cancel")
    client.submit.side_effect = lambda *_args: service.cancel(job["id"], "owner")
    client.get.side_effect = [{"worker_active": True}, {"worker_active": False}]
    service.artifact_path(job).write_bytes(b"partial")
    service._run(job["id"], "owner")
    assert service.get(job["id"], "owner")["status"] == "cancelled"
    assert not service.get(job["id"], "owner")["worker_active"]
    assert not service.artifact_path(job).exists()
    client.cancel.assert_called_once_with(job["id"])
    assert client.get.call_count == 2


def test_download_rejects_bad_output_without_keeping_partial_file(service, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_MEDIA_WORKER_URL", "http://127.0.0.1:5002")
    client = HostMediaClient()
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock()
    response.iter_content.return_value = [b"unexpected html response"]
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: response)
    output = tmp_path / "preview.mp4"
    with pytest.raises(RuntimeError, match="valid media"):
        client.download(JOB, output, "video")
    assert not output.exists() and not output.with_suffix(".mp4.partial").exists()
    client.close()


def test_cloud_cannot_reuse_a_cached_host_capability_result(service, monkeypatch):
    from services.local_agent import media_bridge
    import time
    monkeypatch.setattr(settings, "LOCAL_MEDIA_WORKER_URL", "http://host.docker.internal:5002")
    monkeypatch.setattr(media_bridge, "_cache", ((settings.LOCAL_MEDIA_WORKER_URL, TOKEN), time.monotonic() + 100,
                        {"models": [{"ready": True}], "workers": {"video": {"available": True}}}))
    monkeypatch.setattr(settings, "IS_CLOUD_RUNTIME", True)
    assert media_bridge.host_capabilities()["models"] == []
