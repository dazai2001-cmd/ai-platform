import io
import unittest

from werkzeug.datastructures import FileStorage

from apps.api.deps import save_upload
from application.ingestion.ingestion_service import IngestionService
from application.ingestion.chunker import chunk_text
from domain.bi.pipeline import BIPipeline, _PROMPT
from domain.router.router import QueryRouter
from core.config.settings import settings


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

    def test_unknown_task_uses_rag_model(self):
        self.assertEqual(
            QueryRouter.model_for_type("unknown"),
            settings.TASK_MODELS["rag"],
        )


if __name__ == "__main__":
    unittest.main()
