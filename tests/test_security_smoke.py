import io
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from werkzeug.datastructures import FileStorage

from apps.api.deps import save_upload
from application.ingestion.ingestion_service import IngestionService
from application.ingestion.chunker import chunk_text
from domain.bi.pipeline import BIPipeline, _PROMPT
from domain.router.router import QueryRouter
from core.config.settings import settings
from services.career.career_service import CareerService
from services.auth.auth_service import AuthError, auth_service
from services.chat.conversation_service import conversations
from services.memory.memory_service import memory
from infrastructure.llm.ollama_client import ollama


class UploadValidationTests(unittest.TestCase):
    def test_rejects_fake_pdf(self):
        upload = FileStorage(
            stream=io.BytesIO(b"not a pdf"),
            filename="notes.pdf",
            content_type="application/pdf",
        )

        with self.assertRaisesRegex(ValueError, "Invalid PDF"):
            save_upload(upload, {"pdf"})


class ChunkingTests(unittest.TestCase):
    def test_short_note_is_kept(self):
        self.assertEqual(chunk_text("my name is rahul"), ["my name is rahul"])


class UrlValidationTests(unittest.TestCase):
    def test_rejects_localhost_url(self):
        with self.assertRaisesRegex(ValueError, "Local URLs"):
            IngestionService._validate_public_url("http://localhost:5000/private")

    def test_rejects_private_ip_url(self):
        with self.assertRaisesRegex(ValueError, "Private"):
            IngestionService._validate_public_url("http://127.0.0.1:5000/private")


class BIPipelineValidationTests(unittest.TestCase):
    def test_bi_prompt_formats_with_json_example(self):
        prompt = _PROMPT.format(schema="Columns:\n  value (int64)", sample="value\n1", question="Show values")
        self.assertIn('"chart_type": "bar"', prompt)
        self.assertNotIn('"values": [10, 20, 30]', prompt)
        self.assertIn("do not use `SUM()`", prompt)
        self.assertIn("Generate SQLite SQL only", prompt)
        self.assertIn("Show values", prompt)

    def test_dataset_names_are_sql_safe(self):
        self.assertEqual(BIPipeline._validate_name("sales_2026"), "sales_2026")
        with self.assertRaisesRegex(ValueError, "Dataset name"):
            BIPipeline._validate_name("sales-2026")

    def test_sql_must_be_single_select(self):
        BIPipeline._validate_select_sql("SELECT * FROM sales")
        with self.assertRaisesRegex(ValueError, "Only SELECT"):
            BIPipeline._validate_select_sql("DROP TABLE sales")
        with self.assertRaisesRegex(ValueError, "Only one"):
            BIPipeline._validate_select_sql("SELECT * FROM sales; SELECT * FROM other")

    def test_chart_data_is_built_from_query_rows(self):
        rows = [
            {"Timestamp": "00:00", "DE": 10.0, "Total": 20.0},
            {"Timestamp": "00:15", "DE": 11.0, "Total": 22.0},
        ]
        chart = BIPipeline._build_chart(
            rows,
            {"chart_type": "bar", "title": "DE vs Total"},
            "Compare DE and total",
        )
        self.assertEqual(chart["data"]["labels"], ["00:00", "00:15"])
        self.assertEqual(chart["data"]["series"][0], {"name": "DE", "values": [10.0, 11.0]})
        self.assertEqual(chart["data"]["series"][1], {"name": "Total", "values": [20.0, 22.0]})

    def test_bi_answer_does_not_expose_generation_blocks(self):
        response = "Here is the SQL query:\n```sql\nSELECT * FROM dataset\n```\n```json\n{\"chart_type\":\"bar\"}\n```"
        cleaned = BIPipeline._clean_answer(response)
        self.assertNotIn("SELECT", cleaned)
        self.assertNotIn("chart_type", cleaned)

    def test_postgres_extract_is_normalized_for_sqlite(self):
        sql = (
            "SELECT EXTRACT(HOUR FROM Timestamp) AS hour, AVG(DE) AS avg_DE "
            "FROM dataset GROUP BY EXTRACT(HOUR FROM Timestamp)"
        )
        normalized = BIPipeline._normalize_sqlite_sql(sql)
        expected = "CAST(strftime('%H', Timestamp) AS INTEGER)"
        self.assertEqual(normalized.count(expected), 2)
        self.assertNotIn("EXTRACT", normalized.upper())


class ModelRoutingTests(unittest.TestCase):
    def test_model_selection_has_one_task_map(self):
        for task, model in settings.TASK_MODELS.items():
            self.assertEqual(QueryRouter.model_for_type(task), model)

    def test_unknown_task_uses_general_model(self):
        self.assertEqual(
            QueryRouter.model_for_type("unknown"),
            settings.TASK_MODELS["general"],
        )

    def test_career_service_reads_current_task_model(self):
        original = dict(settings.TASK_MODELS)
        try:
            service = CareerService()
            with patch("services.career.career_service.ollama.generate", return_value='{"fit_score": 80}') as generate:
                settings.TASK_MODELS["career"] = "mistral:latest"
                first = service.analyze_fit("TypeScript project", "React role")
                settings.TASK_MODELS["career"] = "llama3:latest"
                second = service.analyze_fit("TypeScript project", "React role")

            self.assertEqual(first["model"], "mistral:latest")
            self.assertEqual(second["model"], "llama3:latest")
            self.assertEqual(generate.call_args_list[0].args[0], "mistral:latest")
            self.assertEqual(generate.call_args_list[1].args[0], "llama3:latest")
        finally:
            settings.TASK_MODELS.clear()
            settings.TASK_MODELS.update(original)


