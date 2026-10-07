"""Public structured-inference boundary tests; no external network access."""

import json

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel, ConfigDict

from dining.inference import InferenceSettings, invoke_json


class Taste(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    spice: str
    uncertain: bool


def configured():
    return InferenceSettings.from_env(
        {
            "DINING_LLM_PROVIDER": "ilmu",
            "DINING_LLM_MODEL": "ilmu-mini-v3.3",
            "DINING_LLM_API_KEY": "private-boundary-test-key",
        }
    )


def invoke(settings=None, **overrides):
    return invoke_json(
        settings or configured(),
        **{
            "payload": {"untrusted_text": "Something mild"},
            "instruction": "Return the typed taste object only.",
            "validate": lambda data: Taste.model_validate(data).model_dump(),
            "prompt_version": "boundary-test-v1",
            "role": "preference_draft",
            **overrides,
        },
    )


@pytest.mark.parametrize(
    "payload,instruction",
    [
        ({"untrusted_text": "x" * 8000}, "Return JSON."),
        ({"untrusted_text": "炒饭" * 1500}, "Return JSON."),
        ({"untrusted_text": "mild"}, "x" * 8000),
    ],
)
def test_input_budget_rejects_before_provider_construction(
    monkeypatch, payload, instruction
):
    constructions = []

    def provider(**kwargs):
        constructions.append(kwargs)
        raise AssertionError("Oversized prompts must not instantiate a provider")

    monkeypatch.setattr("langchain_openai.ChatOpenAI", provider)
    result = invoke(payload=payload, instruction=instruction)
    assert constructions == []
    assert result.data is None
    assert result.metadata["inference_status"] == "budget_exceeded"
    assert result.metadata["model_error_code"] == "input_budget_exceeded"
    assert result.metadata["model_calls"] == 0
    assert result.metadata["input_tokens"] == result.metadata["output_tokens"] == 0
    assert result.metadata["usage_source"] == "not_called"


def test_exact_budget_includes_utf8_instruction_payload_and_framing(monkeypatch):
    calls = []

    class Provider:
        def __init__(self, **kwargs):
            calls.append("constructed")

        def invoke(self, messages):
            calls.append(messages)
            return AIMessage(content='{"spice":"mild","uncertain":false}')

    monkeypatch.setattr("langchain_openai.ChatOpenAI", Provider)
    payload, instruction = {"text": "面条"}, "Only JSON: 辣"
    bound = (
        len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        + len(instruction.encode("utf-8"))
        + 256
    )
    rejected = invoke(payload=payload, instruction=instruction, input_budget=bound - 1)
    assert rejected.metadata["input_budget_bound"] == bound
    assert calls == []
    accepted = invoke(payload=payload, instruction=instruction, input_budget=bound)
    assert accepted.data == {"spice": "mild", "uncertain": False}
    assert len(calls) == 2
    assert accepted.metadata["model_calls"] == 1


@pytest.mark.parametrize(
    "content",
    [
        '{"spice":2,"uncertain":false}',
        '{"spice":"mild","uncertain":"false"}',
        '{"spice":"mild","uncertain":false,"allergy_safe":true}',
        '{"spice":"mild","spice":"hot","uncertain":false}',
        '[{"spice":"mild","uncertain":false}]',
        '{"spice":"mild","uncertain":false} PRIVATE_RESPONSE_MARKER',
        [{"type": "text", "text": '{"spice":"mild","uncertain":false}'}],
    ],
)
def test_hostile_or_wrongly_typed_output_retains_usage_without_exposing_response(
    monkeypatch, content
):
    class Provider:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0

        def invoke(self, messages):
            return AIMessage(
                content=content,
                usage_metadata={
                    "input_tokens": 50,
                    "output_tokens": 12,
                    "total_tokens": 62,
                },
                response_metadata={
                    "model_name": "private-boundary-test-key",
                    "finish_reason": "stop",
                },
            )

    monkeypatch.setattr("langchain_openai.ChatOpenAI", Provider)
    result = invoke()
    assert result.data is None
    assert result.metadata["inference_status"] == "invalid_output"
    assert result.metadata["usage_source"] == "provider"
    assert result.metadata["input_tokens"] == 50
    assert result.metadata["output_tokens"] == 12
    assert result.metadata["model_calls"] == 1
    assert result.metadata["returned_model_id"] is None
    assert "PRIVATE_RESPONSE_MARKER" not in repr(result)
    assert "private-boundary-test-key" not in repr(result)
    assert "allergy_safe" not in repr(result)


def test_provider_exception_is_sanitized_and_never_retried(monkeypatch):
    calls = []

    class Provider:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0

        def invoke(self, messages):
            calls.append(1)
            raise RuntimeError("private-boundary-test-key PRIVATE_USER_TEXT")

    monkeypatch.setattr("langchain_openai.ChatOpenAI", Provider)
    result = invoke(payload={"untrusted_text": "PRIVATE_USER_TEXT"})
    assert calls == [1]
    assert result.data is None
    assert result.metadata["inference_status"] == "provider_error"
    assert result.metadata["usage_source"] == "unknown"
    assert result.metadata["input_tokens"] is None
    assert result.metadata["output_tokens"] is None
    assert "private-boundary-test-key" not in repr(result)
    assert "PRIVATE_USER_TEXT" not in repr(result)
