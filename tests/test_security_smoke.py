import io
import os
import requests
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

import pytest

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
from services.storage.sqlite_service import SQLiteService, db
from infrastructure.llm.ollama_client import ollama


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """Keep legacy service-level smoke tests out of the application database."""
    isolated_db = SQLiteService(str(tmp_path / "app.db"))
    monkeypatch.setattr(db, "path", isolated_db.path)


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
        BIPipeline._validate_select_sql("SELECT * FROM dataset")
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

    def test_career_json_workflows_request_structured_output(self):
        service = CareerService()
        with patch(
            "services.career.career_service.ollama.generate",
            return_value="{}",
        ) as generate:
            service.analyze_fit("Python experience", "Python role")
            service.tailor_cv("Python experience", "Python role")
            service.draft_cover_letter("Python experience", "Python role")

        self.assertEqual(generate.call_count, 3)
        self.assertTrue(all(call.kwargs["json_format"] for call in generate.call_args_list))


class CloudProviderTests(unittest.TestCase):
    def test_settings_import_in_cloud_runtime(self):
        env = {
            **os.environ,
            "AI_RUNTIME": "cloud",
            "GEMINI_API_KEY": "test-key",
            "GEMINI_MODELS": "gemini-3.5-flash",
        }
        result = subprocess.run(
            [sys.executable, "-c", "from core.config.settings import settings; print(settings.TASK_MODELS['general'])"],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "gemini:gemini-3.5-flash")

    def test_gemini_generate_uses_generate_content_api(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "hello"}]}}]}

        with patch.object(settings, "GEMINI_API_KEY", "test-key"), patch("infrastructure.llm.ollama_client.requests.post", return_value=Response()) as post:
            answer = ollama.generate("gemini:gemini-3.5-flash", "Say hello", max_tokens=12)

        self.assertEqual(answer, "hello")
        self.assertIn("/models/gemini-3.5-flash:generateContent", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["headers"]["x-goog-api-key"], "test-key")
        self.assertNotIn("test-key", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"], "Say hello")

    def test_gemini_retries_internal_label_and_ignores_thought_parts(self):
        class Response:
            def __init__(self, parts):
                self.parts = parts

            def raise_for_status(self):
                return None

            def json(self):
                return {"candidates": [{"content": {"parts": self.parts}}]}

        responses = [
            Response([{"text": "User Safety: safe"}]),
            Response([
                {"text": "Internal analysis", "thought": True},
                {"text": "Hey! How can I help?"},
            ]),
        ]
        with (
            patch.object(settings, "GEMINI_API_KEY", "test-key"),
            patch("infrastructure.llm.ollama_client.requests.post", side_effect=responses) as post,
        ):
            answer = ollama.generate("gemini:gemini-3.5-flash", "Say hello")

        self.assertEqual(answer, "Hey! How can I help?")
        self.assertEqual(post.call_count, 2)
        retry_prompt = post.call_args_list[1].kwargs["json"]["contents"][0]["parts"][0]["text"]
        self.assertIn("Return only the user-facing answer", retry_prompt)

    def test_openrouter_generate_uses_chat_completions_api(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "hi"}}]}

        model = "google/gemini-2.0-flash-exp:free"
        with (
            patch.object(settings, "OPENROUTER_API_KEY", "test-key"),
            patch.object(settings, "OPENROUTER_MODELS", [model]),
            patch("infrastructure.llm.ollama_client.requests.post", return_value=Response()) as post,
        ):
            answer = ollama.generate(f"openrouter:{model}", "Say hi", max_tokens=12)

        self.assertEqual(answer, "hi")
        self.assertEqual(post.call_args.args[0], f"{settings.OPENROUTER_BASE_URL}/chat/completions")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(post.call_args.kwargs["json"]["model"], model)

    def test_openrouter_retries_structured_output_without_provider_json_mode(self):
        class UnsupportedJsonResponse:
            status_code = 400

            def raise_for_status(self):
                raise requests.exceptions.HTTPError(response=self)

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": '{"fit_score": 90}'}}]}

        with (
            patch.object(settings, "OPENROUTER_API_KEY", "test-key"),
            patch.object(settings, "OPENROUTER_MODELS", ["openrouter/free"]),
            patch(
                "infrastructure.llm.ollama_client.requests.post",
                side_effect=[UnsupportedJsonResponse(), Response()],
            ) as post,
        ):
            answer = ollama.generate(
                "openrouter:openrouter/free",
                "Return JSON",
                json_format=True,
            )

        self.assertEqual(answer, '{"fit_score": 90}')
        self.assertIn("response_format", post.call_args_list[0].kwargs["json"])
        self.assertNotIn("response_format", post.call_args_list[1].kwargs["json"])

    def test_openrouter_retries_transport_failures(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "retry ok"}}]}

        with (
            patch.object(settings, "OPENROUTER_API_KEY", "test-key"),
            patch.object(settings, "OPENROUTER_MODELS", ["openrouter/free"]),
            patch(
                "infrastructure.llm.ollama_client.requests.post",
                side_effect=[requests.exceptions.Timeout("provider timed out"), Response()],
            ) as post,
        ):
            answer = ollama.generate("openrouter:openrouter/free", "hello")

        self.assertEqual(answer, "retry ok")
        self.assertEqual(post.call_count, 2)

    def test_gemini_rate_limit_falls_back_to_openrouter(self):
        class RateLimitResponse:
            status_code = 429

            def raise_for_status(self):
                raise requests.exceptions.HTTPError(response=self)

        class OpenRouterResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "fallback ok"}}]}

        with (
            patch.object(settings, "GEMINI_API_KEY", "gemini-key"),
            patch.object(settings, "OPENROUTER_API_KEY", "openrouter-key"),
            patch.object(settings, "OPENROUTER_MODELS", ["google/gemini-2.0-flash-exp:free"]),
            patch("infrastructure.llm.ollama_client.requests.post", side_effect=[RateLimitResponse(), OpenRouterResponse()]) as post,
        ):
            answer = ollama.generate("gemini:gemini-3.5-flash", "hello")

        self.assertEqual(answer, "fallback ok")
        self.assertEqual(post.call_args_list[1].kwargs["json"]["model"], "google/gemini-2.0-flash-exp:free")

    def test_gemini_service_outage_falls_back_to_openrouter(self):
        class ServiceUnavailableResponse:
            status_code = 503

            def raise_for_status(self):
                raise requests.exceptions.HTTPError(response=self)

        class OpenRouterResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "fallback ok"}}]}

        with (
            patch.object(settings, "GEMINI_API_KEY", "gemini-key"),
            patch.object(settings, "OPENROUTER_API_KEY", "openrouter-key"),
            patch.object(settings, "OPENROUTER_MODELS", ["openrouter/free"]),
            patch(
                "infrastructure.llm.ollama_client.requests.post",
                side_effect=[ServiceUnavailableResponse(), OpenRouterResponse()],
            ) as post,
        ):
            answer = ollama.generate("gemini:gemini-3.5-flash", "hello")

        self.assertEqual(answer, "fallback ok")
        self.assertEqual(post.call_args_list[1].kwargs["json"]["model"], "openrouter/free")

    def test_gemini_transport_failure_falls_back_to_openrouter(self):
        class OpenRouterResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "fallback ok"}}]}

        with (
            patch.object(settings, "GEMINI_API_KEY", "gemini-key"),
            patch.object(settings, "OPENROUTER_API_KEY", "openrouter-key"),
            patch.object(settings, "OPENROUTER_MODELS", ["openrouter/free"]),
            patch(
                "infrastructure.llm.ollama_client.requests.post",
                side_effect=[requests.exceptions.Timeout("provider timed out"), OpenRouterResponse()],
            ) as post,
        ):
            answer = ollama.generate("gemini:gemini-3.5-flash", "hello")

        self.assertEqual(answer, "fallback ok")
        self.assertEqual(post.call_args_list[1].kwargs["json"]["model"], "openrouter/free")

    def test_provider_errors_do_not_expose_api_keys(self):
        class Response:
            status_code = 429

            def raise_for_status(self):
                raise requests.exceptions.HTTPError(
                    "429 Client Error: Too Many Requests for url: https://example.test?key=secret-key",
                    response=self,
                )

        with (
            patch.object(settings, "GEMINI_API_KEY", "secret-key"),
            patch.object(settings, "OPENROUTER_API_KEY", ""),
            patch.object(settings, "OPENROUTER_MODELS", []),
            patch("infrastructure.llm.ollama_client.requests.post", return_value=Response()),
        ):
            with self.assertRaisesRegex(RuntimeError, "Gemini request failed \\(429 rate limit\\)"):
                ollama.generate("gemini:gemini-3.5-flash", "hello")

    def test_cloud_model_settings_reject_local_ollama_models(self):
        from services.settings.model_settings_service import ModelSettingsService

        user_id = f"cloud-model-user-{uuid.uuid4().hex}"
        with (
            patch.object(settings, "IS_CLOUD_RUNTIME", True),
            patch.object(settings, "CLOUD_DEFAULT_MODEL", "gemini:gemini-3.5-flash"),
            patch.object(settings, "GEMINI_API_KEY", "test-key"),
            patch.object(settings, "GEMINI_MODELS", ["gemini-3.5-flash"]),
            patch.object(settings, "OPENROUTER_API_KEY", ""),
            patch.object(settings, "OPENROUTER_MODELS", []),
            patch.object(settings, "TASK_MODEL_OVERRIDES", {}),
        ):
            service = ModelSettingsService()
            with self.assertRaisesRegex(ValueError, "configured allow-list"):
                service.update(
                    {"general": "mistral:latest", "rag": "gemini:gemini-3.5-flash"},
                    user_id=user_id,
                )
            models = service.get(user_id=user_id)

        self.assertEqual(models["general"], "gemini:gemini-3.5-flash")
        self.assertEqual(models["rag"], "gemini:gemini-3.5-flash")
        self.assertNotIn("mistral:latest", models.values())