class CloudProviderTests(unittest.TestCase):
    def test_gemini_generate_uses_generate_content_api(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "hello"}]}}]}

        with patch.object(settings, "GEMINI_API_KEY", "test-key"), patch("infrastructure.llm.ollama_client.requests.post", return_value=Response()) as post:
            answer = ollama.generate("gemini:gemini-2.0-flash", "Say hello", max_tokens=12)

        self.assertEqual(answer, "hello")
        self.assertIn("/models/gemini-2.0-flash:generateContent", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["params"]["key"], "test-key")
        self.assertEqual(post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"], "Say hello")

    def test_openrouter_generate_uses_chat_completions_api(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "hi"}}]}

        with patch.object(settings, "OPENROUTER_API_KEY", "test-key"), patch("infrastructure.llm.ollama_client.requests.post", return_value=Response()) as post:
            answer = ollama.generate("openrouter:google/gemini-2.0-flash-exp:free", "Say hi", max_tokens=12)

        self.assertEqual(answer, "hi")
        self.assertEqual(post.call_args.args[0], f"{settings.OPENROUTER_BASE_URL}/chat/completions")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "google/gemini-2.0-flash-exp:free")


class AuthFlowTests(unittest.TestCase):
    def test_email_verification_is_required_before_login(self):
        email = f"auth-{uuid.uuid4().hex}@example.com"
        password = "good-password-123"

        created = auth_service.create_user(email, password)
        self.assertEqual(created["user"]["email"], email)
        self.assertFalse(created["user"]["email_verified"])
        self.assertIn("verification_token", created)

        with self.assertRaisesRegex(AuthError, "verify your email"):
            auth_service.login(email, password)

        verified = auth_service.verify_email(created["verification_token"])
        self.assertTrue(verified["user"]["email_verified"])

        session = auth_service.login(email, password)
        self.assertEqual(session["user"]["email"], email)
        self.assertTrue(session["token"])
        self.assertEqual(auth_service.authenticate_token(session["token"])["email"], email)

        auth_service.logout(session["token"])
        self.assertIsNone(auth_service.authenticate_token(session["token"]))


class UserIsolationTests(unittest.TestCase):
    def test_chat_and_memory_are_scoped_by_user(self):
        user_a = f"user-a-{uuid.uuid4().hex}"
        user_b = f"user-b-{uuid.uuid4().hex}"
        conversation_id = f"conv-{uuid.uuid4().hex}"
        session_id = f"session-{uuid.uuid4().hex}"

        conversations.create("Private A", conversation_id=conversation_id, user_id=user_a)
        conversations.save_messages(
            conversation_id,
            "Private A",
            [{"role": "user", "content": "secret from A"}],
            user_id=user_a,
        )
        memory.add(session_id, "user", "remember A", user_id=user_a)
        fact = memory.add_fact("A likes Qwen", user_id=user_a)

        self.assertIsNotNone(conversations.get(conversation_id, user_id=user_a))
        self.assertIsNone(conversations.get(conversation_id, user_id=user_b))
        self.assertEqual(len(memory.get(session_id, user_id=user_a)), 1)
        self.assertEqual(memory.get(session_id, user_id=user_b), [])
        self.assertEqual(len(memory.facts(user_id=user_a)), 1)
        self.assertEqual(memory.facts(user_id=user_b), [])

        memory.delete_fact(fact["id"], user_id=user_b)
        self.assertEqual(len(memory.facts(user_id=user_a)), 1)


class ApiRoutingBoundaryTests(unittest.TestCase):
    root = Path(__file__).parents[1]

    def test_auto_chat_stream_dispatches_by_route_type(self):
        source = (self.root / "apps/api/routes/chat.py").read_text(encoding="utf-8")
        self.assertIn("if task_type == TASK_RAG:", source)
        self.assertIn("elif task_type == TASK_BI:", source)
        self.assertIn("general_agent.stream_ask", source)
        self.assertIn('response.headers["X-Model"] = selected_model', source)

    def test_dedicated_rag_endpoint_does_not_use_global_router_model(self):
        source = (self.root / "apps/api/routes/rag.py").read_text(encoding="utf-8")
        self.assertNotIn("router.route", source)
        self.assertIn("rag_agent.ask(question, session_id=session_id, model=model_settings.model_for", source)
        self.assertIn('response.headers["X-Route"] = TASK_RAG', source)

    def test_dedicated_bi_endpoint_does_not_use_global_router_model(self):
        source = (self.root / "apps/api/routes/bi.py").read_text(encoding="utf-8")
        self.assertNotIn("router.route", source)
        self.assertIn("model=model_settings.model_for", source)
        self.assertIn("user_id=user_id", source)
        self.assertIn('result["route"] = TASK_BI', source)


if __name__ == "__main__":
    unittest.main()
