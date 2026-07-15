#!/usr/bin/env python
"""Run the deterministic RAG evaluation suite in fixture or live mode."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.rag_eval import (  # noqa: E402
    SCORE_METRICS,
    DatasetValidationError,
    FixtureAdapter,
    PipelineAdapter,
    evaluate_dataset,
    format_text_report,
    load_dataset,
)


DEFAULT_DATASET = ROOT / "evaluation" / "datasets" / "rag_eval_v1.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate RAG outputs with deterministic source, keyword, grounding, "
            "and latency metrics. Fixture mode is offline and makes no model calls."
        )
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help=f"Versioned JSON dataset (default: {DEFAULT_DATASET.relative_to(ROOT)})",
    )
    parser.add_argument(
        "--mode",
        choices=("fixture", "live"),
        default="fixture",
        help="fixture replays stored outputs; live queries this repository's local RAG pipeline",
    )
    parser.add_argument("--model", help="Optional model override for live mode")
    parser.add_argument("--user-id", default="local", help="Knowledge-base owner for live mode")
    parser.add_argument(
        "--threshold",
        action="append",
        default=[],
        metavar="METRIC=MINIMUM",
        help="Override a minimum score; repeatable (for example keyword_coverage=1.0)",
    )
    parser.add_argument(
        "--max-latency-ms",
        type=float,
        help="Fail a case whose measured end-to-end adapter latency exceeds this value",
    )
    parser.add_argument("--output", type=Path, help="Write the complete JSON report to this path")
    parser.add_argument("--json", action="store_true", help="Print the complete report as JSON")
    return parser


def parse_thresholds(values: list[str]) -> dict[str, float]:
    thresholds = {}
    for value in values:
        metric, separator, raw_minimum = value.partition("=")
        if not separator or metric not in SCORE_METRICS:
            raise ValueError(
                f"Invalid threshold '{value}'; expected one of {', '.join(SCORE_METRICS)}=0..1"
            )
        try:
            minimum = float(raw_minimum)
        except ValueError as exc:
            raise ValueError(f"Invalid numeric threshold in '{value}'") from exc
        if not 0 <= minimum <= 1:
            raise ValueError(f"Threshold in '{value}' must be between 0 and 1")
        thresholds[metric] = minimum
    return thresholds


def build_adapter(mode: str, *, model: str | None, user_id: str):
    if mode == "fixture":
        return FixtureAdapter()

    # Lazy import keeps fixture mode free of FAISS, sentence-transformers, Redis,
    # network access, and model initialization.
    from agents.rag_agent import rag_agent

    ready_agent = rag_agent.ensure_ready()
    return PipelineAdapter(
        ready_agent.retriever,
        ready_agent.pipeline,
        model=model,
        user_id=user_id,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        thresholds = parse_thresholds(args.threshold)
        dataset = load_dataset(args.dataset)
        adapter = build_adapter(args.mode, model=args.model, user_id=args.user_id)
        report = evaluate_dataset(
            dataset,
            adapter,
            threshold_overrides=thresholds,
            max_latency_ms=args.max_latency_ms,
        )
    except (DatasetValidationError, OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(2, f"RAG evaluation setup error: {exc}\n")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_text_report(report))
        if args.output:
            print(f"JSON report: {args.output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
