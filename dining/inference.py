"""Explicit server-side inference configuration and bounded explanation labels.

The deterministic recommender remains authoritative. A model may choose two
pre-approved labels for each already eligible option; it cannot add restaurants,
change scores or assert dietary safety.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from langsmith import tracing_context

from .pricing import DEFAULT_PRICE_VERSION, calculate_cost

REASONS = {
    "group_fit": "A suitable menu choice for each included person",
    "budget": "Listed payable meal prices fit all submitted caps",
    "nearby": "Within your chosen area around the meeting point",
}
ROLE = "explanation_labels_only"
MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@+\-]{0,127}\Z")


def _safe_endpoint(value: str | None) -> bool:
    try:
        parts = urlsplit(value or "")
        if (
            not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            return False
        if parts.port is not None and not 1 <= parts.port <= 65535:
            return False
        if parts.scheme == "https":
            return True
        if parts.scheme != "http":
            return False
        return (
            parts.hostname == "localhost"
            or ipaddress.ip_address(parts.hostname).is_loopback
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class InferenceSettings:
    provider: str = "disabled"
    model: str | None = None
    base_url: str | None = field(default=None, repr=False)
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = 15
    max_output_tokens: int = 384
    config_error: str | None = None
    price_version: str = DEFAULT_PRICE_VERSION

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None, *, use_model: bool = False
    ):
        env = os.environ if environ is None else environ
        provider = (
            env.get("DINING_LLM_PROVIDER", "ollama" if use_model else "disabled")
            .strip()
            .lower()
        )
        model = env.get("DINING_LLM_MODEL", "").strip() or None
        base_url = env.get("DINING_LLM_BASE_URL", "").strip() or None
        api_key = env.get("DINING_LLM_API_KEY", "").strip() or None
        price_version = (
            env.get("DINING_PRICE_VERSION", DEFAULT_PRICE_VERSION).strip()
            or DEFAULT_PRICE_VERSION
        )
        if provider in {"ilmu", "openai_compatible"}:
            base_url = base_url or env.get("OPENAI_BASE_URL", "").strip() or None
            api_key = api_key or env.get("OPENAI_API_KEY", "").strip() or None
        if provider == "ollama":
            model = model or env.get("OLLAMA_MODEL", "llama3.1:8b").strip()
            base_url = (
                base_url
                or env.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1").strip()
            )
            api_key = api_key or "ollama"
        elif provider == "ilmu":
            base_url = base_url or "https://api.ilmu.ai/v1"
        error = None
        try:
            timeout = float(env.get("DINING_LLM_TIMEOUT_SECONDS", "15"))
            tokens = int(env.get("DINING_LLM_MAX_OUTPUT_TOKENS", "384"))
        except (TypeError, ValueError):
            timeout, tokens, error = 15, 384, "invalid_limits"
        return cls(
            provider,
            model,
            base_url,
            api_key,
            timeout,
            tokens,
            error,
            price_version,
        )

    @property
    def error_code(self) -> str | None:
        if self.provider == "disabled":
            return None
        if self.provider not in {"ilmu", "ollama", "openai_compatible"}:
            return "unsupported_provider"
        if self.config_error:
            return self.config_error
        if not self.model:
            return "model_required"
        if not MODEL_PATTERN.fullmatch(self.model):
            return "invalid_model"
        if not _safe_endpoint(self.base_url):
            return "invalid_endpoint"
        if not self.api_key:
            return "api_key_required"
        if (
            not 1 <= self.timeout_seconds <= 60
            or not 64 <= self.max_output_tokens <= 1024
        ):
            return "invalid_limits"
        return None

    @property
    def configuration_status(self) -> str:
        if self.provider == "disabled":
            return "disabled"
        return "not_configured" if self.error_code else "ready"

    @property
    def enabled(self) -> bool:
        return self.configuration_status == "ready"

    def public(self) -> dict:
        return {
            "provider": self.provider
            if self.provider in {"disabled", "ilmu", "ollama", "openai_compatible"}
            else "unknown",
            "model_id": self.model
            if self.model and MODEL_PATTERN.fullmatch(self.model)
            else None,
            "configuration_status": self.configuration_status,
            "error_code": self.error_code,
            "role": ROLE,
        }

    @property
    def signature(self) -> str:
        payload = {
            **self.public(),
            "endpoint": self.base_url,
            "timeout": self.timeout_seconds,
            "max_tokens": self.max_output_tokens,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[
            :24
        ]


@dataclass(frozen=True)
class InferenceResult:
    labels: dict | None
    metadata: dict

    @property
    def data(self):
        return self.labels


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate key")
        value[key] = item
    return value


def invoke_json(
    settings: InferenceSettings,
    *,
    payload: dict,
    instruction: str,
    validate,
    prompt_version: str,
    role: str,
    skip: bool = False,
    input_budget: int = 8000,
) -> InferenceResult:
    """One request, no repair loop; caller keeps template reasons on any failure."""
    configuration = settings.public()
    metadata = {
        "framework": "langgraph",
        "provider": configuration["provider"],
        "configuration_status": configuration["configuration_status"],
        "role": role,
        "model_calls": 0,
        "model_status": settings.configuration_status,
        "inference_status": settings.configuration_status,
        "model_error_code": settings.error_code,
        "usage": {},
        "usage_source": "not_called",
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": None,
        "prompt_version": prompt_version,
        "model_id": configuration["model_id"],
        "returned_model_id": None,
        "tool_calls": 0,
        "estimated_cost": None,
        "currency": None,
        "price_version": None,
    }
    if not settings.enabled:
        return InferenceResult(None, metadata)
    if skip:
        metadata.update(
            model_status="skipped_no_options", inference_status="skipped_no_options"
        )
        return InferenceResult(None, metadata)
    serialized = json.dumps(payload, ensure_ascii=False)
    # UTF-8 bytes plus a framing reserve conservatively bound input before a call;
    # provider token usage remains authoritative when available.
    input_bound = (
        len(serialized.encode("utf-8")) + len(instruction.encode("utf-8")) + 256
    )
    metadata["input_budget_bound"] = input_bound
    if input_bound > input_budget:
        metadata.update(
            model_status="template_fallback",
            inference_status="budget_exceeded",
            model_error_code="input_budget_exceeded",
        )
        return InferenceResult(None, metadata)
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_openai import ChatOpenAI

        client = ChatOpenAI(
            model=settings.model,
            base_url=settings.base_url,
            api_key=settings.api_key,
            temperature=0,
            timeout=settings.timeout_seconds,
            max_retries=0,
            max_tokens=settings.max_output_tokens,
        )
        metadata.update(
            model_calls=1,
            usage_source="unknown",
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            cached_tokens=None,
            estimated_cost=None,
            currency=None,
            price_version=None,
        )
        with tracing_context(enabled=False):
            response = client.invoke(
                [
                    SystemMessage(content=instruction),
                    HumanMessage(content=serialized),
                ]
            )
    except Exception:  # noqa: BLE001 - external provider failures always retain safe templates.
        metadata.update(
            model_status="template_fallback",
            inference_status="provider_error",
            model_error_code="provider_error",
            total_tokens=None,
            cached_tokens=None,
            estimated_cost=None,
            currency=None,
            price_version=None,
        )
        return InferenceResult(None, metadata)
    raw_usage = response.usage_metadata
    if not isinstance(raw_usage, dict):
        resp_meta = response.response_metadata or {}
        raw_usage = resp_meta.get("token_usage") or resp_meta.get("usage")
    if not isinstance(raw_usage, dict):
        raw_usage = {}

    raw_in = raw_usage.get("input_tokens")
    raw_out = raw_usage.get("output_tokens")
    raw_total = raw_usage.get("total_tokens")
    raw_cached = (
        raw_usage.get("cached_tokens")
        or raw_usage.get("cache_read_input_tokens")
        or (
            (raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            if isinstance(raw_usage.get("prompt_tokens_details"), dict)
            else None
        )
    )

    if type(raw_in) is int and raw_in >= 0 and type(raw_out) is int and raw_out >= 0:
        valid_in = raw_in
        valid_out = raw_out
        valid_total = (
            raw_total
            if (type(raw_total) is int and raw_total >= 0)
            else (valid_in + valid_out)
        )
        valid_cached = (
            raw_cached if (type(raw_cached) is int and raw_cached >= 0) else None
        )
        usage_source = "provider"
        cost_calc = calculate_cost(
            provider=settings.provider,
            model=settings.model,
            input_tokens=valid_in,
            output_tokens=valid_out,
            price_version=settings.price_version,
        )
    else:
        valid_in = None
        valid_out = None
        valid_total = None
        valid_cached = None
        usage_source = "unknown"
        cost_calc = calculate_cost(
            provider=None,
            model=None,
            input_tokens=None,
            output_tokens=None,
            price_version=None,
        )

    usage = {
        key: val
        for key, val in {
            "input_tokens": valid_in,
            "output_tokens": valid_out,
            "total_tokens": valid_total,
            "cached_tokens": valid_cached,
        }.items()
        if val is not None
    }
    returned_model = (response.response_metadata or {}).get("model_name")
    metadata.update(
        usage=usage,
        usage_source=usage_source,
        input_tokens=cost_calc.input_tokens,
        output_tokens=cost_calc.output_tokens,
        total_tokens=cost_calc.total_tokens,
        cached_tokens=valid_cached,
        estimated_cost=cost_calc.estimated_cost,
        currency=cost_calc.currency,
        price_version=cost_calc.price_version,
        returned_model_id=returned_model
        if isinstance(returned_model, str)
        and MODEL_PATTERN.fullmatch(returned_model)
        and settings.api_key not in returned_model
        else None,
    )
    try:
        if (
            (response.response_metadata or {}).get("finish_reason")
            not in {None, "stop"}
            or (response.additional_kwargs or {}).get("refusal")
            or response.tool_calls
        ):
            raise ValueError("Unfinished or refused output")
        if not isinstance(response.content, str) or len(response.content) > 8192:
            raise ValueError("Output size or type invalid")
        content = response.content.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", content)
        if fenced:
            content = fenced.group(1)
        labels = json.loads(content, object_pairs_hook=_unique_object)
        if not isinstance(labels, dict):
            raise TypeError("Expected object")
        labels = validate(labels)
    except (ValueError, TypeError):
        metadata.update(
            model_status="template_fallback",
            inference_status="invalid_output",
            model_error_code="invalid_output",
        )
        return InferenceResult(None, metadata)
    metadata.update(model_status="validated", inference_status="validated")
    return InferenceResult(labels, metadata)


def invoke_explanations(
    settings: InferenceSettings, option_ids: list[str]
) -> InferenceResult:
    def validate(value):
        if set(value) != set(option_ids):
            raise ValueError("Invalid IDs")
        for values in value.values():
            if (
                not isinstance(values, list)
                or len(values) != 2
                or any(not isinstance(v, str) or v not in REASONS for v in values)
                or len(set(values)) != 2
            ):
                raise ValueError("Invalid labels")
        return value

    return invoke_json(
        settings,
        payload={
            "options": [
                {"id": i, "allowed_reason_ids": list(REASONS)} for i in option_ids
            ]
        },
        # v3: ilmu-mini-v3.3 answered v2 with one string per option; the explicit array
        # shape fixes that without loosening validation (verified live, 7 Oct 2026).
        instruction=(
            "Return only a JSON object. Use every option id exactly once as a key. "
            "Each value must be a JSON array of exactly two different strings chosen "
            "from that option's allowed_reason_ids. "
            'Example shape: {"<option id>": ["group_fit", "budget"]}. '
            "No other keys, text, tools or code fences."
        ),
        validate=validate,
        prompt_version="allowed-reasons-v3",
        role="explanation_labels_only",
        skip=not option_ids,
    )
