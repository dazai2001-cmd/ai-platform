from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

from core.config.constants import ANALYTICS_LOG
from core.config.settings import settings


_PROCESS_LOCK = threading.RLock()
_MAX_USER_ID_CHARS = 256
_MAX_SESSION_ID_CHARS = 128
_MAX_AGENT_CHARS = 64
_MAX_MODEL_CHARS = 256
_MAX_ERROR_TYPE_CHARS = 96
_MAX_TIMESTAMP_CHARS = 64
_MAX_LATENCY_MS = 7 * 24 * 60 * 60 * 1_000
_MAX_LOCAL_QUERY_CHARS = 2_048
_MAX_LOCAL_ERROR_CHARS = 1_024
_DEFAULT_MAX_LOG_BYTES = 10 * 1024 * 1024
_DEFAULT_MAX_BACKUPS = 3


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _bounded(value: object, limit: int) -> str:
    return str(value or "")[:limit]


def _safe_latency(value: object) -> float:
    try:
        latency = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(latency):
        return 0.0
    return round(min(max(0.0, latency), _MAX_LATENCY_MS), 3)


def _safe_timestamp(value: object) -> str:
    candidate = _bounded(value, _MAX_TIMESTAMP_CHARS)
    try:
        datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return _utc_now().isoformat()
    return candidate


@dataclass
class QueryEvent:
    user_id: str
    session_id: str
    query: str
    agent: str
    model: str
    latency_ms: float
    success: bool = True
    error: Optional[str] = None
    error_type: Optional[str] = None
    timestamp: str = field(default_factory=lambda: _utc_now().isoformat())

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, str) or not self.user_id.strip():
            raise ValueError("Analytics events require a user_id")
        if len(self.user_id) > _MAX_USER_ID_CHARS:
            raise ValueError("Analytics user_id is too long")


