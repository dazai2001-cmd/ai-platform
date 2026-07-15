from __future__ import annotations

import time
import uuid

from services.storage.sqlite_service import db


class ConversationService:
    def create(self, title: str = "New chat", conversation_id: str = None, user_id: str = "local") -> dict | None:
        now = time.time()
        conversation_id = conversation_id or uuid.uuid4().hex
        db.execute(
            """
            INSERT INTO chat_conversations (id, user_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (id) DO NOTHING
            """,
            (conversation_id, user_id, title, now, now),
        )
        # Conversation IDs are globally unique. If a caller supplies an ID that
        # belongs to another user, the conflict no-op must not be represented as a
        # successful create for the caller.
        return self.get(conversation_id, user_id=user_id)

    def list(self, user_id: str = "local") -> list[dict]:
        rows = db.query(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at, COUNT(m.id) AS message_count
            FROM chat_conversations c
            LEFT JOIN chat_messages m ON m.conversation_id = c.id
            WHERE c.user_id = ?
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            """,
            (user_id,),
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

    def get(self, conversation_id: str, user_id: str = "local") -> dict | None:
        conversation = db.query_one(
            "SELECT id, title, created_at, updated_at FROM chat_conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        if not conversation:
            return None
        return {
            "id": conversation["id"],
            "title": conversation["title"],
            "messages": self.messages(conversation_id, user_id=user_id),
            "createdAt": conversation["created_at"],
            "updatedAt": conversation["updated_at"],
        }

    def save_messages(self, conversation_id: str, title: str, messages: list[dict], user_id: str = "local") -> dict | None:
        existing = self.get(conversation_id, user_id=user_id)
        if not existing:
            existing = self.create(title=title, conversation_id=conversation_id, user_id=user_id)
        if not existing:
            return None

        now = time.time()
        statements = [
            (
                """
                DELETE FROM chat_messages
                WHERE conversation_id IN (
                  SELECT id FROM chat_conversations WHERE id = ? AND user_id = ?
                )
                """,
                (conversation_id, user_id),
            ),
            (
                "UPDATE chat_conversations SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (title or "New chat", now, conversation_id, user_id),
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
        return self.get(conversation_id, user_id=user_id)

    def messages(self, conversation_id: str, user_id: str = "local") -> list[dict]:
        rows = db.query(
            """
            SELECT m.role, m.content, m.route, m.model, m.sources_json, m.chart_json, m.sql_text, m.rows_json
            FROM chat_messages m
            JOIN chat_conversations c ON c.id = m.conversation_id
            WHERE m.conversation_id = ? AND c.user_id = ?
            ORDER BY m.id ASC
            """,
            (conversation_id, user_id),
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

    def delete(self, conversation_id: str, user_id: str = "local"):
        existing = self.get(conversation_id, user_id=user_id)
        if not existing:
            return
        db.execute_many([
            (
                """
                DELETE FROM chat_messages
                WHERE conversation_id IN (
                  SELECT id FROM chat_conversations WHERE id = ? AND user_id = ?
                )
                """,
                (conversation_id, user_id),
            ),
            ("DELETE FROM chat_conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)),
        ])


conversations = ConversationService()
