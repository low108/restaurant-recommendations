import json

from langchain_core.messages import AIMessage
from test_recommendation import ready_catalog, snapshot

from dining.recommendation.agent import DiningAgent


def test_graph_runs_without_model_or_external_trace(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    result = DiningAgent(ready_catalog())(snapshot())
    assert result["status"] == "shortlisted"
    assert result["agent"]["model_calls"] == 0
    assert result["agent"]["model_status"] == "disabled"


def test_model_only_receives_option_ids_and_allowed_labels(monkeypatch):
    calls = []

    class FakeModel:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0 and kwargs["max_tokens"] <= 384

        def invoke(self, messages):
            calls.append(messages)
            data = json.loads(messages[1].content)
            assert set(data) == {"options"}
            assert all(
                set(option) == {"id", "allowed_reason_ids"}
                for option in data["options"]
            )
            return AIMessage(
                content=json.dumps(
                    {
                        option["id"]: ["group_fit", "budget"]
                        for option in data["options"]
                    }
                )
            )

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeModel)
    result = DiningAgent(ready_catalog(), use_model=True)(snapshot())
    assert len(calls) == 1
    assert result["agent"]["model_status"] == "validated"


def test_invented_model_output_is_not_published_or_retried(monkeypatch):
    class FakeModel:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            return AIMessage(
                content='{"invented-place": ["allergy_safe", "certified"]}'
            )

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeModel)
    result = DiningAgent(ready_catalog(), use_model=True)(snapshot())
    assert result["agent"]["model_status"] == "template_fallback"
    assert result["agent"]["model_calls"] == 1
    assert "invented-place" not in str(result) and "allergy_safe" not in str(result)


def test_ilmu_configuration_enables_only_explanations_preserving_rank_order(
    monkeypatch,
):
    catalog, meal_snapshot = ready_catalog(), snapshot()
    baseline = DiningAgent(catalog)(meal_snapshot)
    monkeypatch.setenv("DINING_LLM_PROVIDER", "ilmu")
    monkeypatch.setenv("DINING_LLM_MODEL", "ilmu-mini-v3.3")
    monkeypatch.setenv("DINING_LLM_API_KEY", "test-only-key")

    class FakeModel:
        def __init__(self, **kwargs):
            assert kwargs["model"] == "ilmu-mini-v3.3"

        def invoke(self, messages):
            options = json.loads(messages[1].content)["options"]
            return AIMessage(
                content=json.dumps(
                    {option["id"]: ["budget", "nearby"] for option in options}
                )
            )

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeModel)
    result = DiningAgent(catalog)(meal_snapshot)
    assert result["agent"]["model_status"] == "validated"
    assert result["agent"]["provider"] == "ilmu"
    assert [item["id"] for item in result["options"]] == [
        item["id"] for item in baseline["options"]
    ]
    for before, after in zip(baseline["options"], result["options"], strict=True):
        assert {key: value for key, value in before.items() if key != "reasons"} == {
            key: value for key, value in after.items() if key != "reasons"
        }
