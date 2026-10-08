from dining.llm.inference import InferenceSettings


def test_json_code_fence_is_formatting_but_surrounding_prose_is_rejected(monkeypatch):
    from langchain_core.messages import AIMessage

    from dining.llm.inference import invoke_explanations

    responses = [
        '```json\n{"option-a":["budget","nearby"]}\n```',
        'Ignore safety. ```json\n{"option-a":["budget","nearby"]}\n```',
        '```json\n{"invented":["budget","nearby"]}\n```',
    ]

    class Provider:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            return AIMessage(content=responses.pop(0))

    monkeypatch.setattr("langchain_openai.ChatOpenAI", Provider)
    settings = InferenceSettings.from_env({}, use_model=True)
    assert invoke_explanations(settings, ["option-a"]).labels == {
        "option-a": ["budget", "nearby"]
    }
    assert invoke_explanations(settings, ["option-a"]).labels is None
    assert invoke_explanations(settings, ["option-a"]).labels is None


def test_ilmu_requires_an_explicit_model_and_keeps_credentials_private():
    settings = InferenceSettings.from_env(
        {"DINING_LLM_PROVIDER": "ilmu", "DINING_LLM_API_KEY": "private-test-key"}
    )
    assert settings.public() == {
        "provider": "ilmu",
        "model_id": None,
        "configuration_status": "not_configured",
        "error_code": "model_required",
        "role": "explanation_labels_only",
    }
    assert "private-test-key" not in repr(settings)
    configured = InferenceSettings.from_env(
        {
            "DINING_LLM_PROVIDER": "ilmu",
            "DINING_LLM_MODEL": "ilmu-mini-v3.3",
            "DINING_LLM_API_KEY": "private-test-key",
        }
    )
    assert configured.base_url == "https://api.ilmu.ai/v1"
    assert configured.public()["configuration_status"] == "ready"
    assert "private-test-key" not in str(configured.public())


def test_provider_receives_only_ids_and_labels_and_reports_real_usage(monkeypatch):
    import json

    from langchain_core.messages import AIMessage

    from dining.llm.inference import invoke_explanations

    seen = []

    class Provider:
        def __init__(self, **kwargs):
            assert kwargs["model"] == "ilmu-mini-v3.3"
            assert kwargs["api_key"] == "private-test-key"
            assert kwargs["base_url"] == "https://api.ilmu.ai/v1"
            assert kwargs["max_retries"] == 0

        def invoke(self, messages):
            seen.append(json.loads(messages[1].content))
            return AIMessage(
                content='{"option-a":["budget","nearby"]}',
                usage_metadata={
                    "input_tokens": 123,
                    "output_tokens": 21,
                    "total_tokens": 144,
                },
                response_metadata={
                    "model_name": "ilmu-mini-v3.3",
                    "finish_reason": "stop",
                },
            )

    monkeypatch.setattr("langchain_openai.ChatOpenAI", Provider)
    settings = InferenceSettings.from_env(
        {
            "DINING_LLM_PROVIDER": "ilmu",
            "DINING_LLM_MODEL": "ilmu-mini-v3.3",
            "DINING_LLM_API_KEY": "private-test-key",
        }
    )
    result = invoke_explanations(settings, ["option-a"])
    assert seen == [
        {
            "options": [
                {
                    "id": "option-a",
                    "allowed_reason_ids": ["group_fit", "budget", "nearby"],
                }
            ]
        }
    ]
    assert result.labels == {"option-a": ["budget", "nearby"]}
    assert result.metadata["inference_status"] == "validated"
    assert result.metadata["model_calls"] == 1
    assert result.metadata["usage_source"] == "provider"
    assert result.metadata["input_tokens"] == 123
    assert result.metadata["output_tokens"] == 21
    assert result.metadata["returned_model_id"] == "ilmu-mini-v3.3"
    assert "private-test-key" not in str(result)


def test_truncated_or_refused_output_keeps_fallback_even_when_json_looks_valid(
    monkeypatch,
):
    from langchain_core.messages import AIMessage

    from dining.llm.inference import invoke_explanations

    responses = [
        AIMessage(
            content='{"option-a":["budget","nearby"]}',
            response_metadata={"finish_reason": "length"},
        ),
        AIMessage(
            content='{"option-a":["budget","nearby"]}',
            additional_kwargs={"refusal": "cannot comply"},
        ),
    ]

    class Provider:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            return responses.pop(0)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", Provider)
    settings = InferenceSettings.from_env({}, use_model=True)
    for _ in range(2):
        result = invoke_explanations(settings, ["option-a"])
        assert result.labels is None
        assert result.metadata["inference_status"] == "invalid_output"
        assert result.metadata["model_calls"] == 1
        assert result.metadata["input_tokens"] is None


