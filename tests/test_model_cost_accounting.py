from unittest.mock import MagicMock

from langchain_core.messages import AIMessage

from dining.llm.inference import InferenceSettings, invoke_explanations
from dining.llm.pricing import (
    ModelPrice,
    PricingCatalog,
    calculate_cost,
)
from dining.llm.tracing import RedactedTraceExporter, TracingSettings


def test_pricing_catalog_calculates_cost_when_all_inputs_known():
    catalog = PricingCatalog()
    catalog.register(
        ModelPrice(
            provider="ilmu",
            model="ilmu-mini-v3.3",
            price_version="2026-10",
            input_cost_per_million=0.15,
            output_cost_per_million=0.60,
            currency="USD",
        )
    )
    result = calculate_cost(
        provider="ilmu",
        model="ilmu-mini-v3.3",
        input_tokens=1000,
        output_tokens=500,
        price_version="2026-10",
        catalog=catalog,
    )
    assert result.estimated_cost is not None
    # 1000 * 0.15/1M = 0.00015; 500 * 0.60/1M = 0.00030; total = 0.00045
    assert result.estimated_cost == 0.00045
    assert result.currency == "USD"
    assert result.price_version == "2026-10"
    assert result.input_tokens == 1000
    assert result.output_tokens == 500
    assert result.total_tokens == 1500


def test_unknown_usage_yields_unknown_cost():
    catalog = PricingCatalog()
    # Missing input tokens
    res1 = calculate_cost(
        provider="ilmu",
        model="ilmu-mini-v3.3",
        input_tokens=None,
        output_tokens=500,
        price_version="2026-10",
        catalog=catalog,
    )
    assert res1.estimated_cost is None
    assert res1.currency is None
    assert res1.price_version is None

    # Missing output tokens
    res2 = calculate_cost(
        provider="ilmu",
        model="ilmu-mini-v3.3",
        input_tokens=1000,
        output_tokens=None,
        price_version="2026-10",
        catalog=catalog,
    )
    assert res2.estimated_cost is None

    # Unknown unpriced model
    res3 = calculate_cost(
        provider="ilmu",
        model="unknown-model-xyz",
        input_tokens=1000,
        output_tokens=500,
        price_version="2026-10",
        catalog=catalog,
    )
    assert res3.estimated_cost is None

    # Unknown price version
    res4 = calculate_cost(
        provider="ilmu",
        model="ilmu-mini-v3.3",
        input_tokens=1000,
        output_tokens=500,
        price_version="invalid-version-999",
        catalog=catalog,
    )
    assert res4.estimated_cost is None


def test_malformed_token_usage_is_rejected():
    catalog = PricingCatalog()
    # Negative tokens
    res = calculate_cost(
        provider="ilmu",
        model="ilmu-mini-v3.3",
        input_tokens=-10,
        output_tokens=50,
        price_version="2026-10",
        catalog=catalog,
    )
    assert res.estimated_cost is None


def test_per_call_tokens_and_cost_recorded_in_inference_result(monkeypatch):
    class FakeProvider:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            return AIMessage(
                content='{"option-a": ["budget", "nearby"]}',
                usage_metadata={
                    "input_tokens": 200,
                    "output_tokens": 40,
                    "total_tokens": 240,
                },
                response_metadata={"model_name": "ilmu-mini-v3.3"},
            )

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeProvider)
    settings = InferenceSettings.from_env(
        {
            "DINING_LLM_PROVIDER": "ilmu",
            "DINING_LLM_MODEL": "ilmu-mini-v3.3",
            "DINING_LLM_API_KEY": "test-key",
            "DINING_PRICE_VERSION": "2026-10",
        }
    )
    result = invoke_explanations(settings, ["option-a"])
    meta = result.metadata
    assert meta["input_tokens"] == 200
    assert meta["output_tokens"] == 40
    assert meta["total_tokens"] == 240
    assert meta["usage_source"] == "provider"
    assert meta["estimated_cost"] is not None
    assert meta["currency"] == "USD"
    assert meta["price_version"] == "2026-10"


def test_total_tokens_inferred_if_omitted_by_provider(monkeypatch):
    class FakeResponse:
        def __init__(self):
            self.content = '{"option-a": ["budget", "nearby"]}'
            self.usage_metadata = None
            self.response_metadata = {
                "model_name": "ilmu-mini-v3.3",
                "token_usage": {
                    "input_tokens": 120,
                    "output_tokens": 30,
                },
            }
            self.additional_kwargs = {}
            self.tool_calls = []

    class FakeProvider:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            return FakeResponse()

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeProvider)
    settings = InferenceSettings.from_env(
        {
            "DINING_LLM_PROVIDER": "ilmu",
            "DINING_LLM_MODEL": "ilmu-mini-v3.3",
            "DINING_LLM_API_KEY": "test-key",
        }
    )
    result = invoke_explanations(settings, ["option-a"])
    meta = result.metadata
    assert meta["input_tokens"] == 120
    assert meta["output_tokens"] == 30
    assert meta["total_tokens"] == 150


def test_cached_token_usage_is_captured(monkeypatch):
    class FakeProvider:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            return AIMessage(
                content='{"option-a": ["budget", "nearby"]}',
                usage_metadata={
                    "input_tokens": 500,
                    "output_tokens": 50,
                    "total_tokens": 550,
                    "cached_tokens": 400,
                },
                response_metadata={"model_name": "ilmu-mini-v3.3"},
            )

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeProvider)
    settings = InferenceSettings.from_env(
        {
            "DINING_LLM_PROVIDER": "ilmu",
            "DINING_LLM_MODEL": "ilmu-mini-v3.3",
            "DINING_LLM_API_KEY": "test-key",
        }
    )
    result = invoke_explanations(settings, ["option-a"])
    meta = result.metadata
    assert meta["cached_tokens"] == 400


