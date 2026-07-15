# RAG evaluation

The repository includes a small deterministic evaluation harness for tracking RAG quality without a paid API or model-based judge.

Run the versioned example fixtures entirely offline:

```bash
python scripts/evaluate_rag.py
```

Fixture mode replays the outputs stored in `evaluation/datasets/rag_eval_v1.json`. It verifies the dataset, metrics, thresholds, failure reporting, and report format; it does **not** claim that the live retrieval/model stack produced those answers.

To evaluate the current local knowledge base and Ollama-backed pipeline instead:

```bash
python scripts/evaluate_rag.py --mode live --user-id local --output output/rag-eval.json
```

Live mode requires the normal RAG dependencies, an indexed knowledge base whose source names match the dataset, and an available configured model. Add `--model MODEL_ID` to override the task model. The command exits `0` only when every case passes, `1` for quality failures, and `2` for an invalid dataset or command setup.

Useful controls:

```bash
python scripts/evaluate_rag.py --threshold keyword_coverage=1.0 --max-latency-ms 5000
python scripts/evaluate_rag.py --json
python scripts/evaluate_rag.py --help
```

## Dataset contract

Each JSON case supplies a stable `id`, `question`, one or more exact `expected_sources`, and `expected_keywords`. Top-level thresholds apply to every case; a case may override them with its own `thresholds`. Source matching ignores case and slash direction but is otherwise exact.

The deterministic scores are:

- `retrieval_hit`: 1 when at least one expected source is returned, otherwise 0.
- `source_accuracy`: fraction of expected sources returned (source recall).
- `citation_correctness`: fraction of returned source metadata entries that are expected (source precision). Returned source metadata is treated as the pipeline's citation list.
- `keyword_coverage` / `answer_relevance`: fraction of expected phrases present in the answer; these are the same transparent relevance proxy.
- `groundedness`: fraction of unique, non-trivial answer tokens also present in the retrieved contexts. This is a lexical regression signal, not proof of factual correctness.
- `hallucination_rate`: `1 - groundedness`, reported as a transparent unsupported-token proxy. It is not accepted as a minimum CLI threshold because lower is better.
- `latency_ms`: wall-clock adapter latency for each query, with mean and nearest-rank p95 in the summary.

Adapter exceptions and every missed threshold are recorded under each case's `error` and `failures` fields. To evaluate another implementation in Python, pass any object with `ask(question, case)` to `evaluate_dataset`; return either `RAGOutput` or a mapping with `answer`, `sources`, and `contexts`.