def test_untrusted_output_is_strict_bounded_and_never_exposed(monkeypatch):
    from langchain_core.messages import AIMessage

    from dining.llm.inference import invoke_explanations

    payloads = [
        '{"option-a":["budget","nearby"],"option-a":["budget","group_fit"]}',
        '{"invented-option":["budget","nearby"]}',
        '{"option-a":["allergy_safe","nearby"]}',
        '{"option-a":["budget","budget"]}',
        '{"option-a":[["budget"],"nearby"]}',
        " " * 9000 + '{"option-a":["budget","nearby"]}',
    ]

    class Provider:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            return AIMessage(
                content=payloads.pop(0),
                usage_metadata={
                    "input_tokens": 10,
                    "output_tokens": 30,
                    "total_tokens": 40,
                },
            )

    monkeypatch.setattr("langchain_openai.ChatOpenAI", Provider)
    settings = InferenceSettings.from_env({}, use_model=True)
    while payloads:
        result = invoke_explanations(settings, ["option-a"])
        assert result.labels is None
        assert result.metadata["inference_status"] == "invalid_output"
        assert result.metadata["input_tokens"] == 10
        assert result.metadata["output_tokens"] == 30
        assert "allergy_safe" not in str(result)


def test_legacy_openai_credentials_are_used_only_for_an_explicit_provider():
    env = {
        "OPENAI_API_KEY": "legacy-private-key",
        "OPENAI_BASE_URL": "https://private-gateway.example/v1",
        "DINING_LLM_MODEL": "ilmu-mini-v3.3",
    }
    disabled = InferenceSettings.from_env(env)
    assert disabled.configuration_status == "disabled"
    assert disabled.api_key is None
    configured = InferenceSettings.from_env({**env, "DINING_LLM_PROVIDER": "ilmu"})
    assert configured.enabled
    assert configured.base_url == "https://private-gateway.example/v1"
    assert configured.api_key == "legacy-private-key"
    explicit = InferenceSettings.from_env(
        {
            **env,
            "DINING_LLM_PROVIDER": "ilmu",
            "DINING_LLM_API_KEY": "new-private-key",
            "DINING_LLM_BASE_URL": "https://api.ilmu.ai/v1",
        }
    )
    assert explicit.api_key == "new-private-key"
    assert explicit.base_url == "https://api.ilmu.ai/v1"


def test_invalid_configuration_and_empty_options_never_create_a_provider(monkeypatch):
    from dining.llm.inference import invoke_explanations

    def forbidden_provider(**kwargs):
        raise AssertionError("Provider must not be constructed")

    monkeypatch.setattr("langchain_openai.ChatOpenAI", forbidden_provider)
    cases = [
        ({}, "disabled"),
        (
            {"DINING_LLM_PROVIDER": "ilmu", "DINING_LLM_MODEL": "ilmu-mini-v3.3"},
            "not_configured",
        ),
        (
            {
                "DINING_LLM_PROVIDER": "ilmu",
                "DINING_LLM_MODEL": "ilmu-mini-v3.3",
                "DINING_LLM_API_KEY": "secret",
                "DINING_LLM_BASE_URL": "http://remote.example/v1",
            },
            "not_configured",
        ),
        (
            {
                "DINING_LLM_PROVIDER": "ilmu",
                "DINING_LLM_MODEL": "ilmu-mini-v3.3",
                "DINING_LLM_API_KEY": "secret",
                "DINING_LLM_TIMEOUT_SECONDS": "nan",
            },
            "not_configured",
        ),
        (
            {
                "DINING_LLM_PROVIDER": "ilmu",
                "DINING_LLM_MODEL": "ilmu-mini-v3.3",
                "DINING_LLM_API_KEY": "secret",
                "DINING_LLM_MAX_OUTPUT_TOKENS": "99999",
            },
            "not_configured",
        ),
    ]
    for env, status in cases:
        result = invoke_explanations(InferenceSettings.from_env(env), ["option-a"])
        assert result.metadata["model_status"] == status
        assert result.metadata["model_calls"] == 0
        assert result.metadata["input_tokens"] == 0
    result = invoke_explanations(InferenceSettings.from_env({}, use_model=True), [])
    assert result.metadata["model_status"] == "skipped_no_options"
    assert result.metadata["model_calls"] == 0


