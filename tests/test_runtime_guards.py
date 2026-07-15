import tempfile
import unittest
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fitz
import numpy as np
import pandas as pd
from werkzeug.test import EnvironBuilder

from apps.api.deps import remove_upload
from apps.api.main import create_app
from application.ingestion.ingestion_service import IngestionService
from core.config.settings import settings
from core.config.validation import ConfigurationError, configuration_issues, validate_settings
from domain.bi.pipeline import BIPipeline
from infrastructure.vectorstore.faiss_store import FAISSStore


def _config(**overrides):
    values = {
        name: getattr(settings, name)
        for name in dir(settings)
        if name.isupper() and not name.startswith("_")
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ConfigurationValidationTests(unittest.TestCase):
    def test_cloud_runtime_requires_provider_auth_and_safe_secret(self):
        config = _config(
            AI_RUNTIME="cloud",
            IS_CLOUD_RUNTIME=True,
            GEMINI_API_KEY="",
            OPENROUTER_API_KEY="",
            AUTH_REQUIRED=False,
            SECRET_KEY="dev-secret-key",
            SEND_VERIFICATION_EMAILS=False,
        )

        with self.assertRaises(ConfigurationError) as raised:
            validate_settings(config)

        message = str(raised.exception)
        self.assertIn("cloud_model", message)
        self.assertIn("AUTH_REQUIRED", message)
        self.assertIn("SECRET_KEY", message)

    def test_memory_rate_limit_storage_is_a_cloud_warning_not_a_startup_error(self):
        cloud_model = "gemini:gemini-3.5-flash"
        config = _config(
            APP_ENV="development",
            IS_PRODUCTION=False,
            AI_RUNTIME="cloud",
            IS_CLOUD_RUNTIME=True,
            GEMINI_API_KEY="configured",
            GEMINI_MODELS=["gemini-3.5-flash"],
            CLOUD_DEFAULT_MODEL=cloud_model,
            ROUTER_MODEL=cloud_model,
            TASK_MODELS={task: cloud_model for task in settings.TASK_MODELS},
            AUTH_REQUIRED=True,
            SECRET_KEY="x" * 32,
            SEND_VERIFICATION_EMAILS=False,
            RATE_LIMIT_ENABLED=True,
            RATE_LIMIT_STORAGE_URI="memory://",
            APP_PUBLIC_URL="https://app.example.com",
        )

        issues = configuration_issues(config)
        self.assertTrue(any(issue.name == "RATE_LIMIT_STORAGE_URI" and issue.severity == "warning" for issue in issues))
        validate_settings(config)

    def test_invalid_rate_limit_expression_fails_startup_validation(self):
        config = _config(RATE_LIMIT_AUTH="definitely invalid")

        with self.assertRaisesRegex(ConfigurationError, "RATE_LIMIT_AUTH"):
            validate_settings(config)


class RequestGuardTests(unittest.TestCase):
    def test_rejects_oversized_prompt_before_route_execution(self):
        with patch.object(settings, "MAX_PROMPT_CHARS", 5):
            app = create_app({"TESTING": True, "RATELIMIT_ENABLED": False})
            response = app.test_client().post("/api/chat", json={"query": "too long"})

        self.assertEqual(response.status_code, 413)
        self.assertIn("maximum length", response.get_json()["error"])

    def test_only_plain_health_check_is_public(self):
        with patch.object(settings, "AUTH_REQUIRED", True):
            app = create_app({"TESTING": True, "RATELIMIT_ENABLED": False})
            response = app.test_client().post("/api/health/warmup", json={})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {"error": "login required"})

    def test_auth_limit_returns_json_429(self):
        with patch.object(settings, "RATE_LIMIT_AUTH", "2 per minute"):
            app = create_app({"TESTING": True, "RATELIMIT_ENABLED": True})
            client = app.test_client()
            kwargs = {
                "json": {"email": "missing@example.com", "password": "bad-password"},
                "environ_base": {"REMOTE_ADDR": "198.51.100.77"},
            }
            first = client.post("/api/auth/login", **kwargs)
            second = client.post("/api/auth/login", **kwargs)
            limited = client.post("/api/auth/login", **kwargs)

        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 401)
        self.assertEqual(limited.status_code, 429)
        self.assertIn("rate limit", limited.get_json()["error"])

    def test_auth_limit_cannot_be_bypassed_with_rotating_bearer_values(self):
        with patch.object(settings, "RATE_LIMIT_AUTH", "2 per minute"):
            app = create_app({"TESTING": True, "RATELIMIT_ENABLED": True})
            client = app.test_client()
            statuses = []
            for token in ("attacker-a", "attacker-b", "attacker-c"):
                response = client.post(
                    "/api/auth/login",
                    json={"email": "missing@example.com", "password": "bad-password"},
                    headers={"Authorization": f"Bearer {token}"},
                    environ_base={"REMOTE_ADDR": "198.51.100.91"},
                )
                statuses.append(response.status_code)

        self.assertEqual(statuses, [401, 401, 429])

    def test_auth_endpoints_share_one_rate_limit_budget(self):
        with patch.object(settings, "RATE_LIMIT_AUTH", "2 per minute"):
            app = create_app({"TESTING": True, "RATELIMIT_ENABLED": True})
            client = app.test_client()
            kwargs = {"environ_base": {"REMOTE_ADDR": "198.51.100.93"}}

            login = client.post(
                "/api/auth/login",
                json={"email": "missing@example.com", "password": "bad-password"},
                **kwargs,
            )
            verify = client.get("/api/auth/verify?token=bad", **kwargs)
            limited = client.post(
                "/api/auth/resend-verification",
                json={"email": "missing@example.com"},
                **kwargs,
            )

        self.assertEqual(login.status_code, 401)
        self.assertEqual(verify.status_code, 400)
        self.assertEqual(limited.status_code, 429)

    def test_limiter_instances_do_not_mutate_previously_created_apps(self):
        with patch.object(settings, "RATE_LIMIT_AUTH", "1 per minute"):
            enabled = create_app({"TESTING": True, "RATELIMIT_ENABLED": True})
            enabled_client = enabled.test_client()
            request_kwargs = {
                "json": {"email": "missing@example.com", "password": "bad-password"},
                "environ_base": {"REMOTE_ADDR": "198.51.100.92"},
            }
            self.assertEqual(enabled_client.post("/api/auth/login", **request_kwargs).status_code, 401)

            disabled = create_app({"TESTING": True, "RATELIMIT_ENABLED": False})
            disabled_client = disabled.test_client()
            self.assertEqual(disabled_client.post("/api/auth/login", **request_kwargs).status_code, 401)

            self.assertEqual(enabled_client.post("/api/auth/login", **request_kwargs).status_code, 429)

    def test_trusted_proxy_hop_uses_forwarded_client_ip(self):
        with (
            patch.object(settings, "RATE_LIMIT_AUTH", "1 per minute"),
            patch.object(settings, "TRUST_PROXY_HOPS", 1),
        ):
            app = create_app({"TESTING": True, "RATELIMIT_ENABLED": True})
            client = app.test_client()
            kwargs = {"json": {"email": "missing@example.com", "password": "bad-password"}}
            first = client.post("/api/auth/login", headers={"X-Forwarded-For": "198.51.100.10"}, **kwargs)
            other_client = client.post("/api/auth/login", headers={"X-Forwarded-For": "198.51.100.11"}, **kwargs)
            repeated = client.post("/api/auth/login", headers={"X-Forwarded-For": "198.51.100.10"}, **kwargs)

        self.assertEqual(first.status_code, 401)
        self.assertEqual(other_client.status_code, 401)
        self.assertEqual(repeated.status_code, 429)

    def test_streamed_json_without_content_length_uses_json_limit(self):
        body = json.dumps({"query": "x" * 100}).encode()
        with patch.object(settings, "MAX_JSON_BYTES", 32):
            app = create_app({"TESTING": True, "RATELIMIT_ENABLED": False})
            builder = EnvironBuilder(path="/api/chat", method="POST", data=body, content_type="application/json")
            environ = builder.get_environ()
            environ.pop("CONTENT_LENGTH", None)
            environ["wsgi.input"] = io.BytesIO(body)
            environ["wsgi.input_terminated"] = True
            with app.request_context(environ):
                response = app.full_dispatch_request()

        self.assertEqual(response.status_code, 413)

    def test_text_source_must_be_a_bounded_string(self):
        app = create_app({"TESTING": True, "RATELIMIT_ENABLED": False})
        client = app.test_client()

        wrong_type = client.post("/api/rag/upload/text", json={"text": "hello", "source": ["note"]})
        self.assertEqual(wrong_type.status_code, 400)
        self.assertIn("source must be a string", wrong_type.get_json()["error"])

        with patch.object(settings, "MAX_SOURCE_CHARS", 4):
            too_long = client.post("/api/rag/upload/text", json={"text": "hello", "source": "notes"})
        self.assertEqual(too_long.status_code, 400)
        self.assertIn("character limit", too_long.get_json()["error"])


