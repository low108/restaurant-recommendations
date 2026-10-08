from test_recommendation import ready_catalog, snapshot

from dining.catalog.models import Catalog
from dining.llm.inference import InferenceSettings
from dining.llm.tracing import (
    RedactedTraceExporter,
    TracingSettings,
    set_trace_exporter,
)
from dining.recommendation.agent import DiningAgent


class MockTraceClient:
    def __init__(self, should_fail=False):
        self.exported_runs = []
        self.should_fail = should_fail

    def create_run(self, **kwargs):
        if self.should_fail:
            raise RuntimeError("LangSmith network timeout")
        self.exported_runs.append(kwargs)


def test_tracing_remains_off_when_configuration_is_incomplete():
    # 1. Default settings - disabled
    settings = TracingSettings()
    assert settings.enabled is False

    # 2. Incomplete settings (tracing true but no api key)
    settings_no_key = TracingSettings(enabled=True, api_key="")
    assert settings_no_key.is_configured is False
    assert settings_no_key.enabled is False

    # 3. Complete settings
    settings_complete = TracingSettings(
        enabled=True,
        api_key="lsv2_pt_test_key_12345678",
        project="makan-pilot-test",
    )
    assert settings_complete.is_configured is True
    assert settings_complete.enabled is True


def test_private_sentinel_values_never_appear_in_traces():
    SENTINEL_ALLERGY = "SENTINEL_PEANUT_ALLERGY_ANAPHYLAXIS"
    SENTINEL_GPS = "3.123456,101.987654"
    SENTINEL_CRAVING = "SENTINEL_PRIVATE_CRAVING_TEXT_TOM_YUM"
    SENTINEL_CREDENTIAL = "sk-super-secret-production-key-999"
    SENTINEL_FEEDBACK = "SENTINEL_SECRET_FEEDBACK_POOR_SERVICE"

    mock_client = MockTraceClient()
    exporter = RedactedTraceExporter(
        TracingSettings(enabled=True, api_key="lsv2_pt_key", project="test"),
        client=mock_client,
    )

    raw_stage_data = {
        "stage": "before_model",
        "diner_allergy": SENTINEL_ALLERGY,
        "gps_origin": SENTINEL_GPS,
        "craving": SENTINEL_CRAVING,
        "api_key": SENTINEL_CREDENTIAL,
        "feedback": SENTINEL_FEEDBACK,
        "option_ids": ["opt-1", "opt-2"],
        "duration_ms": 15.2,
        "status": "ok",
    }

    exporter.record_stage(
        run_id="run-test-1",
        session_ref="sess-ref-1",
        stage_name="before_model",
        duration_ms=15.2,
        status="ok",
        raw_metadata=raw_stage_data,
    )

    assert len(mock_client.exported_runs) == 1
    dump = str(mock_client.exported_runs[0])

    # Sentinel values must NEVER appear in parent or child traces
    assert SENTINEL_ALLERGY not in dump
    assert SENTINEL_GPS not in dump
    assert SENTINEL_CRAVING not in dump
    assert SENTINEL_CREDENTIAL not in dump
    assert SENTINEL_FEEDBACK not in dump

    # Structural fields are present
    run = mock_client.exported_runs[0]
    assert run["run_id"] == "run-test-1"
    assert run["stage_name"] == "before_model"
    assert run["duration_ms"] == 15.2
    assert run["status"] == "ok"


def test_middleware_inputs_and_outputs_represented_through_hashes_and_counts():
    mock_client = MockTraceClient()
    exporter = RedactedTraceExporter(
        TracingSettings(enabled=True, api_key="lsv2_pt_key", project="test"),
        client=mock_client,
    )

    private_inputs = ["Spicy soup", "Extra chilli"]
    private_outputs = ["Option-123", "Option-456"]

    exporter.record_middleware_step(
        run_id="run-mid-1",
        stage_name="preference_filter",
        input_items=private_inputs,
        output_items=private_outputs,
        duration_ms=5.0,
    )

    assert len(mock_client.exported_runs) == 1
    run = mock_client.exported_runs[0]

    # No raw text in trace
    dump = str(run)
    assert "Spicy soup" not in dump
    assert "Extra chilli" not in dump

    # Hashes and counts represent state
    assert run["input_count"] == 2
    assert run["output_count"] == 2
    assert "input_hash" in run
    assert "output_hash" in run


def test_trace_exporter_failure_cannot_break_recommendation():
    raw = ready_catalog().model_dump()
    catalog = Catalog.model_validate(raw)
    state = snapshot()

    failing_client = MockTraceClient(should_fail=True)
    failing_exporter = RedactedTraceExporter(
        TracingSettings(enabled=True, api_key="lsv2_pt_key", project="test"),
        client=failing_client,
    )
    set_trace_exporter(failing_exporter)

    try:
        agent = DiningAgent(catalog, InferenceSettings(provider="disabled"))

        # Agent execution must complete and succeed even when trace export raises errors
        result = agent(state)
        assert result["status"] == "shortlisted"
        assert len(result["options"]) > 0
    finally:
        set_trace_exporter(None)
