import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from core.config.settings import settings
from services.analytics.analytics_service import AnalyticsService, QueryEvent
from services.storage.sqlite_service import SQLiteService


def event(owner, question="Private question", **kwargs):
    return QueryEvent(user_id=owner, session_id="private-session", query=question, agent="rag", model="test-model", latency_ms=10, **kwargs)


def test_database_analytics_survives_service_recreation_and_keeps_owners_private(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "IS_PRODUCTION", True)
    monkeypatch.setattr(settings, "ANALYTICS_STORE_QUERY_TEXT", True)
    database_path = str(tmp_path / "app.db")
    log = tmp_path / "absent.jsonl"
    first = AnalyticsService(log, database=SQLiteService(database_path))
    first.record(event("owner-a"))
    first.record(event("owner-a", success=False, error="Secret provider details", error_type="RuntimeError"))
    first.record(event("owner-b", "Another private question"))

    second = AnalyticsService(log, database=SQLiteService(database_path))
    summary = second.summary(user_id="owner-a")
    assert summary["total_queries"] == 2
    assert summary["by_agent"] == {"rag": 2}
    assert summary["success_rate"] == 0.5
    assert second.summary(user_id="owner-b")["total_queries"] == 1
    assert second.summary(user_id="owner-a' OR 1=1 --")["total_queries"] == 0
    assert second.recent(user_id="owner-a")[-1]["error_type"] == "RuntimeError"
    stored = second._database.query("SELECT payload_json FROM analytics_events")
    assert "Private question" not in json.dumps(stored)
    assert "Secret provider details" not in json.dumps(stored)
    assert "private-session" not in json.dumps(stored)
    assert not log.exists()


def test_database_retention_prunes_only_the_recording_owners_old_events(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ANALYTICS_RETENTION_DAYS", 1)
    database = SQLiteService(str(tmp_path / "app.db"))
    service = AnalyticsService(tmp_path / "absent.jsonl", database=database)
    old = datetime.now(timezone.utc) - timedelta(days=2)
    for owner in ("owner-a", "owner-b"):
        payload = service._serialize(event(owner, timestamp=old.isoformat()))
        database.execute("INSERT INTO analytics_events (id,user_id,occurred_at,payload_json) VALUES (?,?,?,?)", (owner, owner, old.timestamp(), json.dumps(payload)))
    service.record(event("owner-a"))
    assert database.query_one("SELECT COUNT(*) AS n FROM analytics_events WHERE user_id=?", ("owner-a",))["n"] == 1
    assert database.query_one("SELECT COUNT(*) AS n FROM analytics_events WHERE user_id=?", ("owner-b",))["n"] == 1
    assert service.summary(user_id="owner-b")["total_queries"] == 0


def test_independent_database_analytics_workers_share_every_event(tmp_path):
    database_path = str(tmp_path / "app.db")
    workers = [AnalyticsService(tmp_path / "absent.jsonl", database=SQLiteService(database_path)) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: workers[i % 2].record(event("owner", f"Query {i}")), range(20)))
    assert workers[0].summary(user_id="owner")["total_queries"] == 20
    assert workers[1].summary(user_id="owner")["total_queries"] == 20


def test_existing_owned_file_events_remain_visible_alongside_new_database_events(tmp_path):
    log = tmp_path / "old.jsonl"
    log.write_text(json.dumps(AnalyticsService._serialize(event("owner"))) + "\n" + json.dumps({"query":"unowned legacy secret"}) + "\n", encoding="utf-8")
    service = AnalyticsService(log, database=SQLiteService(str(tmp_path / "app.db")))
    original = log.read_text(encoding="utf-8")
    service.record(event("owner", "New question"))
    assert service.summary(user_id="owner")["total_queries"] == 2
    assert service.summary(user_id="other")["total_queries"] == 0
    assert log.read_text(encoding="utf-8") == original