class AnalyticsService:
    """Append-only, tenant-scoped query analytics with bounded local storage."""

    def __init__(
        self,
        log_path: str | Path = ANALYTICS_LOG,
        *,
        max_log_bytes: int = _DEFAULT_MAX_LOG_BYTES,
        max_backups: int = _DEFAULT_MAX_BACKUPS,
    ) -> None:
        self._log = Path(log_path)
        self._lock_file = self._log.with_name(f"{self._log.name}.lock")
        self._max_log_bytes = max(1, int(max_log_bytes))
        self._max_backups = max(0, int(max_backups))
        self._log.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _store_raw_text() -> bool:
        # Production must fail closed even if the opt-in flag is accidentally
        # enabled. Development can retain bounded text for the local dashboard.
        is_production = bool(getattr(settings, "IS_PRODUCTION", False))
        configured = bool(
            getattr(settings, "ANALYTICS_STORE_QUERY_TEXT", not is_production)
        )
        return configured and not is_production

    @staticmethod
    def _serialize(event: QueryEvent) -> dict:
        query = str(event.query or "")
        error = str(event.error or "")
        session_id = str(event.session_id or "")
        store_raw_text = AnalyticsService._store_raw_text()
        payload = {
            "user_id": event.user_id,
            "session_hash": _content_hash(session_id),
            "agent": _bounded(event.agent, _MAX_AGENT_CHARS),
            "model": _bounded(event.model, _MAX_MODEL_CHARS),
            "latency_ms": _safe_latency(event.latency_ms),
            "success": bool(event.success),
            "timestamp": _safe_timestamp(event.timestamp),
            "query_hash": _content_hash(query),
            "query_chars": len(query),
        }
        if error:
            payload.update(
                error_hash=_content_hash(error),
                error_chars=len(error),
                error_type=_bounded(event.error_type or "unknown", _MAX_ERROR_TYPE_CHARS),
            )
        if store_raw_text:
            payload["session_id"] = session_id[:_MAX_SESSION_ID_CHARS]
            payload["query"] = query[:_MAX_LOCAL_QUERY_CHARS]
            payload["query_truncated"] = len(query) > _MAX_LOCAL_QUERY_CHARS
            if error:
                payload["error"] = error[:_MAX_LOCAL_ERROR_CHARS]
                payload["error_truncated"] = len(error) > _MAX_LOCAL_ERROR_CHARS
        return payload

    def record(self, event: QueryEvent) -> None:
        payload = self._serialize(event)
        encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        self._log.parent.mkdir(parents=True, exist_ok=True)
        with self._exclusive_lock():
            self._rotate_if_needed(len(encoded))
            with self._log.open("ab") as handle:
                handle.write(encoded)
                handle.flush()

    def _load(self, *, user_id: str, since_hours: int = 24) -> list[dict]:
        if not isinstance(user_id, str) or not user_id:
            return []
        retention_hours = max(1, int(getattr(settings, "ANALYTICS_RETENTION_DAYS", 30))) * 24
        cutoff = _utc_now() - timedelta(hours=min(max(0, since_hours), retention_hours))
        events: list[dict] = []
        with self._exclusive_lock():
            for path in self._log_paths_oldest_first():
                if not path.exists():
                    continue
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        event = self._parse_owned_event(line, user_id=user_id, cutoff=cutoff)
                        if event is not None:
                            events.append(event)
        return events

    @staticmethod
    def _parse_owned_event(line: str, *, user_id: str, cutoff: datetime) -> dict | None:
        try:
            event = json.loads(line)
            # Legacy events did not have an owner. Excluding them is safer than
            # guessing that they belong to the current local/authenticated user.
            if not isinstance(event, dict) or event.get("user_id") != user_id:
                return None
            timestamp = datetime.fromisoformat(str(event["timestamp"]).replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            if timestamp.astimezone(timezone.utc) < cutoff:
                return None
            return event
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def summary(self, *, user_id: str, since_hours: int = 24) -> dict:
        events = self._load(user_id=user_id, since_hours=since_hours)
        if not events:
            return {"total_queries": 0, "since_hours": since_hours}

        latencies = [
            float(event.get("latency_ms", 0))
            for event in events
            if isinstance(event.get("latency_ms"), (int, float))
        ]
        by_agent: dict[str, int] = defaultdict(int)
        by_model: dict[str, int] = defaultdict(int)
        for event in events:
            by_agent[_bounded(event.get("agent", "unknown"), _MAX_AGENT_CHARS) or "unknown"] += 1
            by_model[_bounded(event.get("model", "unknown"), _MAX_MODEL_CHARS) or "unknown"] += 1

        result = {
            "total_queries": len(events),
            "since_hours": since_hours,
            "success_rate": round(
                sum(1 for event in events if event.get("success")) / len(events), 3
            ),
            "by_agent": dict(by_agent),
            "by_model": dict(by_model),
        }
        if latencies:
            ordered = sorted(latencies)
            p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
            result.update(
                avg_latency_ms=round(sum(ordered) / len(ordered), 1),
                p95_latency_ms=round(ordered[p95_index], 1),
            )
        return result

    def recent(self, *, user_id: str, n: int = 20) -> list[dict]:
        if n <= 0:
            return []
        events = self._load(user_id=user_id, since_hours=72)[-n:]
        return [self._public_event(event) for event in events]

    @staticmethod
    def _public_event(event: dict) -> dict:
        public = {
            "timestamp": event.get("timestamp"),
            "agent": event.get("agent"),
            "model": event.get("model"),
            "latency_ms": event.get("latency_ms"),
            "success": event.get("success", True),
            "query_chars": event.get("query_chars", 0),
            "query": event.get("query", "Content logging disabled"),
        }
        if event.get("query_truncated"):
            public["query_truncated"] = True
        if event.get("error_type"):
            public["error_type"] = event["error_type"]
        return public

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        current_size = self._log.stat().st_size if self._log.exists() else 0
        if current_size == 0 or current_size + incoming_bytes <= self._max_log_bytes:
            return
        if self._max_backups == 0:
            self._log.unlink(missing_ok=True)
            return

        oldest = self._backup_path(self._max_backups)
        oldest.unlink(missing_ok=True)
        for index in range(self._max_backups - 1, 0, -1):
            source = self._backup_path(index)
            if source.exists():
                source.replace(self._backup_path(index + 1))
        self._log.replace(self._backup_path(1))

    def _backup_path(self, index: int) -> Path:
        return self._log.with_name(f"{self._log.name}.{index}")

    def _log_paths_oldest_first(self) -> list[Path]:
        return [
            *(self._backup_path(index) for index in range(self._max_backups, 0, -1)),
            self._log,
        ]

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self._lock_file.parent.mkdir(parents=True, exist_ok=True)
        with _PROCESS_LOCK:
            with self._lock_file.open("a+b") as handle:
                self._lock_handle(handle)
                try:
                    yield
                finally:
                    self._unlock_handle(handle)

    @staticmethod
    def _lock_handle(handle) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    @staticmethod
    def _unlock_handle(handle) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


analytics = AnalyticsService(
    max_log_bytes=getattr(settings, "ANALYTICS_MAX_FILE_BYTES", _DEFAULT_MAX_LOG_BYTES),
)
