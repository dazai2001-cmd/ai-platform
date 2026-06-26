from datetime import datetime

from core.config.settings import settings
from services.storage.sqlite_service import db


class MemoryService:
    def add(self, session_id: str, role: str, content: str, user_id: str = "local"):
        timestamp = datetime.utcnow().isoformat()
        db.execute(
            "INSERT INTO memory_messages (user_id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
            (user_id, session_id, role, content, timestamp),
        )

        rows = db.query(
            "SELECT id FROM memory_messages WHERE user_id = ? AND session_id = ? ORDER BY id DESC",
            (user_id, session_id),
        )
        stale_ids = [row["id"] for row in rows[settings.MAX_MEMORY_MESSAGES:]]
        if stale_ids:
            placeholders = ",".join("?" for _ in stale_ids)
            db.execute(f"DELETE FROM memory_messages WHERE id IN ({placeholders})", tuple(stale_ids))

    def get(self, session_id: str, user_id: str = "local") -> list[dict]:
        rows = db.query(
            """
            SELECT role, content, timestamp
            FROM memory_messages
            WHERE user_id = ? AND session_id = ?
            ORDER BY id ASC
            """,
            (user_id, session_id),
        )
        return rows

    def to_llm_format(self, session_id: str, user_id: str = "local") -> list[dict]:
        return [{"role": m["role"], "content": m["content"]} for m in self.get(session_id, user_id=user_id)]

    def clear(self, session_id: str, user_id: str = "local"):
        db.execute("DELETE FROM memory_messages WHERE user_id = ? AND session_id = ?", (user_id, session_id))

    def list_sessions(self, user_id: str = "local") -> list[str]:
        rows = db.query("SELECT DISTINCT session_id FROM memory_messages WHERE user_id = ? ORDER BY session_id", (user_id,))
        return [row["session_id"] for row in rows]

    def session_summaries(self, user_id: str = "local") -> list[dict]:
        summaries = []
        for session_id in self.list_sessions(user_id=user_id):
            messages = self.get(session_id, user_id=user_id)
            last = messages[-1] if messages else {}
            summaries.append({
                "session_id": session_id,
                "messages": len(messages),
                "last_role": last.get("role"),
                "last_message": last.get("content", "")[:160],
                "updated_at": last.get("timestamp"),
            })
        return sorted(summaries, key=lambda s: s.get("updated_at") or "", reverse=True)

    def add_fact(self, content: str, user_id: str = "local") -> dict:
        fact = {
            "id": datetime.utcnow().strftime("%Y%m%d%H%M%S%f"),
            "content": content,
            "timestamp": datetime.utcnow().isoformat(),
        }
        db.execute(
            "INSERT INTO memory_facts (id, user_id, content, timestamp) VALUES (?, ?, ?, ?)",
            (fact["id"], user_id, fact["content"], fact["timestamp"]),
        )
        return fact

    def facts(self, user_id: str = "local") -> list[dict]:
        return db.query("SELECT id, content, timestamp FROM memory_facts WHERE user_id = ? ORDER BY timestamp ASC", (user_id,))

    def delete_fact(self, fact_id: str, user_id: str = "local"):
        db.execute("DELETE FROM memory_facts WHERE id = ? AND user_id = ?", (fact_id, user_id))

    def facts_text(self, user_id: str = "local") -> str:
        facts = [fact.get("content", "") for fact in self.facts(user_id=user_id) if fact.get("content")]
        return "\n".join(f"- {fact}" for fact in facts)


memory = MemoryService()
