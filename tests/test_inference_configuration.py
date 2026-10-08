import json

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from dining.llm.inference import InferenceSettings
from webapp import create_app


def test_public_configuration_is_not_a_claim_of_live_inference(tmp_path, monkeypatch):
    monkeypatch.setenv("DINING_OPERATIONS_TOKEN", "operator-fixture-" * 3)
    settings = InferenceSettings(
        "ilmu",
        "ilmu-mini-v3.3",
        "https://private-provider.example/v1",
        "SECRET_TEST_KEY",
    )
    app = create_app(tmp_path / "app.sqlite3", inference_settings=settings)
    with TestClient(app) as client:
        response = client.get("/api/inference/status")
        assert response.status_code == 200
        assert response.json() == settings.public()
        assert response.json()["configuration_status"] == "ready"
        assert "validated" not in response.text
        assert "private-provider" not in response.text
        assert "SECRET_TEST_KEY" not in response.text
        assert response.headers["cache-control"] == "no-store"
        ops = client.get(
            "/api/operations/summary",
            headers={"X-Operations-Token": "operator-fixture-" * 3},
        )
        assert ops.json()["inference"] == settings.public()
        assert "SECRET_TEST_KEY" not in ops.text
    app.state.store.close()


def test_env_file_preserves_exported_values_and_never_sends_on_dry_run(
    tmp_path, monkeypatch, capsys
):
    from dining.llm.check import main

    def forbidden(*args, **kwargs):
        raise AssertionError("Dry run must not construct a provider")

    monkeypatch.setattr("langchain_openai.ChatOpenAI", forbidden)
    config = tmp_path / "inference.env"
    config.write_text(
        'DINING_LLM_PROVIDER=ilmu\nDINING_LLM_MODEL=ilmu-mini-v3.3\nDINING_LLM_API_KEY="SECRET_TEST_KEY"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DINING_LLM_MODEL", "explicit-model")
    assert main(["--env-file", str(config)]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["status"] == "not_run"
    assert report["model_id"] == "explicit-model"
    assert report["model_calls"] == 0
    assert report["provider_contract_verified"] is False
    assert "SECRET_TEST_KEY" not in output


def test_probe_missing_key_does_not_call_provider(monkeypatch, capsys):
    from dining.llm.check import main

    monkeypatch.setenv("DINING_LLM_PROVIDER", "ilmu")
    monkeypatch.setenv("DINING_LLM_MODEL", "ilmu-mini-v3.3")
    assert main(["--send"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["model_calls"] == 0
    assert report["model_error_code"] == "api_key_required"
    assert report["provider_contract_verified"] is False


def test_explicit_probe_uses_only_fictional_ids_and_reports_usage(monkeypatch, capsys):
    from dining.llm.check import main

    class Provider:
        def __init__(self, **kwargs):
            assert kwargs["model"] == "ilmu-mini-v3.3"
            assert kwargs["max_retries"] == 0

        def invoke(self, messages):
            content = json.loads(messages[1].content)
            assert [item["id"] for item in content["options"]] == [
                "probe-option-a",
                "probe-option-b",
            ]
            assert all(
                set(item) == {"id", "allowed_reason_ids"} for item in content["options"]
            )
            return AIMessage(
                content=json.dumps(
                    {item["id"]: ["group_fit", "budget"] for item in content["options"]}
                ),
                usage_metadata={
                    "input_tokens": 81,
                    "output_tokens": 22,
                    "total_tokens": 103,
                },
            )

    monkeypatch.setattr("langchain_openai.ChatOpenAI", Provider)
    monkeypatch.setenv("DINING_LLM_PROVIDER", "ilmu")
    monkeypatch.setenv("DINING_LLM_MODEL", "ilmu-mini-v3.3")
    monkeypatch.setenv("DINING_LLM_API_KEY", "SECRET_TEST_KEY")
    assert main(["--send"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["status"] == "validated"
    assert report["model_calls"] == 1
    assert report["input_tokens"] == 81
    assert report["role"] == "explanation_labels_only"
    assert "SECRET_TEST_KEY" not in output


def test_unreadable_env_file_reports_bounded_error(tmp_path, capsys):
    from dining.llm.check import main

    assert main(["--env-file", str(tmp_path / "missing.env")]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["error_code"] == "env_file_unreadable"
    assert report["model_calls"] == 0