class IngestionLimitTests(unittest.TestCase):
    def test_document_reservations_include_in_flight_uploads_and_duplicate_sources(self):
        from apps.api.routes import rag as rag_routes

        with (
            patch.object(settings, "MAX_DOCUMENTS_PER_USER", 1),
            patch.object(rag_routes.rag_agent, "documents", return_value=[]),
        ):
            reservation = rag_routes._reserve_document("quota-user", "first.pdf")
            try:
                with self.assertRaisesRegex(ValueError, "Document limit"):
                    rag_routes._reserve_document("quota-user", "second.pdf")
                with self.assertRaisesRegex(ValueError, "already exists"):
                    rag_routes._reserve_document("quota-user", "first.pdf")
            finally:
                reservation.release()

    def test_per_user_chunk_limit_is_enforced_atomically_before_store_add(self):
        class Embedder:
            def embed_batch(self, chunks):
                raise AssertionError("embedding must not run over quota")

        with tempfile.TemporaryDirectory() as tmpdir:
            store = FAISSStore(dim=2, index_path=str(Path(tmpdir) / "index.faiss"))
            store.add(
                np.asarray([[1.0, 0.0]], dtype=np.float32),
                [{"user_id": "owner", "text": "existing"}],
            )
            service = IngestionService(Embedder(), store)
            with patch.object(settings, "MAX_CHUNKS_PER_USER", 1):
                with self.assertRaisesRegex(ValueError, "per-user limit"):
                    service.ingest_text("new chunk", source="note", extra={"user_id": "owner"})

    def test_rejects_pdf_over_page_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "large.pdf"
            document = fitz.open()
            document.new_page()
            document.new_page()
            document.save(path)
            document.close()

            with patch.object(settings, "MAX_PDF_PAGES", 1):
                with self.assertRaisesRegex(ValueError, "page limit"):
                    IngestionService._extract_pdf_text(str(path))

    def test_rejects_oversized_dataset_and_cross_join(self):
        with patch.object(settings, "MAX_DATASET_ROWS", 1):
            with self.assertRaisesRegex(ValueError, "row limit"):
                BIPipeline._validate_dataframe(pd.DataFrame({"value": [1, 2]}))

        with self.assertRaisesRegex(ValueError, "Joins"):
            BIPipeline._validate_select_sql("SELECT * FROM dataset CROSS JOIN dataset AS duplicate")

        bypasses = [
            "SELECT * FROM dataset a, dataset b",
            "SELECT * FROM dataset a JOIN dataset b ON a.id = b.id",
            "SELECT * FROM dataset a JOIN dataset b ON 1 = 1",
            "SELECT * FROM dataset CROSS/*comment*/JOIN dataset b",
            "SELECT * FROM dataset UNION SELECT * FROM dataset",
        ]
        for sql in bypasses:
            with self.subTest(sql=sql):
                with self.assertRaises(ValueError):
                    BIPipeline._validate_select_sql(sql)

        BIPipeline._validate_select_sql("SELECT * FROM dataset WHERE note = 'from home'")
        self.assertEqual(
            BIPipeline._validate_select_sql("SELECT * FROM dataset;"),
            "SELECT * FROM dataset",
        )

    def test_remove_upload_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "upload.pdf"
            path.write_bytes(b"%PDF")
            remove_upload(str(path))
            remove_upload(str(path))
            self.assertFalse(path.exists())

    def test_replacing_manifest_record_deletes_only_superseded_managed_upload(self):
        import domain.bi.pipeline as bi_module

        with tempfile.TemporaryDirectory() as tmpdir:
            upload_root = Path(tmpdir) / "uploads"
            upload_root.mkdir()
            old_path = upload_root / "old.csv"
            new_path = upload_root / "new.csv"
            old_path.write_text("value\n1\n")
            new_path.write_text("value\n2\n")
            manifest = Path(tmpdir) / "manifest.json"
            manifest.write_text(json.dumps([
                {"name": "sales", "path": str(old_path), "kind": "csv", "user_id": "owner"}
            ]))

            pipeline = object.__new__(BIPipeline)
            with (
                patch.object(settings, "UPLOAD_PATH", str(upload_root)),
                patch.object(bi_module, "_manifest", manifest),
            ):
                pipeline._save_dataset_record("sales", str(new_path), "csv", user_id="owner")

            self.assertFalse(old_path.exists())
            self.assertTrue(new_path.exists())
            records = json.loads(manifest.read_text())
            self.assertEqual(records[0]["path"], str(new_path))


if __name__ == "__main__":
    unittest.main()
