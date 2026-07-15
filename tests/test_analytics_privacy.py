import hashlib
import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from flask import Flask

from core.config.settings import settings
from services.analytics.analytics_service import AnalyticsService, QueryEvent


def _event(
    user_id: str,
    query: str,
    *,
    latency_ms: float = 10,
    success: bool = True,
    error: str | None = None,
    error_type: str | None = None,
) -> QueryEvent:
    return QueryEvent(
        user_id=user_id,
        session_id=f"session-{user_id}",
        query=query,
        agent="general",
        model="test-model",
        latency_ms=latency_ms,
        success=success,
        error=error,
        error_type=error_type,
    )


def _set_text_policy(monkeypatch, *, production: bool, store_text: bool) -> None:
    monkeypatch.setattr(settings, "IS_PRODUCTION", production, raising=False)
    monkeypatch.setattr(
        settings, "ANALYTICS_STORE_QUERY_TEXT", store_text, raising=False
    )


def _record_process_batch(log_path: str, start: int, count: int) -> None:
    service = AnalyticsService(log_path, max_log_bytes=1024 * 1024)
    for index in range(start, start + count):
        service.record(_event("user-a", f"process query {index}"))


def test_summary_and_recent_are_strictly_tenant_scoped(tmp_path, monkeypatch):
    _set_text_policy(monkeypatch, production=False, store_text=True)
    log = tmp_path / "analytics.jsonl"
    service = AnalyticsService(log)

    # Records from before analytics ownership was added must fail closed.
    log.write_text(
        json.dumps(
            {
                "session_id": "legacy",
                "query": "legacy global secret",
                "agent": "general",
                "model": "old-model",
                "latency_ms": 5,
                "success": True,
                "timestamp": _event("unused", "unused").timestamp,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    service.record(_event("user-a", "private A one", latency_ms=10))
    service.record(
        _event(
            "user-a",
            "private A two",
            latency_ms=30,
            success=False,
            error="A-only error",
            error_type="RuntimeError",
        )
    )
    service.record(_event("user-b", "private B", latency_ms=999))

    summary_a = service.summary(user_id="user-a", since_hours=24)
    assert summary_a == {
        "total_queries": 2,
        "since_hours": 24,
        "success_rate": 0.5,
        "avg_latency_ms": 20.0,
        "p95_latency_ms": 30.0,
        "by_agent": {"general": 2},
        "by_model": {"test-model": 2},
    }
    assert service.summary(user_id="user-b")["total_queries"] == 1
    assert service.summary(user_id="missing") == {
        "total_queries": 0,
        "since_hours": 24,
    }

    recent_a = service.recent(user_id="user-a", n=10)
    assert [event["query"] for event in recent_a] == [
        "private A one",
        "private A two",
    ]
    assert all("user_id" not in event for event in recent_a)
    assert all("session_id" not in event for event in recent_a)
    assert all("error" not in event for event in recent_a)
    assert service.recent(user_id="user-a", n=0) == []


def test_production_never_persists_or_returns_raw_query_and_error(tmp_path, monkeypatch):
    # Production wins over an accidentally enabled text-retention setting.
    _set_text_policy(monkeypatch, production=True, store_text=True)
    log = tmp_path / "analytics.jsonl"
    service = AnalyticsService(log)
    query = "my confidential acquisition plan"
    error = "provider failed with secret customer@example.com"
    session_secret = "private-session-customer@example.com"

    event = _event(
        "user-a",
        query,
        success=False,
        error=error,
        error_type="ProviderAuthenticationError" * 10,
    )
    event.session_id = session_secret
    service.record(event)

    raw_log = log.read_text(encoding="utf-8")
    stored = json.loads(raw_log)
    assert query not in raw_log
    assert error not in raw_log
    assert session_secret not in raw_log
    assert "query" not in stored
    assert "error" not in stored
    assert "session_id" not in stored
    assert stored["session_hash"] == hashlib.sha256(session_secret.encode()).hexdigest()
    assert stored["query_hash"] == hashlib.sha256(query.encode()).hexdigest()
    assert stored["error_hash"] == hashlib.sha256(error.encode()).hexdigest()
    assert stored["query_chars"] == len(query)
    assert stored["error_chars"] == len(error)
    assert len(stored["error_type"]) <= 96

    assert service.recent(user_id="user-a") == [
        {
            "timestamp": stored["timestamp"],
            "agent": "general",
            "model": "test-model",
            "latency_ms": 10.0,
            "success": False,
            "query_chars": len(query),
            "query": "Content logging disabled",
            "error_type": stored["error_type"],
        }
    ]


def test_local_raw_text_is_bounded(tmp_path, monkeypatch):
    _set_text_policy(monkeypatch, production=False, store_text=True)
    log = tmp_path / "analytics.jsonl"
    service = AnalyticsService(log)
    service.record(
        _event(
            "user-a",
            "q" * 3_000,
            success=False,
            error="e" * 2_000,
            error_type="RuntimeError",
        )
    )

    stored = json.loads(log.read_text(encoding="utf-8"))
    assert len(stored["query"]) == 2_048
    assert stored["query_truncated"] is True
    assert len(stored["error"]) == 1_024
    assert stored["error_truncated"] is True


def test_concurrent_records_are_complete_valid_json(tmp_path, monkeypatch):
    _set_text_policy(monkeypatch, production=True, store_text=False)
    log = tmp_path / "analytics.jsonl"
    service = AnalyticsService(log, max_log_bytes=1024 * 1024)

    with ThreadPoolExecutor(max_workers=12) as executor:
        list(
            executor.map(
                lambda index: service.record(
                    _event("user-a", f"concurrent query {index}")
                ),
                range(120),
            )
        )

    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 120
    assert all(record["user_id"] == "user-a" for record in records)
    assert len({record["query_hash"] for record in records}) == 120


def test_file_lock_coordinates_multiple_writer_processes(tmp_path):
    log = tmp_path / "analytics.jsonl"
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=_record_process_batch,
            args=(str(log), worker * 20, 20),
        )
        for worker in range(4)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 80
    assert len({record["query_hash"] for record in records}) == 80


def test_log_rotation_bounds_retained_files(tmp_path, monkeypatch):
    _set_text_policy(monkeypatch, production=True, store_text=False)
    log = tmp_path / "analytics.jsonl"
    service = AnalyticsService(log, max_log_bytes=650, max_backups=2)

    for index in range(30):
        service.record(_event("user-a", f"rotating query {index}"))

    retained = sorted(tmp_path.glob("analytics.jsonl*"))
    data_files = [path for path in retained if path.name != "analytics.jsonl.lock"]
    assert {path.name for path in data_files} <= {
        "analytics.jsonl",
        "analytics.jsonl.1",
        "analytics.jsonl.2",
    }
    assert len(data_files) <= 3
    assert all(path.stat().st_size <= 650 for path in data_files)
    assert service.recent(user_id="user-a", n=1)[0]["query_chars"] == len(
        "rotating query 29"
    )


def test_analytics_routes_pass_current_user_and_bound_inputs(monkeypatch):
    from apps.api.routes import health as health_routes

    summary = Mock(return_value={"total_queries": 0, "since_hours": 8760})
    recent = Mock(return_value=[])
    monkeypatch.setattr(health_routes.analytics, "summary", summary)
    monkeypatch.setattr(health_routes.analytics, "recent", recent)
    monkeypatch.setattr(health_routes, "current_user_id", lambda: "route-owner")

    app = Flask(__name__)
    app.register_blueprint(health_routes.health_bp)
    with app.test_client() as client:
        assert client.get("/api/analytics/summary?since_hours=999999").status_code == 200
        assert client.get("/api/analytics/recent?n=999999").status_code == 200

    summary.assert_called_once_with(user_id="route-owner", since_hours=24 * 365)
    recent.assert_called_once_with(user_id="route-owner", n=100)


def test_workspace_analytics_summary_uses_requesting_user(monkeypatch):
    from domain.router import workspace_router as router_module

    summary = Mock(return_value={"total_queries": 7, "since_hours": 24})
    monkeypatch.setattr(router_module.analytics, "summary", summary)
    router = router_module.WorkspaceRouter()
    monkeypatch.setattr(
        router,
        "route",
        lambda query, user_id: {
            "action": "analytics_summary",
            "confidence": 1.0,
            "arguments": {},
            "router_model": "router-model",
            "router_source": "rules",
        },
    )

    result = router.handle("show analytics", session_id="session", user_id="owner-a")

    assert result["data"]["total_queries"] == 7
    summary.assert_called_once_with(user_id="owner-a", since_hours=24)


def test_query_event_rejects_unowned_records():
    try:
        _event("", "orphaned query")
    except ValueError as error:
        assert "user_id" in str(error)
    else:
        raise AssertionError("unowned analytics event should be rejected")
