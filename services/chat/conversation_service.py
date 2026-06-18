from __future__ import annotations

import time
import uuid

from services.storage.sqlite_service import db


class ConversationService:
    def create(self, title: str = "New chat", conversation_id: str = None) -> dict:
        now = time.time()
        conversation_id = conversation_id or uuid.uuid4().hex
        db.execute(
            """
            INSERT OR IGNORE INTO chat_conversations (id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, title, now, now),
        )
        return self.get(conversation_id) or {
            "id": conversation_id,
            "title": title,
            "messages": [],
            "createdAt": now,
            "updatedAt": now,
        }

    def list(self) -> list[dict]:
        rows = db.query(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at, COUNT(m.id) AS message_count
            FROM chat_conversations c
            LEFT JOIN chat_messages m ON m.conversation_id = c.id
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            """
        )
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "messages": int(row["message_count"] or 0),
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def get(self, conversation_id: str) -> dict | None:
        conversation = db.query_one(
            "SELECT id, title, created_at, updated_at FROM chat_conversations WHERE id = ?",
            (conversation_id,),
        )
        if not conversation:
            return None
        return {
            "id": conversation["id"],
            "title": conversation["title"],
            "messages": self.messages(conversation_id),
            "createdAt": conversation["created_at"],
            "updatedAt": conversation["updated_at"],
        }

    def save_messages(self, conversation_id: str, title: str, messages: list[dict]) -> dict:
        existing = self.get(conversation_id)
        if not existing:
            self.create(title=title, conversation_id=conversation_id)

        now = time.time()
        statements = [
            ("DELETE FROM chat_messages WHERE conversation_id = ?", (conversation_id,)),
            (
                "UPDATE chat_conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title or "New chat", now, conversation_id),
            ),
        ]
        for message in messages:
            statements.append((
                """
                INSERT INTO chat_messages
                (conversation_id, role, content, route, model, sources_json, chart_json, sql_text, rows_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    message.get("role", "assistant"),
                    message.get("content", ""),
                    message.get("route"),
                    message.get("model"),
                    db.dumps(message.get("sources")),
                    db.dumps(message.get("chart")),
                    message.get("sql"),
                    db.dumps(message.get("rows")),
                    now,
                ),
            ))
        db.execute_many(statements)
        return self.get(conversation_id)

    def messages(self, conversation_id: str) -> list[dict]:
        rows = db.query(
            """
            SELECT role, content, route, model, sources_json, chart_json, sql_text, rows_json
            FROM chat_messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (conversation_id,),
        )
        return [
            {
                "role": row["role"],
                "content": row["content"],
                "route": row["route"],
                "model": row["model"],
                "sources": db.loads(row["sources_json"], []),
                "chart": db.loads(row["chart_json"], None),
                "sql": row["sql_text"],
                "rows": db.loads(row["rows_json"], None),
            }
            for row in rows
        ]

    def delete(self, conversation_id: str):
        db.execute_many([
            ("DELETE FROM chat_messages WHERE conversation_id = ?", (conversation_id,)),
            ("DELETE FROM chat_conversations WHERE id = ?", (conversation_id,)),
        ])


conversations = ConversationService()
