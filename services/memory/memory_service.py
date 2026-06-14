import json
from datetime import datetime
from typing import Optional
from core.config.settings import settings

# Try Redis, fall back to in-process dict
try:
    import redis
    _redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
    _redis.ping()
    _USE_REDIS = True
except Exception:
    _redis = None
    _USE_REDIS = False

_fallback: dict[str, list[dict]] = {}
_PREFIX = "ai_platform:memory:"


class MemoryService:
    def add(self, session_id: str, role: str, content: str):
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat()
        }
        if _USE_REDIS:
            key = _PREFIX + session_id
            _redis.rpush(key, json.dumps(msg))
            _redis.ltrim(key, -settings.MAX_MEMORY_MESSAGES, -1)
            _redis.expire(key, settings.MEMORY_TTL)
        else:
            buf = _fallback.setdefault(session_id, [])
            buf.append(msg)
            _fallback[session_id] = buf[-settings.MAX_MEMORY_MESSAGES:]

    def get(self, session_id: str) -> list[dict]:
        if _USE_REDIS:
            raw = _redis.lrange(_PREFIX + session_id, 0, -1)
            return [json.loads(m) for m in raw]
        return list(_fallback.get(session_id, []))

    def to_llm_format(self, session_id: str) -> list[dict]:
        return [{"role": m["role"], "content": m["content"]} for m in self.get(session_id)]

    def clear(self, session_id: str):
        if _USE_REDIS:
            _redis.delete(_PREFIX + session_id)
        else:
            _fallback.pop(session_id, None)

    def list_sessions(self) -> list[str]:
        if _USE_REDIS:
            return [k.removeprefix(_PREFIX) for k in _redis.keys(_PREFIX + "*")]
        return list(_fallback.keys())


memory = MemoryService()
