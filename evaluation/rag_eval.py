"""Deterministic evaluation helpers for the document RAG pipeline.

The evaluator deliberately avoids model-based judging so it can run offline.  An
adapter supplies an answer, returned source identifiers, and retrieved context;
the runner then computes transparent lexical/source metrics and records every
threshold failure.
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Mapping, Protocol, Sequence


REPORT_SCHEMA_VERSION = 1
SUPPORTED_DATASET_SCHEMA_VERSION = 1
SCORE_METRICS = (
    "retrieval_hit",
    "source_accuracy",
    "citation_correctness",
    "keyword_coverage",
    "answer_relevance",
    "groundedness",
)
REPORTED_METRICS = (*SCORE_METRICS, "hallucination_rate")

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "by",
    "for",
    "from",
    "has",
    "have",
    "he",
    "her",
    "his",
    "i",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "she",
    "that",
    "the",
    "their",
    "there",
    "they",
    "this",
    "to",
    "was",
    "were",
    "will",
    "with",
    "you",
    "your",
}


class DatasetValidationError(ValueError):
    """Raised when an evaluation dataset does not match the supported schema."""


@dataclass(frozen=True)
class RAGOutput:
    """Normalized output consumed by the deterministic evaluator."""

    answer: str
    sources: tuple[str, ...]
    contexts: tuple[str, ...]

    @classmethod
    def from_value(cls, value: "RAGOutput | Mapping[str, Any]") -> "RAGOutput":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("RAG adapter output must be a mapping or RAGOutput")

        answer = value.get("answer")
        if not isinstance(answer, str):
            raise TypeError("RAG adapter output must contain a string 'answer'")

        sources = _extract_sources(value.get("sources", []))
        contexts_value = value.get("contexts", value.get("retrieved_contexts", []))
        if not isinstance(contexts_value, Sequence) or isinstance(contexts_value, (str, bytes)):
            raise TypeError("RAG adapter 'contexts' must be a list of strings")
        contexts = []
        for context in contexts_value:
            if not isinstance(context, str):
                raise TypeError("Every RAG adapter context must be a string")
            contexts.append(context)

        return cls(answer=answer, sources=tuple(sources), contexts=tuple(contexts))


class RAGAdapter(Protocol):
    """Minimal dependency-injection boundary for an evaluated RAG system."""

    def ask(self, question: str, case: Mapping[str, Any]) -> RAGOutput | Mapping[str, Any]:
        """Return an answer, source identifiers, and retrieved context."""


class FixtureAdapter:
    """Read pre-recorded outputs embedded in each dataset case."""

    def ask(self, question: str, case: Mapping[str, Any]) -> RAGOutput:
        del question
        if "fixture" not in case:
            raise DatasetValidationError(
                f"Case '{case.get('id', '<unknown>')}' has no fixture output"
            )
        return RAGOutput.from_value(case["fixture"])


class CallableAdapter:
    """Wrap a callable for tests or custom/local RAG implementations."""

    def __init__(
        self,
        ask: Callable[[str, Mapping[str, Any]], RAGOutput | Mapping[str, Any]],
    ) -> None:
        self._ask = ask

    def ask(self, question: str, case: Mapping[str, Any]) -> RAGOutput | Mapping[str, Any]:
        return self._ask(question, case)


class PipelineAdapter:
    """Adapter for this repository's live Retriever + QAPipeline pair.

    Imports and construction of the heavyweight embedding/vector dependencies are
    intentionally left to the caller, so importing the offline evaluator remains
    fast and dependency-light.
    """

    def __init__(
        self,
        retriever: Any,
        pipeline: Any,
        *,
        model: str | None = None,
        user_id: str = "local",
    ) -> None:
        self.retriever = retriever
        self.pipeline = pipeline
        self.model = model
        self.user_id = user_id

    def ask(self, question: str, case: Mapping[str, Any]) -> RAGOutput:
        del case
        retrieval_results = self.retriever.search(question, user_id=self.user_id)
        result = self.pipeline.ask(
            question,
            model=self.model,
            user_id=self.user_id,
            retrieval_results=retrieval_results,
        )
        contexts = [
            item.get("metadata", {}).get("text", "")
            for item in retrieval_results
            if isinstance(item, Mapping)
        ]
        return RAGOutput.from_value({**result, "contexts": contexts})


def load_dataset(path: str | Path) -> dict[str, Any]:
    """Load and validate a versioned JSON evaluation dataset."""

    dataset_path = Path(path)
    with dataset_path.open(encoding="utf-8") as handle:
        dataset = json.load(handle)
    validate_dataset(dataset)
    return dataset


def validate_dataset(dataset: Mapping[str, Any]) -> None:
    """Validate the fields required by the deterministic metrics."""

    if not isinstance(dataset, Mapping):
        raise DatasetValidationError("Dataset root must be a JSON object")
    if dataset.get("schema_version") != SUPPORTED_DATASET_SCHEMA_VERSION:
        raise DatasetValidationError(
            "Unsupported dataset schema_version "
            f"{dataset.get('schema_version')!r}; expected {SUPPORTED_DATASET_SCHEMA_VERSION}"
        )
    if not isinstance(dataset.get("name"), str) or not dataset["name"].strip():
        raise DatasetValidationError("Dataset must have a non-empty string 'name'")

    _validate_thresholds(dataset.get("thresholds", {}), "dataset thresholds")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise DatasetValidationError("Dataset must contain a non-empty 'cases' list")

    seen_ids: set[str] = set()
    for index, case in enumerate(cases):
        location = f"cases[{index}]"
        if not isinstance(case, Mapping):
            raise DatasetValidationError(f"{location} must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise DatasetValidationError(f"{location}.id must be a non-empty string")
        if case_id in seen_ids:
            raise DatasetValidationError(f"Duplicate case id: {case_id}")
        seen_ids.add(case_id)
        if not isinstance(case.get("question"), str) or not case["question"].strip():
            raise DatasetValidationError(f"Case '{case_id}' must have a non-empty question")
        _validate_string_list(case, "expected_sources", case_id)
        _validate_string_list(case, "expected_keywords", case_id)
        _validate_thresholds(case.get("thresholds", {}), f"case '{case_id}' thresholds")


def evaluate_dataset(
    dataset: Mapping[str, Any],
    adapter: RAGAdapter,
    *,
    threshold_overrides: Mapping[str, float] | None = None,
    max_latency_ms: float | None = None,
) -> dict[str, Any]:
    """Evaluate all cases and return a JSON-serializable report.

    Adapter exceptions are captured as case-level errors so one broken query does
    not hide results for the remaining cases.
    """

    validate_dataset(dataset)
    overrides = dict(threshold_overrides or {})
    _validate_thresholds(overrides, "threshold overrides")
    if max_latency_ms is not None and max_latency_ms < 0:
        raise ValueError("max_latency_ms must be non-negative")

    dataset_thresholds = dict(dataset.get("thresholds", {}))
    reported_thresholds = {**dataset_thresholds, **overrides}
    case_reports = []
    for case in dataset["cases"]:
        thresholds = dict(dataset_thresholds)
        thresholds.update(case.get("thresholds", {}))
        # Explicit runner/CLI overrides apply to every case, including cases with
        # their own dataset-specific minimums.
        thresholds.update(overrides)
        case_reports.append(
            _evaluate_case(case, adapter, thresholds, max_latency_ms=max_latency_ms)
        )

    successful = [case for case in case_reports if case["error"] is None]
    metric_means = {
        metric: _rounded(mean(case["metrics"][metric] for case in successful))
        if successful
        else 0.0
        for metric in REPORTED_METRICS
    }
    latencies = [case["latency_ms"] for case in case_reports]
    passed = sum(1 for case in case_reports if case["passed"])
    errored = sum(1 for case in case_reports if case["error"] is not None)

    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "dataset": {
            "name": dataset["name"],
            "schema_version": dataset["schema_version"],
            "description": dataset.get("description", ""),
        },
        "thresholds": reported_thresholds,
        "max_latency_ms": max_latency_ms,
        "summary": {
            "total_cases": len(case_reports),
            "passed_cases": passed,
            "failed_cases": len(case_reports) - passed,
            "errored_cases": errored,
            "pass_rate": _rounded(passed / len(case_reports)),
            "average_latency_ms": _rounded(mean(latencies)),
            "p95_latency_ms": _rounded(_percentile_nearest_rank(latencies, 0.95)),
            "mean_metrics": metric_means,
        },
        "cases": case_reports,
        "passed": passed == len(case_reports),
    }


def format_text_report(report: Mapping[str, Any]) -> str:
    """Format a compact human-readable report for the command line."""

    summary = report["summary"]
    metrics = summary["mean_metrics"]
    lines = [
        f"RAG evaluation: {report['dataset']['name']}",
        (
            f"Cases: {summary['passed_cases']}/{summary['total_cases']} passed "
            f"({summary['errored_cases']} errors)"
        ),
        (
            "Mean metrics: "
            f"hit={metrics['retrieval_hit']:.3f} "
            f"source={metrics['source_accuracy']:.3f} "
            f"citations={metrics['citation_correctness']:.3f} "
            f"keywords={metrics['keyword_coverage']:.3f} "
            f"groundedness={metrics['groundedness']:.3f} "
            f"hallucination={metrics['hallucination_rate']:.3f}"
        ),
        (
            f"Latency: avg={summary['average_latency_ms']:.3f} ms "
            f"p95={summary['p95_latency_ms']:.3f} ms"
        ),
    ]
    for case in report["cases"]:
        status = "PASS" if case["passed"] else "FAIL"
        lines.append(f"[{status}] {case['id']} ({case['latency_ms']:.3f} ms)")
        for failure in case["failures"]:
            lines.append(f"  - {failure}")
    return "\n".join(lines)


def _evaluate_case(
    case: Mapping[str, Any],
    adapter: RAGAdapter,
    thresholds: Mapping[str, float],
    *,
    max_latency_ms: float | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    error = None
    output: RAGOutput | None = None
    try:
        output = RAGOutput.from_value(adapter.ask(case["question"], case))
    except Exception as exc:  # Deliberately preserve failures without aborting the suite.
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = _rounded((time.perf_counter() - started) * 1000)

    metrics = _empty_metrics()
    returned_sources: list[str] = []
    diagnostics = {
        "missing_expected_sources": list(case["expected_sources"]),
        "unexpected_sources": [],
        "missing_expected_keywords": list(case["expected_keywords"]),
    }
    if output is not None:
        returned_sources = list(output.sources)
        metrics = _compute_metrics(
            answer=output.answer,
            returned_sources=output.sources,
            contexts=output.contexts,
            expected_sources=case["expected_sources"],
            expected_keywords=case["expected_keywords"],
        )
        diagnostics = _compute_diagnostics(
            answer=output.answer,
            returned_sources=output.sources,
            expected_sources=case["expected_sources"],
            expected_keywords=case["expected_keywords"],
        )

    failures = []
    if error is not None:
        failures.append(f"adapter error: {error}")
    else:
        for metric, minimum in thresholds.items():
            value = metrics[metric]
            if value < minimum:
                detail = _failure_detail(metric, diagnostics)
                failures.append(
                    f"{metric} {value:.3f} < required {minimum:.3f}{detail}"
                )
    if max_latency_ms is not None and latency_ms > max_latency_ms:
        failures.append(
            f"latency_ms {latency_ms:.3f} > allowed {max_latency_ms:.3f}"
        )

    return {
        "id": case["id"],
        "question": case["question"],
        "passed": not failures,
        "metrics": metrics,
        "latency_ms": latency_ms,
        "expected_sources": list(case["expected_sources"]),
        "returned_sources": returned_sources,
        **diagnostics,
        "thresholds": dict(thresholds),
        "failures": failures,
        "error": error,
    }


def _compute_metrics(
    *,
    answer: str,
    returned_sources: Sequence[str],
    contexts: Sequence[str],
    expected_sources: Sequence[str],
    expected_keywords: Sequence[str],
) -> dict[str, float]:
    expected_keys = {_normalize_source(source) for source in expected_sources}
    returned_keys = {_normalize_source(source) for source in returned_sources}
    matched_sources = expected_keys & returned_keys

    retrieval_hit = 1.0 if matched_sources else 0.0
    source_accuracy = len(matched_sources) / len(expected_keys)
    citation_correctness = (
        len(matched_sources) / len(returned_keys) if returned_keys else 0.0
    )

    normalized_answer = _normalized_text(answer)
    matched_keywords = sum(
        1 for keyword in expected_keywords if _phrase_present(keyword, normalized_answer)
    )
    keyword_coverage = matched_keywords / len(expected_keywords)

    answer_tokens = {
        token for token in _tokens(answer) if token not in _STOP_WORDS and len(token) > 1
    }
    context_tokens = {
        token
        for context in contexts
        for token in _tokens(context)
        if token not in _STOP_WORDS and len(token) > 1
    }
    groundedness = (
        len(answer_tokens & context_tokens) / len(answer_tokens) if answer_tokens else 0.0
    )

    return {
        "retrieval_hit": _rounded(retrieval_hit),
        "source_accuracy": _rounded(source_accuracy),
        "citation_correctness": _rounded(citation_correctness),
        "keyword_coverage": _rounded(keyword_coverage),
        # This is intentionally the same transparent proxy: expected answer concepts
        # present in the generated answer, not a model-judged semantic score.
        "answer_relevance": _rounded(keyword_coverage),
        "groundedness": _rounded(groundedness),
        # This is the complement of lexical groundedness: a transparent proxy
        # for unsupported answer tokens, not a semantic hallucination judge.
        "hallucination_rate": _rounded(1.0 - groundedness),
    }


def _compute_diagnostics(
    *,
    answer: str,
    returned_sources: Sequence[str],
    expected_sources: Sequence[str],
    expected_keywords: Sequence[str],
) -> dict[str, list[str]]:
    expected_keys = {_normalize_source(source): source for source in expected_sources}
    returned_keys = {_normalize_source(source): source for source in returned_sources}
    normalized_answer = _normalized_text(answer)
    return {
        "missing_expected_sources": [
            source for key, source in expected_keys.items() if key not in returned_keys
        ],
        "unexpected_sources": [
            source for key, source in returned_keys.items() if key not in expected_keys
        ],
        "missing_expected_keywords": [
            keyword
            for keyword in expected_keywords
            if not _phrase_present(keyword, normalized_answer)
        ],
    }


def _failure_detail(metric: str, diagnostics: Mapping[str, Sequence[str]]) -> str:
    detail_key = {
        "retrieval_hit": "missing_expected_sources",
        "source_accuracy": "missing_expected_sources",
        "citation_correctness": "unexpected_sources",
        "keyword_coverage": "missing_expected_keywords",
        "answer_relevance": "missing_expected_keywords",
    }.get(metric)
    values = diagnostics.get(detail_key, ()) if detail_key else ()
    return f" ({', '.join(values)})" if values else ""


def _extract_sources(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError("RAG adapter 'sources' must be a list")
    sources = []
    for source in value:
        if isinstance(source, str):
            sources.append(source)
        elif isinstance(source, Mapping) and isinstance(source.get("source"), str):
            sources.append(source["source"])
        else:
            raise TypeError("Every source must be a string or an object with a string 'source'")
    return sources


def _validate_string_list(case: Mapping[str, Any], key: str, case_id: str) -> None:
    value = case.get(key)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise DatasetValidationError(
            f"Case '{case_id}' must have a non-empty string list '{key}'"
        )


def _validate_thresholds(value: Any, location: str) -> None:
    if not isinstance(value, Mapping):
        raise DatasetValidationError(f"{location} must be an object")
    for metric, threshold in value.items():
        if metric not in SCORE_METRICS:
            raise DatasetValidationError(
                f"Unknown metric '{metric}' in {location}; choose from {', '.join(SCORE_METRICS)}"
            )
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise DatasetValidationError(f"Threshold '{metric}' in {location} must be numeric")
        if not 0 <= float(threshold) <= 1:
            raise DatasetValidationError(f"Threshold '{metric}' in {location} must be between 0 and 1")


def _normalize_source(source: str) -> str:
    return source.strip().replace("\\", "/").casefold()


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.casefold())


def _normalized_text(text: str) -> str:
    return " ".join(_tokens(text))


def _phrase_present(phrase: str, normalized_text: str) -> bool:
    normalized_phrase = _normalized_text(phrase)
    return f" {normalized_phrase} " in f" {normalized_text} "


def _empty_metrics() -> dict[str, float]:
    metrics = dict.fromkeys(REPORTED_METRICS, 0.0)
    metrics["hallucination_rate"] = 1.0
    return metrics


def _rounded(value: float) -> float:
    return round(float(value), 6)


def _percentile_nearest_rank(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]