class AuthFlowTests(unittest.TestCase):
    def test_email_verification_is_required_before_login(self):
        email = f"auth-{uuid.uuid4().hex}@example.com"
        password = "good-password-123"

        with patch.object(settings, "SEND_VERIFICATION_EMAILS", False):
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

    def test_production_signup_sends_verification_email_without_exposing_token(self):
        class Response:
            content = b'{"id":"email-test-id"}'

            def raise_for_status(self):
                return None

            def json(self):
                return {"id": "email-test-id"}

        email = f"cloud-auth-{uuid.uuid4().hex}@example.com"
        password = "good-password-123"

        with (
            patch.object(settings, "IS_PRODUCTION", True),
            patch.object(settings, "IS_CLOUD_RUNTIME", False),
            patch.object(settings, "SEND_VERIFICATION_EMAILS", True),
            patch.object(settings, "RESEND_API_KEY", "resend-test-key"),
            patch.object(settings, "EMAIL_FROM", "AI Platform <verify@example.com>"),
            patch("services.auth.email_service.requests.post", return_value=Response()) as post,
        ):
            created = auth_service.create_user(email, password)

        self.assertTrue(created["verification_sent"])
        self.assertEqual(created["verification_delivery"], "email")
        self.assertEqual(created["email_id"], "email-test-id")
        self.assertNotIn("verification_token", created)
        self.assertNotIn("verification_url", created)
        self.assertEqual(post.call_args.args[0], "https://api.resend.com/emails")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer resend-test-key")
        self.assertEqual(post.call_args.kwargs["json"]["to"], [email])


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


if __name__ == "__main__":
    unittest.main()
