import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evaluation.rag_eval import (
    CallableAdapter,
    DatasetValidationError,
    FixtureAdapter,
    PipelineAdapter,
    evaluate_dataset,
    load_dataset,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "evaluation" / "datasets" / "rag_eval_v1.json"


def make_dataset(**case_updates):
    case = {
        "id": "example",
        "question": "What is the launch date?",
        "expected_sources": ["handbook.md"],
        "expected_keywords": ["15 September", "2026"],
        **case_updates,
    }
    return {
        "schema_version": 1,
        "name": "unit-test",
        "thresholds": {
            "retrieval_hit": 1.0,
            "source_accuracy": 1.0,
            "citation_correctness": 0.5,
            "keyword_coverage": 1.0,
            "groundedness": 0.5,
        },
        "cases": [case],
    }


class RAGEvaluationTests(unittest.TestCase):
    def test_live_adapter_reuses_one_retrieval_for_answer_and_metrics(self):
        retrieved = [{"metadata": {"source": "handbook.md", "text": "Launch is in September."}}]

        class Retriever:
            calls = 0

            def search(self, question, user_id):
                self.calls += 1
                return retrieved

        class Pipeline:
            received = None

            def ask(self, question, **kwargs):
                self.received = kwargs["retrieval_results"]
                return {"answer": "Launch is in September.", "sources": ["handbook.md"]}

        retriever = Retriever()
        pipeline = Pipeline()
        output = PipelineAdapter(retriever, pipeline).ask("When?", {})

        self.assertEqual(retriever.calls, 1)
        self.assertIs(pipeline.received, retrieved)
        self.assertEqual(output.contexts, ("Launch is in September.",))

    def test_versioned_fixture_dataset_passes_offline(self):
        dataset = load_dataset(DATASET_PATH)

        report = evaluate_dataset(dataset, FixtureAdapter())

        self.assertTrue(report["passed"])
        self.assertEqual(report["summary"]["passed_cases"], 3)
        self.assertEqual(report["summary"]["errored_cases"], 0)
        self.assertEqual(report["summary"]["mean_metrics"]["retrieval_hit"], 1.0)
        self.assertGreaterEqual(report["summary"]["mean_metrics"]["groundedness"], 0.5)
        self.assertAlmostEqual(
            report["summary"]["mean_metrics"]["hallucination_rate"],
            1.0 - report["summary"]["mean_metrics"]["groundedness"],
            places=5,
        )

    def test_injected_adapter_reports_metric_failures(self):
        adapter = CallableAdapter(
            lambda question, case: {
                "answer": "The launch might happen later.",
                "sources": ["unrelated.md"],
                "contexts": ["A different project was delayed."],
            }
        )

        report = evaluate_dataset(make_dataset(), adapter)
        result = report["cases"][0]

        self.assertFalse(report["passed"])
        self.assertEqual(result["metrics"]["retrieval_hit"], 0.0)
        self.assertEqual(result["metrics"]["keyword_coverage"], 0.0)
        self.assertEqual(result["missing_expected_sources"], ["handbook.md"])
        self.assertEqual(
            result["missing_expected_keywords"], ["15 September", "2026"]
        )
        self.assertTrue(any("source_accuracy" in failure for failure in result["failures"]))
        self.assertTrue(any("groundedness" in failure for failure in result["failures"]))

    def test_adapter_exception_is_explicit_and_other_cases_continue(self):
        dataset = make_dataset()
        dataset["cases"].append(
            {
                **dataset["cases"][0],
                "id": "second",
                "question": "A second question",
            }
        )

        def ask(question, case):
            if case["id"] == "example":
                raise RuntimeError("model unavailable")
            return {
                "answer": "The date is 15 September 2026.",
                "sources": [{"source": "handbook.md", "score": 0.1}],
                "contexts": ["The date is 15 September 2026."],
            }

        report = evaluate_dataset(dataset, CallableAdapter(ask))

        self.assertEqual(report["summary"]["errored_cases"], 1)
        self.assertIn("RuntimeError: model unavailable", report["cases"][0]["error"])
        self.assertIn("adapter error", report["cases"][0]["failures"][0])
        self.assertTrue(report["cases"][1]["passed"])

    def test_dataset_rejects_unknown_metric(self):
        dataset = make_dataset()
        dataset["thresholds"]["magic_judge"] = 0.5

        with self.assertRaisesRegex(DatasetValidationError, "Unknown metric"):
            evaluate_dataset(dataset, CallableAdapter(lambda question, case: {}))

    def test_runner_threshold_override_wins_over_case_threshold(self):
        dataset = make_dataset(thresholds={"keyword_coverage": 0.0})
        adapter = CallableAdapter(
            lambda question, case: {
                "answer": "No date is available.",
                "sources": ["handbook.md"],
                "contexts": ["No date is available."],
            }
        )

        report = evaluate_dataset(
            dataset,
            adapter,
            threshold_overrides={"keyword_coverage": 1.0},
        )

        self.assertFalse(report["passed"])
        self.assertIn("keyword_coverage", report["cases"][0]["failures"][0])

    def test_cli_fixture_mode_writes_machine_readable_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "report.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "evaluate_rag.py"),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=15,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("3/3 passed", result.stdout)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            self.assertEqual(report["report_schema_version"], 1)

    def test_cli_returns_one_for_quality_failure(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "evaluate_rag.py"),
                "--threshold",
                "citation_correctness=1.0",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("[FAIL] atlas-launch-date", result.stdout)
        self.assertIn("team_directory.md", result.stdout)


if __name__ == "__main__":
    unittest.main()