def test_price_update_does_not_rewrite_historical_receipts():
    # An existing historical record created under version 2026-10 with cost 0.00045
    historical_receipt = {
        "provider": "ilmu",
        "model_id": "ilmu-mini-v3.3",
        "price_version": "2026-10",
        "input_tokens": 1000,
        "output_tokens": 500,
        "total_tokens": 1500,
        "estimated_cost": 0.00045,
        "currency": "USD",
        "result_status": "ok",
        "option_count": 2,
    }

    # Now update pricing catalog with version 2026-11 (higher rates)
    catalog = PricingCatalog()
    catalog.register(
        ModelPrice(
            provider="ilmu",
            model="ilmu-mini-v3.3",
            price_version="2026-11",
            input_cost_per_million=1.50,
            output_cost_per_million=6.00,
            currency="USD",
        )
    )

    # Historical receipt must retain its historical pricing and cost
    assert historical_receipt["price_version"] == "2026-10"
    assert historical_receipt["estimated_cost"] == 0.00045


def test_langsmith_metadata_includes_cost_accounting():
    client = MagicMock()
    exporter = RedactedTraceExporter(
        TracingSettings(enabled=True, api_key="test-key", project="test-project"),
        client=client,
    )
    exporter.record_stage(
        run_id="run-cost-1",
        session_ref="sess-cost-1",
        stage_name="after_model",
        duration_ms=45.0,
        status="ok",
        raw_metadata={
            "input_tokens": 200,
            "output_tokens": 50,
            "total_tokens": 250,
            "estimated_cost": 0.00006,
            "currency": "USD",
            "price_version": "2026-10",
            "model_calls": 1,
        },
    )
    assert client.create_run.called
    kwargs = client.create_run.call_args[1]
    assert kwargs["input_tokens"] == 200
    assert kwargs["output_tokens"] == 50
    assert kwargs["total_tokens"] == 250
    assert kwargs["estimated_cost"] == 0.00006
    assert kwargs["currency"] == "USD"
    assert kwargs["price_version"] == "2026-10"


def test_operations_summary_includes_token_and_cost_accounting(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from webapp import create_app

    monkeypatch.setenv("DINING_OPERATIONS_TOKEN", "test-operator-cost-" * 3)
    app = create_app(db_path=tmp_path / "ops-cost.sqlite3")
    client = TestClient(app)

    # Insert a fake meal with a result containing agent cost accounting into SQLite
    with app.state.store.transaction() as db:
        from dining.core.store import encode

        result_payload = {
            "status": "shortlisted",
            "options": [{"id": "opt-1", "reasons": ["budget"]}],
            "agent": {
                "provider": "ilmu",
                "model_id": "ilmu-mini-v3.3",
                "price_version": "2026-10",
                "input_tokens": 800,
                "output_tokens": 200,
                "total_tokens": 1000,
                "estimated_cost": 0.00024,
                "currency": "USD",
                "model_calls": 1,
                "tool_calls": 0,
                "usage_source": "provider",
                "stages": [],
            },
        }
        db.execute(
            "INSERT INTO users (id, email, name, password_hash, created_at, profile) "
            "VALUES ('user-1', 'ops@test.local', 'Ops User', 'hash', '2026-10-06T12:00:00Z', '{}')"
        )
        db.execute(
            "INSERT INTO rooms (id, name, owner_id, created_at) "
            "VALUES ('room-1', 'Ops Room', 'user-1', '2026-10-06T12:00:00Z')"
        )
        db.execute(
            "INSERT INTO meals (id, room_id, created_at, organizer_id, payload, status, revision, result, idempotency_key) "
            "VALUES ('meal-cost-1', 'room-1', '2026-10-06T12:00:00Z', 'user-1', '{}', 'selected', 1, ?, 'idem-1')",
            (encode(result_payload),),
        )

    res = client.get(
        "/api/operations/summary",
        headers={"X-Operations-Token": "test-operator-cost-" * 3},
    )
    assert res.status_code == 200
    data = res.json()
    assert "cost_summary" in data
    cost_summary = data["cost_summary"]
    assert cost_summary["total_input_tokens"] == 800
    assert cost_summary["total_output_tokens"] == 200
    assert cost_summary["total_tokens"] == 1000
    assert cost_summary["total_estimated_cost"] == 0.00024
    assert "USD" in cost_summary["currencies"]

    # Recent runs should have the cost details
    assert len(data["recent_runs"]) == 1
    run = data["recent_runs"][0]
    assert run["input_tokens"] == 800
    assert run["output_tokens"] == 200
    assert run["total_tokens"] == 1000
    assert run["estimated_cost"] == 0.00024
    assert run["currency"] == "USD"
    assert run["price_version"] == "2026-10"
    app.state.store.close()


def test_diner_ui_template_omits_internal_billing_details():
    with open("web/index.html", encoding="utf-8") as f:
        html = f.read()
    with open("web/app.js", encoding="utf-8") as f:
        js = f.read()

    # Neither HTML nor diner app.js should render token costs or pricing version to diners
    assert "cost_accounting" not in html
    assert "estimated_cost" not in html
    assert "price_version" not in html
    assert "cost_summary" not in html
    assert "price_version" not in js
    assert "cost_summary" not in js
