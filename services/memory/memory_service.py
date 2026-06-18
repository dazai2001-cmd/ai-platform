from datetime import datetime

from core.config.settings import settings
from services.storage.sqlite_service import db


class MemoryService:
    def add(self, session_id: str, role: str, content: str):
        timestamp = datetime.utcnow().isoformat()
        db.execute(
            "INSERT INTO memory_messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (session_id, role, content, timestamp),
        )

        rows = db.query(
            "SELECT id FROM memory_messages WHERE session_id = ? ORDER BY id DESC",
            (session_id,),
        )
        stale_ids = [row["id"] for row in rows[settings.MAX_MEMORY_MESSAGES:]]
        if stale_ids:
            placeholders = ",".join("?" for _ in stale_ids)
            db.execute(f"DELETE FROM memory_messages WHERE id IN ({placeholders})", tuple(stale_ids))

    def get(self, session_id: str) -> list[dict]:
        rows = db.query(
            """
            SELECT role, content, timestamp
            FROM memory_messages
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        )
        return rows

    def to_llm_format(self, session_id: str) -> list[dict]:
        return [{"role": m["role"], "content": m["content"]} for m in self.get(session_id)]

    def clear(self, session_id: str):
        db.execute("DELETE FROM memory_messages WHERE session_id = ?", (session_id,))

    def list_sessions(self) -> list[str]:
        rows = db.query("SELECT DISTINCT session_id FROM memory_messages ORDER BY session_id")
        return [row["session_id"] for row in rows]

    def session_summaries(self) -> list[dict]:
        summaries = []
        for session_id in self.list_sessions():
            messages = self.get(session_id)
            last = messages[-1] if messages else {}
            summaries.append({
                "session_id": session_id,
                "messages": len(messages),
                "last_role": last.get("role"),
                "last_message": last.get("content", "")[:160],
                "updated_at": last.get("timestamp"),
            })
        return sorted(summaries, key=lambda s: s.get("updated_at") or "", reverse=True)

    def add_fact(self, content: str) -> dict:
        fact = {
            "id": datetime.utcnow().strftime("%Y%m%d%H%M%S%f"),
            "content": content,
            "timestamp": datetime.utcnow().isoformat(),
        }
        db.execute(
            "INSERT INTO memory_facts (id, content, timestamp) VALUES (?, ?, ?)",
            (fact["id"], fact["content"], fact["timestamp"]),
        )
        return fact

    def facts(self) -> list[dict]:
        return db.query("SELECT id, content, timestamp FROM memory_facts ORDER BY timestamp ASC")

    def delete_fact(self, fact_id: str):
        db.execute("DELETE FROM memory_facts WHERE id = ?", (fact_id,))

    def facts_text(self) -> str:
        facts = [fact.get("content", "") for fact in self.facts() if fact.get("content")]
        return "\n".join(f"- {fact}" for fact in facts)


memory = MemoryService()
