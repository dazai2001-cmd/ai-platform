import io
import tempfile
import unittest
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
from services.career.job_search_service import CareerJobService
from services.storage.sqlite_service import SQLiteService


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

    def test_career_analysis_parses_noisy_json_and_numeric_score(self):
        service = CareerService()
        raw = 'Here is the score:\n{"analysis": {"fit_score": "82/100", "application_decision": "Apply"}}'

        parsed = service._normalize_analysis(service._json_or_fallback(raw, "analysis"))

        self.assertEqual(parsed["fit_score"], 82)
        self.assertEqual(parsed["application_decision"], "apply")

    def test_match_pack_reuses_analysis_and_requests_compact_output(self):
        service = CareerService()
        existing_analysis = {"fit_score": 84, "matched_skills": ["Python", "LLMs"]}
        generated = (
            '{"tailored_cv":{"headline":"AI Engineer","tailored_bullets":["Built RAG systems"]},'
            '"cover_letter":{"cover_letter":"I build practical AI systems."}}'
        )

        with patch("services.career.career_service.ollama.generate", return_value=generated) as generate:
            result = service.application_pack_for_match(
                "Python and RAG experience",
                "Build production AI products",
                existing_analysis,
                model="qwen3:8b",
            )

        self.assertEqual(result["analysis"], existing_analysis)
        self.assertEqual(result["tailored_cv"]["headline"], "AI Engineer")
        self.assertEqual(generate.call_args.kwargs["max_tokens"], 650)

    def test_career_service_falls_back_on_cloud_rate_limit(self):
        original = dict(settings.TASK_MODELS)
        original_models = list(settings.OPENAI_COMPAT_MODELS)
        original_fallback = settings.OPENAI_COMPAT_FALLBACK_MODEL
        original_cooldown = settings.OPENAI_COMPAT_COOLDOWN_SECONDS
        try:
            settings.TASK_MODELS["career"] = "z-ai/glm-5.2-free"
            settings.OPENAI_COMPAT_MODELS = ["z-ai/glm-5.2-free"]
            settings.OPENAI_COMPAT_FALLBACK_MODEL = "qwen3:8b"
            settings.OPENAI_COMPAT_COOLDOWN_SECONDS = 300
            service = CareerService()

            with patch(
                "services.career.career_service.ollama.generate",
                side_effect=[
                    RuntimeError("OpenAI-compatible request failed: 429 Too Many Requests"),
                    '{"fit_score": 81, "application_decision": "apply"}',
                ],
            ) as generate:
                result = service.analyze_fit("Python and LLM projects", "AI Engineer role")

            self.assertEqual(result["fit_score"], 81)
            self.assertEqual(result["model"], "qwen3:8b")
            self.assertEqual(result["requested_model"], "z-ai/glm-5.2-free")
            self.assertEqual(generate.call_args_list[0].args[0], "z-ai/glm-5.2-free")
            self.assertEqual(generate.call_args_list[1].args[0], "qwen3:8b")
        finally:
            settings.TASK_MODELS.clear()
            settings.TASK_MODELS.update(original)
            settings.OPENAI_COMPAT_MODELS = original_models
            settings.OPENAI_COMPAT_FALLBACK_MODEL = original_fallback
            settings.OPENAI_COMPAT_COOLDOWN_SECONDS = original_cooldown


class CareerScoreQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.test_db = SQLiteService(str(Path(self.temp_dir.name) / "queue.db"))
        self.db_patch = patch("services.career.job_search_service.db", self.test_db)
        self.db_patch.start()
        self.service = CareerJobService()
        self.service.save_profile("Python, TypeScript, LLM and RAG experience")

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_score_batch_is_persisted_and_can_be_cancelled(self):
        first = self.service.save_job(
            description="Build production AI systems and APIs.",
            title="AI Engineer",
            company="Example One",
            location="London",
            url="https://example.com/jobs/one",
            source="search",
        )
        second = self.service.save_job(
            description="Develop machine learning workflows.",
            title="ML Engineer",
            company="Example Two",
            location="Remote",
            url="https://example.com/jobs/two",
            source="search",
        )

        with patch.object(self.service, "start_score_worker"):
            batch = self.service.create_score_batch(job_ids=[first["id"], second["id"]])

        reloaded_service = CareerJobService()
        persisted = reloaded_service.get_current_score_batch()
        self.assertEqual(persisted["id"], batch["id"])
        self.assertEqual(persisted["total"], 2)
        self.assertEqual(persisted["remaining"], 2)

        cancelled = reloaded_service.cancel_score_batch(batch["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["cancelled"], 2)


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
        self.assertIn("rag_agent.ask(question, session_id=session_id)", source)
        self.assertIn('response.headers["X-Route"] = TASK_RAG', source)

    def test_dedicated_bi_endpoint_does_not_use_global_router_model(self):
        source = (self.root / "apps/api/routes/bi.py").read_text(encoding="utf-8")
        self.assertNotIn("router.route", source)
        self.assertIn("bi_agent.ask(question, session_id=session_id, dataset_name=dataset)", source)
        self.assertIn('result["route"] = TASK_BI', source)


class CareerJobImportTests(unittest.TestCase):
    def test_career_profile_round_trip(self):
        service = CareerJobService()
        original = service.profile()
        try:
            saved = service.save_profile("Python, SQL, LLM apps")
            self.assertEqual(saved["cv_text"], "Python, SQL, LLM apps")
            self.assertEqual(service.profile()["cv_text"], "Python, SQL, LLM apps")
        finally:
            service.save_profile(original.get("cv_text", ""))

    def test_job_html_parser_extracts_basic_fields(self):
        html = """
        <html>
          <head>
            <title>AI Engineering Intern | Example Labs</title>
            <meta property="og:site_name" content="Example Labs" />
          </head>
          <body>
            <main>
              <h1>AI Engineering Intern</h1>
              <p>Location: Remote / London</p>
              <p>Work with TypeScript, React, SQL, and LLM workflows.</p>
              <p>Build internal tools, review small PRs, and communicate clearly.</p>
            </main>
          </body>
        </html>
        """
        parsed = CareerJobService._parse_job_html(html, "https://jobs.example.com/ai-intern")

        self.assertIn("AI Engineering Intern", parsed["title"])
        self.assertEqual(parsed["company"], "Example Labs")
        self.assertIn("TypeScript", parsed["description"])
        self.assertIn("jobs.example.com", parsed["description"])

    def test_job_search_filters_role_title_and_location(self):
        preferences = {
            "roles": "AI Engineer",
            "locations": "United Kingdom",
            "remote": "any",
            "industries": "",
            "must_have": "",
            "avoid": "",
            "match_mode": "both",
        }
        matching = {
            "title": "AI Engineer",
            "company": "Example",
            "location": "London, United Kingdom",
            "description": "Build LLM workflows.",
        }
        wrong_title = {
            **matching,
            "title": "Senior Independent Software Developer",
        }
        wrong_location = {
            **matching,
            "location": "Berlin",
            "description": "Build AI products with documentation and gute Englischkenntnisse.",
        }
        misleading_description = {
            **matching,
            "location": "Worldwide",
            "description": "Candidates should understand UK businesses.",
        }

        self.assertTrue(CareerJobService._matches_preferences(matching, preferences))
        self.assertFalse(CareerJobService._matches_preferences(wrong_title, preferences))
        self.assertFalse(CareerJobService._matches_preferences(wrong_location, preferences))
        self.assertFalse(CareerJobService._matches_preferences(misleading_description, preferences))

    def test_profile_only_search_ignores_stale_role_and_location_filters(self):
        preferences = {
            "roles": "AI Engineer",
            "locations": "United Kingdom",
            "remote": "any",
            "industries": "",
            "must_have": "",
            "avoid": "",
            "match_mode": "profile",
        }
        candidate = {
            "title": "Machine Learning Engineer",
            "company": "Example",
            "location": "Remote",
            "description": "Build model pipelines.",
        }

        self.assertTrue(CareerJobService._matches_preferences(candidate, preferences))

    def test_good_match_requires_score_of_at_least_70(self):
        self.assertTrue(CareerJobService._is_good_match({"fit_score": 70}))
        self.assertTrue(CareerJobService._is_good_match({"fit_score": 85}))
        self.assertFalse(CareerJobService._is_good_match({"fit_score": 69}))
        self.assertFalse(CareerJobService._is_good_match({"fit_score": None}))

    def test_keyed_job_sources_are_skipped_without_credentials(self):
        with patch("services.career.job_search_service.settings.ADZUNA_APP_ID", ""), \
             patch("services.career.job_search_service.settings.ADZUNA_APP_KEY", ""), \
             patch("services.career.job_search_service.settings.REED_API_KEY", ""):
            self.assertIsNone(CareerJobService._fetch_adzuna_jobs("AI Engineer", {}, 5))
            self.assertIsNone(CareerJobService._fetch_reed_jobs("AI Engineer", {}, 5))

    def test_job_sources_are_interleaved(self):
        jobs = CareerJobService._interleave_source_jobs([
            [{"source": "adzuna", "title": "a1"}, {"source": "adzuna", "title": "a2"}],
            [{"source": "reed", "title": "r1"}, {"source": "reed", "title": "r2"}],
            [{"source": "remotive", "title": "m1"}],
        ])

        self.assertEqual(
            [job["title"] for job in jobs],
            ["a1", "r1", "m1", "a2", "r2"],
        )

    def test_job_identity_detects_same_vacancy_across_sources(self):
        reed_job = {
            "title": "Lead AI Engineer",
            "company": "Example Technology Ltd",
            "location": "London",
            "url": "https://www.reed.co.uk/jobs/12345?source=search",
        }
        adzuna_job = {
            "title": "Lead AI Engineer",
            "company": "Example Technology Ltd",
            "location": "London, UK",
            "url": "https://www.adzuna.co.uk/jobs/details/98765?utm_source=jobs",
        }

        self.assertTrue(
            CareerJobService._job_identity_keys(reed_job)
            & CareerJobService._job_identity_keys(adzuna_job)
        )

    def test_job_identity_keeps_same_role_in_different_locations(self):
        london_job = {
            "title": "AI Engineer",
            "company": "Example Ltd",
            "location": "London, UK",
            "url": "",
        }
        manchester_job = {
            "title": "AI Engineer",
            "company": "Example Ltd",
            "location": "Manchester, UK",
            "url": "",
        }

        self.assertFalse(
            CareerJobService._job_identity_keys(london_job)
            & CareerJobService._job_identity_keys(manchester_job)
        )

    def test_duplicate_cleanup_prefers_user_tracked_job(self):
        applied = {"status": "applied", "fit_score": None, "updated_at": 1}
        newly_scored = {"status": "scored", "fit_score": 95, "updated_at": 2}

        self.assertGreater(
            CareerJobService._deduplication_priority(applied),
            CareerJobService._deduplication_priority(newly_scored),
        )


if __name__ == "__main__":
    unittest.main()
