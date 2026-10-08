"""Redacted opt-in LangSmith tracing abstraction.

Tracing is disabled by default and requires explicit server configuration.
Exported metadata is strictly structural:
  - run_id: unique run identifier
  - session_ref: opaque hash of room/meal session
  - stage_name: structural pipeline stage (e.g. "before_model", "after_model")
  - duration_ms: stage duration in milliseconds
  - status: stage execution status (e.g. "ok", "skipped", "failed")
  - error_code: bounded error code (if any)
  - model_calls: integer count of model calls
  - tool_names: allowlisted list of called tool names
  - token_usage: input, output, and total token counts

Prohibited from export (strictly redacted):
  - Prompts containing diner text or cravings
  - Allergies, medical inferences, and dietary requirements
  - Precise GPS coordinates or locations
  - Feedback comments or ratings
  - User emails, names, or credentials
  - Restaurant source text
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any

SENSITIVE_PATTERNS = re.compile(
    r"\b(?:allergy|allergies|allergic|peanut|nuts?|shellfish|halal|haram|pork|gluten|gps|latitude|longitude|sk-[\w-]+|password|secret|token)\b|\d{1,3}\.\d{3,}",
    re.IGNORECASE,
)

ALLOWLISTED_KEYS = {
    "run_id",
    "session_ref",
    "stage_name",
    "duration_ms",
    "status",
    "error_code",
    "model_calls",
    "tool_names",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_tokens",
    "estimated_cost",
    "currency",
    "price_version",
    "input_count",
    "output_count",
    "input_hash",
    "output_hash",
}


@dataclass(frozen=True)
class TracingSettings:
    """LangSmith tracing settings from ``LANGSMITH_*`` environment variables."""

    api_key: str = field(default="", repr=False)
    project: str = "makan-together"
    endpoint: str = "https://api.smith.langchain.com"
    _enabled: bool = field(default=False, repr=False)

    def __init__(
        self,
        enabled: bool = False,
        api_key: str = "",
        project: str = "makan-together",
        endpoint: str = "https://api.smith.langchain.com",
    ):
        object.__setattr__(self, "_enabled", enabled)
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "project", project)
        object.__setattr__(self, "endpoint", endpoint)

    @property
    def is_configured(self) -> bool:
        """True when tracing is on and has a key and project."""
        return bool(self._enabled and self.api_key.strip() and self.project.strip())

    @property
    def enabled(self) -> bool:
        """Alias for ``is_configured``."""
        return self.is_configured

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> TracingSettings:
        """Settings from the environment (off unless LANGSMITH_TRACING is true)."""
        environ = os.environ if env is None else env
        raw_enabled = environ.get("LANGSMITH_TRACING", "false").casefold()
        enabled = raw_enabled in ("true", "1", "yes")
        return cls(
            enabled=enabled,
            api_key=environ.get("LANGSMITH_API_KEY", ""),
            project=environ.get("LANGSMITH_PROJECT", "makan-together"),
            endpoint=environ.get(
                "LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"
            ),
        )


def _hash_items(items: Any) -> str:
    serialized = str(len(items)) + ":" + ":".join(type(x).__name__ for x in items)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


class RedactedTraceExporter:
    """Exports run stages with structural metadata only; private content is dropped."""

    def __init__(self, settings: TracingSettings, client: Any = None):
        self.settings = settings
        self.client = client

    def record_stage(
        self,
        run_id: str,
        session_ref: str,
        stage_name: str,
        duration_ms: float,
        status: str,
        raw_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Export one stage's timing and status (allowlisted metadata only)."""
        if not self.settings.is_configured and self.client is None:
            return

        metadata = {
            "run_id": run_id,
            "session_ref": session_ref,
            "stage_name": stage_name,
            "duration_ms": duration_ms,
            "status": status,
        }

        if raw_metadata:
            for k, v in raw_metadata.items():
                if (
                    k in ALLOWLISTED_KEYS
                    and k not in metadata
                    and isinstance(v, (int, float, bool, str))
                ):
                    # Double-check string does not contain sensitive text
                    if isinstance(v, str) and SENSITIVE_PATTERNS.search(v):
                        continue
                    metadata[k] = v

        try:
            if self.client:
                self.client.create_run(**metadata)
        except Exception:  # noqa: BLE001, S110 - Exporter failure must NEVER crash generation
            pass

    def record_middleware_step(
        self,
        run_id: str,
        stage_name: str,
        input_items: list[Any],
        output_items: list[Any],
        duration_ms: float,
    ) -> None:
        """Export a step's item counts and hashes, never the items."""
        if not self.settings.is_configured and self.client is None:
            return

        metadata = {
            "run_id": run_id,
            "stage_name": stage_name,
            "duration_ms": duration_ms,
            "input_count": len(input_items),
            "output_count": len(output_items),
            "input_hash": _hash_items(input_items),
            "output_hash": _hash_items(output_items),
            "status": "ok",
        }

        try:
            if self.client:
                self.client.create_run(**metadata)
        except Exception:  # noqa: BLE001, S110
            pass


_CURRENT_TRACE_EXPORTER: RedactedTraceExporter | None = None


def get_trace_exporter() -> RedactedTraceExporter:
    """The process-wide exporter, created from the environment on first use."""
    global _CURRENT_TRACE_EXPORTER
    if _CURRENT_TRACE_EXPORTER is None:
        _CURRENT_TRACE_EXPORTER = RedactedTraceExporter(TracingSettings.from_env())
    return _CURRENT_TRACE_EXPORTER


def set_trace_exporter(exporter: RedactedTraceExporter | None) -> None:
    """Replace the process-wide exporter (None resets it)."""
    global _CURRENT_TRACE_EXPORTER
    _CURRENT_TRACE_EXPORTER = exporter


def export_structural_run(
    run_id: str,
    stage_name: str,
    duration_ms: float,
    status: str,
    session_ref: str = "default",
) -> None:
    """Convenience: record one stage on the process-wide exporter."""
    exporter = get_trace_exporter()
    exporter.record_stage(
        run_id=run_id,
        session_ref=session_ref,
        stage_name=stage_name,
        duration_ms=duration_ms,
        status=status,
    )
