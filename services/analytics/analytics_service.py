import json
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from core.config.constants import ANALYTICS_LOG


@dataclass
class QueryEvent:
    session_id: str
    query: str
    agent: str
    model: str
    latency_ms: float
    success: bool = True
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class AnalyticsService:
    def __init__(self):
        self._log = Path(ANALYTICS_LOG)
        self._log.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: QueryEvent):
        with open(self._log, "a") as f:
            f.write(json.dumps(asdict(event)) + "\n")

    def _load(self, since_hours: int = 24) -> list[dict]:
        if not self._log.exists():
            return []
        cutoff = datetime.utcnow() - timedelta(hours=since_hours)
        events = []
        with open(self._log) as f:
            for line in f:
                try:
                    ev = json.loads(line)
                    if datetime.fromisoformat(ev["timestamp"]) >= cutoff:
                        events.append(ev)
                except Exception:
                    continue
        return events

    def summary(self, since_hours: int = 24) -> dict:
        events = self._load(since_hours)
        if not events:
            return {"total_queries": 0, "since_hours": since_hours}
        latencies = [e["latency_ms"] for e in events]
        by_agent: dict = defaultdict(int)
        by_model: dict = defaultdict(int)
        for e in events:
            by_agent[e["agent"]] += 1
            by_model[e["model"]] += 1
        return {
            "total_queries": len(events),
            "since_hours": since_hours,
            "success_rate": round(sum(1 for e in events if e.get("success")) / len(events), 3),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1),
            "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 1),
            "by_agent": dict(by_agent),
            "by_model": dict(by_model),
        }

    def recent(self, n: int = 20) -> list[dict]:
        return self._load(since_hours=72)[-n:]


analytics = AnalyticsService()
