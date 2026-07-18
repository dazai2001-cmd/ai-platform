from unittest.mock import Mock, patch

import pandas as pd
import pytest
from flask import Flask

import domain.bi.pipeline as bi_module
from agents.bi_agent import BIAgent
from core.config.settings import settings
from domain.bi.pipeline import BIBusyError, BIPipeline, BIProviderError


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    import services.bi.dataset_repository as repository_module
    from services.storage.sqlite_service import SQLiteService

    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    manifest = tmp_path / "bi-manifest.json"
    monkeypatch.setattr(bi_module, "_manifest", manifest)
    monkeypatch.setattr(settings, "UPLOAD_PATH", str(upload_root))
    monkeypatch.setattr(
        repository_module, "db", SQLiteService(str(tmp_path / "datasets.db"))
    )
    bi_module._datasets.clear()
    bi_module._dataset_versions.clear()
    yield object.__new__(BIPipeline), upload_root
    bi_module._datasets.clear()
    bi_module._dataset_versions.clear()


def test_bi_requires_an_explicit_existing_dataset(pipeline):
    instance, _upload_root = pipeline
    bi_module._datasets.update({
        "owner:sales": pd.DataFrame({"value": [1]}),
        "owner:returns": pd.DataFrame({"value": [2]}),
    })

    with patch.object(bi_module.ollama, "generate") as generate:
        with pytest.raises(ValueError, match="Select a dataset"):
            instance.ask("Show totals", user_id="owner")
        with pytest.raises(ValueError, match="was not found"):
            instance.ask("Show totals", dataset_name="missing", user_id="owner")

    generate.assert_not_called()


def test_bi_reports_when_no_dataset_is_available(pipeline):
    instance, _upload_root = pipeline

    with pytest.raises(ValueError, match="Upload a CSV or Excel file"):
        instance.ask("Show totals", dataset_name="sales", user_id="owner")


def test_prompt_uses_bounded_history_but_never_raw_sample_values(pipeline):
    instance, _upload_root = pipeline
    bi_module._datasets["owner:sales"] = pd.DataFrame({
        "product": ["CONFIDENTIAL_PRODUCT", "Beta"],
        "month": ["March", "March"],
        "revenue": [999_999, 1_650],
    })
    history = [
        {"role": "user", "content": "Which product generated the most revenue?"},
        {"role": "assistant", "content": "product is Beta; total revenue is 4,650."},
    ]
    generated = """Calculate revenue for the previously identified product in March.
```sql
SELECT product, SUM(revenue) AS revenue FROM dataset
WHERE product = 'Beta' AND month = 'March' GROUP BY product
```"""

    with patch.object(bi_module.ollama, "generate", return_value=generated) as generate:
        result = instance.ask(
            "What was its revenue in March only?",
            dataset_name="sales",
            model="fixture-model",
            user_id="owner",
            history=history,
        )

    prompt = generate.call_args.args[1]
    assert "ASSISTANT: product is Beta; total revenue is 4,650." in prompt
    assert "CONFIDENTIAL_PRODUCT" not in prompt
    assert "999999" not in prompt
    assert "SAMPLE DATA" not in prompt
    assert "multiply a 0-1 ratio by 100" in prompt
    assert len(prompt) <= settings.MAX_PROMPT_CHARS
    assert result["rows"] == [{"product": "Beta", "revenue": 1650}]
    assert result["answer"] == "product is Beta; revenue is 1,650."