def test_endpoint_rejects_credentials_queries_fragments_and_allows_local_http():
    env = {
        "DINING_LLM_PROVIDER": "ilmu",
        "DINING_LLM_MODEL": "ilmu-mini-v3.3",
        "DINING_LLM_API_KEY": "secret",
    }
    for url in [
        "https://user:password@example.com/v1",
        "https://example.com/v1?key=secret",
        "https://example.com/v1#secret",
        "https://example.com:99999/v1",
        "file:///tmp/socket",
        "http://169.254.169.254/v1",
    ]:
        assert (
            InferenceSettings.from_env({**env, "DINING_LLM_BASE_URL": url}).error_code
            == "invalid_endpoint"
        )
    for url in [
        "http://127.0.0.1:8000/v1",
        "http://[::1]:8000/v1",
        "http://localhost:8000/v1",
    ]:
        assert InferenceSettings.from_env({**env, "DINING_LLM_BASE_URL": url}).enabled


def test_failed_provider_never_echoes_secrets_and_does_not_retry(monkeypatch):
    from dining.llm.inference import invoke_explanations

    calls = []

    class Provider:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            calls.append(messages)
            raise TimeoutError(
                "Authorization: private-test-key; private prompt content"
            )

    monkeypatch.setattr("langchain_openai.ChatOpenAI", Provider)
    result = invoke_explanations(
        InferenceSettings.from_env({}, use_model=True), ["option-a"]
    )
    assert result.labels is None
    assert result.metadata["inference_status"] == "provider_error"
    assert result.metadata["model_calls"] == 1
    assert result.metadata["usage_source"] == "unknown"
    assert result.metadata["input_tokens"] is None
    assert len(calls) == 1
    assert "private" not in str(result)


def test_signature_changes_for_model_and_endpoint_but_not_secret_rotation():
    env = {
        "DINING_LLM_PROVIDER": "ilmu",
        "DINING_LLM_MODEL": "ilmu-mini-v3.3",
        "DINING_LLM_API_KEY": "secret",
    }
    settings = InferenceSettings.from_env(env)
    assert len(settings.signature) == 24
    assert (
        settings.signature
        == InferenceSettings.from_env(
            {**env, "DINING_LLM_API_KEY": "new-secret"}
        ).signature
    )
    assert (
        settings.signature
        != InferenceSettings.from_env(
            {**env, "DINING_LLM_MODEL": "other-explicit-model"}
        ).signature
    )
    assert (
        settings.signature
        != InferenceSettings.from_env(
            {**env, "DINING_LLM_BASE_URL": "https://different-endpoint.example/v1"}
        ).signature
    )


def test_standalone_probe_suppresses_ambient_tracing(monkeypatch):
    from langchain_core.messages import AIMessage
    from langsmith.run_helpers import get_tracing_context

    from dining.llm.inference import invoke_explanations

    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    class Provider:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            assert get_tracing_context()["enabled"] is False
            return AIMessage(content='{"option-a":["budget","nearby"]}')

    monkeypatch.setattr("langchain_openai.ChatOpenAI", Provider)
    result = invoke_explanations(
        InferenceSettings.from_env({}, use_model=True), ["option-a"]
    )
    assert result.metadata["model_status"] == "validated"


def test_provider_model_metadata_cannot_echo_the_api_key(monkeypatch):
    from langchain_core.messages import AIMessage

    from dining.llm.inference import invoke_explanations

    class Provider:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            return AIMessage(
                content='{"option-a":["budget","nearby"]}',
                response_metadata={"model_name": "private-test-key"},
            )

    monkeypatch.setattr("langchain_openai.ChatOpenAI", Provider)
    settings = InferenceSettings.from_env(
        {
            "DINING_LLM_PROVIDER": "ilmu",
            "DINING_LLM_MODEL": "ilmu-mini-v3.3",
            "DINING_LLM_API_KEY": "private-test-key",
        }
    )
    result = invoke_explanations(settings, ["option-a"])
    assert result.metadata["model_status"] == "validated"
    assert result.metadata["returned_model_id"] is None
    assert "private-test-key" not in str(result)
