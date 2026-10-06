"""A bounded LangGraph pipeline; the model can select supported explanation labels only."""

from __future__ import annotations

import time
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from dining.catalog import Catalog
from dining.inference import REASONS, InferenceSettings, invoke_explanations
from dining.recommendation import Recommender
from dining.tracing import get_trace_exporter


class RunState(TypedDict, total=False):
    snapshot: dict
    result: dict
    stages: list[dict]
    run_id: str


def receipt(stage, started, status="ok", changed_paths=()):
    """Structural receipts never serialize a snapshot, prompt or provider error."""
    return {
        "receipt_ref": uuid4().hex,
        "stage": stage,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "status": status,
        "changed_paths": list(changed_paths),
        "projection": "structural_only",
        "diff_note": "Private values are intentionally omitted; this is not a full message diff.",
        "policy_version": "dining-boundary-v1",
    }


class DiningAgent:
    def __init__(
        self,
        catalog: Catalog,
        use_model: bool = False,
        settings: InferenceSettings | None = None,
    ):
        self.recommender = Recommender(catalog)
        self.revalidate = self.recommender
        self.settings = settings or InferenceSettings.from_env(use_model=use_model)
        self.use_model = self.settings.enabled
        graph = StateGraph(RunState)
        graph.add_node("check_and_rank", self._recommend)
        graph.add_node("explain_supported_options", self._explain)
        graph.add_edge(START, "check_and_rank")
        graph.add_edge("check_and_rank", "explain_supported_options")
        graph.add_edge("explain_supported_options", END)
        self.graph = graph.compile()

    def _recommend(self, state: RunState) -> dict:
        started = time.monotonic()
        result = self.recommender(state["snapshot"])
        return {
            "result": result,
            "stages": [
                *state["stages"],
                receipt(
                    "check_and_rank",
                    started,
                    result["status"],
                    ("result.options", "result.coverage"),
                ),
            ],
        }

    def _explain(self, state: RunState) -> dict:
        result = state["result"]
        started = time.monotonic()
        stages = [
            *state["stages"],
            receipt(
                "before_model",
                started,
                "ok" if self.settings.enabled and result["options"] else "skipped",
                ("model.allowed_options",)
                if self.settings.enabled and result["options"]
                else (),
            ),
        ]
        started = time.monotonic()
        inference = invoke_explanations(
            self.settings, [option["id"] for option in result["options"]]
        )
        result["agent"] = inference.metadata
        if inference.labels is not None:
            for option in result["options"]:
                option["reasons"] = [
                    REASONS[key] for key in inference.labels[option["id"]]
                ]
        return {
            "result": result,
            "stages": [
                *stages,
                receipt(
                    "after_model",
                    started,
                    result["agent"]["inference_status"],
                    ("result.options.reasons",) if inference.labels is not None else (),
                ),
            ],
        }

    def __call__(self, snapshot: dict) -> dict:
        started = time.monotonic()
        # Keep full private snapshots out of native LangSmith traces, regardless of ambient env.
        with tracing_context(enabled=False):
            state = self.graph.invoke(
                {
                    "snapshot": snapshot,
                    "run_id": uuid4().hex,
                    "stages": [
                        receipt("before_agent", started, "ok", ("snapshot.frozen",))
                    ],
                },
                config={"recursion_limit": 5},
            )
        result = state["result"]
        result["run_id"] = state["run_id"]
        result["agent"]["stages"] = [
            *state["stages"],
            receipt("after_agent", started, "completed", ("result",)),
        ]
        result["agent"]["trace_export"] = "disabled"
        result["agent"]["elapsed_ms"] = round((time.monotonic() - started) * 1000)

        try:
            exporter = get_trace_exporter()
            for st in result["agent"]["stages"]:
                raw_stage_metadata = None
                if st.get("stage") == "after_model":
                    raw_stage_metadata = {
                        "model_calls": result["agent"].get("model_calls"),
                        "input_tokens": result["agent"].get("input_tokens"),
                        "output_tokens": result["agent"].get("output_tokens"),
                        "total_tokens": result["agent"].get("total_tokens"),
                        "cached_tokens": result["agent"].get("cached_tokens"),
                        "estimated_cost": result["agent"].get("estimated_cost"),
                        "currency": result["agent"].get("currency"),
                        "price_version": result["agent"].get("price_version"),
                    }
                exporter.record_stage(
                    run_id=result["run_id"],
                    session_ref=snapshot.get("meal", {}).get("id", "default"),
                    stage_name=st.get("stage", "stage"),
                    duration_ms=st.get("duration_ms", 0),
                    status=st.get("status", "ok"),
                    raw_metadata=raw_stage_metadata,
                )
        except Exception:  # noqa: BLE001, S110 - trace exporter failures must never crash generation
            pass

        return result