def test_history_is_bounded_and_keeps_the_most_recent_messages():
    history = [
        {"role": "user", "content": f"message-{index}-" + ("x" * 900)}
        for index in range(20)
    ]

    text = BIPipeline._history_text(history)

    assert len(text) <= min(4000, settings.MAX_PROMPT_CHARS // 2)
    assert "message-19-" in text
    assert "message-0-" not in text


def test_agent_passes_session_scoped_history_to_pipeline():
    history = [{"role": "assistant", "content": "product is Beta"}]
    result = {"answer": "revenue is 1,650", "rows": [{"revenue": 1650}]}

    with (
        patch("agents.bi_agent.memory.to_llm_format", return_value=history) as read_history,
        patch("agents.bi_agent.memory.add"),
        patch("agents.bi_agent.bi_pipeline.ask", return_value=result.copy()) as ask,
        patch("agents.bi_agent.analytics.record"),
    ):
        response = BIAgent().ask(
            "What was its revenue in March?",
            session_id="session-1",
            dataset_name="sales",
            model="fixture-model",
            user_id="owner",
        )

    read_history.assert_called_once_with("session-1", user_id="owner")
    ask.assert_called_once_with(
        "What was its revenue in March?",
        dataset_name="sales",
        model="fixture-model",
        user_id="owner",
        history=history,
    )
    assert response["session_id"] == "session-1"


def test_result_summary_formats_values_and_percentages():
    assert BIPipeline._result_answer(
        [{"total_revenue": 11850, "total_cost": 7200, "total_profit": 4650}],
        False,
    ) == "total revenue is 11,850; total cost is 7,200; total profit is 4,650."
    assert BIPipeline._result_answer(
        [{"returned_orders": 2, "percentage_returned": 16.6666667, "total_orders": 12}],
        False,
    ) == "returned orders is 2; percentage returned is 16.67%; total orders is 12."
    assert BIPipeline._result_answer([{"return_pct": 0.25}], False) == "return pct is 0.25%."
    assert BIPipeline._result_answer([], False) == "No matching rows were found."


def test_upload_names_are_normalized_and_collisions_get_unique_suffixes(pipeline):
    instance, upload_root = pipeline
    first = upload_root / "first.csv"
    second = upload_root / "second.csv"
    first.write_text("value\n1\n", encoding="utf-8")
    second.write_text("value\n2\n", encoding="utf-8")

    first_result = instance.load_csv(str(first), "2026 Sales & Returns!", user_id="owner")
    second_result = instance.load_csv(str(second), "2026 Sales & Returns!", user_id="owner")

    assert first_result["name"] == "dataset_2026_sales_returns"
    assert second_result["name"] == "dataset_2026_sales_returns_2"
    assert int(instance._get_dataset(first_result["name"], "owner").iloc[0]["value"]) == 1
    assert int(instance._get_dataset(second_result["name"], "owner").iloc[0]["value"]) == 2


def test_uploaded_dataset_survives_pipeline_cache_restart(pipeline):
    instance, upload_root = pipeline
    source = upload_root / "sales.csv"
    source.write_text("region,revenue\nNorth,100\nSouth,200\n", encoding="utf-8")
    uploaded = instance.load_csv(str(source), "sales", user_id="owner")

    bi_module._datasets.clear()
    bi_module._dataset_versions.clear()
    restarted = object.__new__(BIPipeline)

    assert [item["name"] for item in restarted.list_datasets("owner")] == [uploaded["name"]]
    assert restarted.get_sample(uploaded["name"], "owner") == {
        "columns": ["region", "revenue"],
        "sample": [
            {"region": "North", "revenue": 100},
            {"region": "South", "revenue": 200},
        ],
    }


def test_durable_datasets_are_not_retained_in_the_process_cache(pipeline):
    instance, upload_root = pipeline
    source = upload_root / "sales.csv"
    source.write_text("region,revenue\nNorth,100\n", encoding="utf-8")

    uploaded = instance.load_csv(str(source), "sales", user_id="owner")
    key = f"owner:{uploaded['name']}"
    assert key not in bi_module._datasets

    with patch.object(bi_module.pd, "read_csv", wraps=bi_module.pd.read_csv) as read_csv:
        assert instance.get_sample(uploaded["name"], "owner")["sample"] == [
            {"region": "North", "revenue": 100}
        ]
    assert read_csv.call_args.kwargs["nrows"] == 5
    assert key not in bi_module._datasets


@pytest.mark.parametrize(
    ("provider_message", "expected_status"),
    [
        ("Gemini request failed (429 rate limit).", 429),
        ("OpenRouter request failed (503).", 502),
    ],
)
def test_provider_failures_are_safe_and_actionable(pipeline, provider_message, expected_status):
    instance, _upload_root = pipeline
    bi_module._datasets["owner:sales"] = pd.DataFrame({"value": [1]})

    with (
        patch.object(bi_module.ollama, "generate", side_effect=RuntimeError(provider_message)),
        pytest.raises(BIProviderError) as error,
    ):
        instance.ask("Show totals", dataset_name="sales", user_id="owner")

    expected_message = (
        "The BI model is temporarily rate-limited. Wait a moment and try again."
        if expected_status == 429
        else "The BI model is temporarily unavailable. Please try again."
    )
    assert str(error.value) == expected_message
    assert provider_message not in str(error.value)
    assert error.value.status_code == expected_status


def test_bi_query_guard_rejects_concurrent_memory_pressure_safely(pipeline, monkeypatch):
    instance, _upload_root = pipeline
    monkeypatch.setattr(settings, "BI_QUERY_SLOT_TIMEOUT_SECONDS", 0)
    assert bi_module._BI_QUERY_SLOTS.acquire(blocking=False)
    try:
        with pytest.raises(BIBusyError) as error:
            instance.ask("Show totals", dataset_name="sales", user_id="owner")
        with pytest.raises(BIBusyError):
            instance.get_sample("sales", user_id="owner")
        with pytest.raises(BIBusyError):
            instance.load_csv("missing.csv", "sales", user_id="owner")
    finally:
        bi_module._BI_QUERY_SLOTS.release()

    assert error.value.status_code == 503
    assert str(error.value) == (
        "BI analysis is busy on this server. Wait a moment and try again."
    )


def test_bi_route_exposes_only_safe_provider_error(monkeypatch):
    from apps.api.routes import bi as bi_routes

    app = Flask(__name__)
    app.register_blueprint(bi_routes.bi_bp)
    monkeypatch.setattr(bi_routes, "current_user_id", lambda: "owner")
    monkeypatch.setattr(bi_routes.model_settings, "model_for", Mock(return_value="fixture"))
    monkeypatch.setattr(
        bi_routes.bi_agent,
        "ask",
        Mock(side_effect=BIProviderError("Gemini request failed (429 rate limit).")),
    )

    response = app.test_client().post(
        "/api/bi/ask",
        json={"question": "Show totals", "dataset": "sales"},
    )

    assert response.status_code == 429
    assert response.get_json() == {
        "error": "The BI model is temporarily rate-limited. Wait a moment and try again."
    }
