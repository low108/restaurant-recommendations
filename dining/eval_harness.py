"""Synthetic ILMU quality evaluation set and contract verification harness."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage

from .inference import InferenceSettings
from .preferences import CUISINES, interpret_preferences
from .ranking import DIMENSIONS, OCCASIONS


@dataclass(frozen=True)
class EvalCase:
    id: str
    category: str
    description: str
    input_text: str
    mock_response: Any = None
    expected_status: tuple[str, ...] = (
        "validated",
        "uncertain",
        "needs_requirement_review",
    )


EVALUATION_CASES: list[EvalCase] = [
    EvalCase(
        id="case_en_clean",
        category="english",
        description="Standard English craving with budget and spice",
        input_text="Craving spicy noodle soup, regular appetite, budget around RM25",
        mock_response=AIMessage(
            content=json.dumps(
                {
                    "cuisines": ["Chinese"],
                    "dish_families": ["noodles", "soup"],
                    "flavour_tags": ["spicy"],
                    "appetite": "regular",
                    "spice": "hot",
                    "soft_budget_target": 25.0,
                    "occasion_features": [],
                    "novelty": "any",
                    "needs_requirement_review": False,
                    "uncertain": False,
                }
            ),
            usage_metadata={
                "input_tokens": 85,
                "output_tokens": 42,
                "total_tokens": 127,
            },
            response_metadata={"model_name": "ilmu-mini-v3.3"},
        ),
        expected_status=("proposed",),
    ),
    EvalCase(
        id="case_bm_clean",
        category="bahasa_melayu",
        description="Standard Bahasa Melayu craving with spice and appetite",
        input_text="Teringin makan nasi lemak sambal pedas, nak makan kenyang",
        mock_response=AIMessage(
            content=json.dumps(
                {
                    "cuisines": ["Malay", "Malaysian"],
                    "dish_families": ["rice"],
                    "flavour_tags": ["spicy"],
                    "appetite": "hearty",
                    "spice": "hot",
                    "soft_budget_target": None,
                    "occasion_features": [],
                    "novelty": "any",
                    "needs_requirement_review": False,
                    "uncertain": False,
                }
            ),
            usage_metadata={
                "input_tokens": 80,
                "output_tokens": 40,
                "total_tokens": 120,
            },
            response_metadata={"model_name": "ilmu-mini-v3.3"},
        ),
        expected_status=("proposed",),
    ),
    EvalCase(
        id="case_mixed_craving",
        category="mixed_language",
        description="Mixed Manglish food terms and budget constraint",
        input_text="Jom makan dim sum or roti canai, budget around RM 15, not too heavy",
        mock_response=AIMessage(
            content=json.dumps(
                {
                    "cuisines": ["Chinese", "Mamak"],
                    "dish_families": ["noodles"],
                    "flavour_tags": ["savoury"],
                    "appetite": "light",
                    "spice": "mild",
                    "soft_budget_target": 15.0,
                    "occasion_features": [],
                    "novelty": "any",
                    "needs_requirement_review": False,
                    "uncertain": False,
                }
            ),
            usage_metadata={
                "input_tokens": 92,
                "output_tokens": 45,
                "total_tokens": 137,
            },
            response_metadata={"model_name": "ilmu-mini-v3.3"},
        ),
        expected_status=("proposed",),
    ),
    EvalCase(
        id="case_ambiguous_request",
        category="ambiguous",
        description="Conflicting taste and unclear food preference",
        input_text="I want food that is extremely fiery spicy and completely not spicy simultaneously",
        mock_response=AIMessage(
            content=json.dumps(
                {
                    "cuisines": [],
                    "dish_families": [],
                    "flavour_tags": [],
                    "appetite": None,
                    "spice": None,
                    "soft_budget_target": None,
                    "occasion_features": [],
                    "novelty": "any",
                    "needs_requirement_review": False,
                    "uncertain": True,
                }
            ),
            usage_metadata={
                "input_tokens": 75,
                "output_tokens": 30,
                "total_tokens": 105,
            },
            response_metadata={"model_name": "ilmu-mini-v3.3"},
        ),
        expected_status=("uncertain",),
    ),
    EvalCase(
        id="case_prohibited_dietary_allergy",
        category="prohibited_dietary",
        description="Severe allergen and halal requirement which must never enter taste learning",
        input_text="Saya ada alahan kacang tanah yang teruk dan hanya boleh makan halal",
        mock_response=None,  # Intercepted locally prior to model invocation
        expected_status=("needs_requirement_review",),
    ),
    EvalCase(
        id="case_invalid_json_format",
        category="invalid_json",
        description="Unparseable JSON or corrupted output from provider",
        input_text="Craving fried chicken",
        mock_response=AIMessage(
            content="Here is your recommendation: {not valid json...}",
            usage_metadata={
                "input_tokens": 60,
                "output_tokens": 20,
                "total_tokens": 80,
            },
            response_metadata={"model_name": "ilmu-mini-v3.3"},
        ),
        expected_status=("unavailable", "fallback"),
    ),
    EvalCase(
        id="case_invented_labels_hallucination",
        category="invented_labels",
        description="Model invents non-allowlisted tags and fictitious cuisines",
        input_text="Want something exotic",
        mock_response=AIMessage(
            content=json.dumps(
                {
                    "cuisines": ["MartianExoticCuisine"],
                    "dish_families": ["CosmicSteak"],
                    "flavour_tags": ["Radioactive"],
                    "appetite": "hearty",
                    "spice": "hot",
                    "soft_budget_target": None,
                    "occasion_features": [],
                    "novelty": "any",
                    "needs_requirement_review": False,
                    "uncertain": False,
                }
            ),
            usage_metadata={
                "input_tokens": 70,
                "output_tokens": 35,
                "total_tokens": 105,
            },
            response_metadata={"model_name": "ilmu-mini-v3.3"},
        ),
        expected_status=("unavailable", "fallback"),
    ),
    EvalCase(
        id="case_provider_network_failure",
        category="unavailable_provider",
        description="Provider times out or disconnects",
        input_text="Want chicken rice",
        mock_response=RuntimeError("Provider connection timeout"),
        expected_status=("unavailable", "fallback"),
    ),
]


@dataclass
class EvaluationReport:
    model_id: str
    prompt_version: str
    total_cases: int
    passed_cases: int
    contract_compliance: dict[str, Any]
    recommendation_quality: dict[str, Any]
    token_usage: dict[str, int]
    latency: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "contract_compliance": self.contract_compliance,
            "recommendation_quality": self.recommendation_quality,
            "token_usage": self.token_usage,
            "latency": self.latency,
        }


class EvaluationHarness:
    def __init__(
        self,
        settings: InferenceSettings,
        cases: list[EvalCase] | None = None,
    ):
        self.settings = settings
        self.cases = cases or list(EVALUATION_CASES)

    def evaluate_case(self, case: EvalCase) -> dict[str, Any]:
        class MockClient:
            def __init__(self, **kwargs):
                pass

            def invoke(self, messages):
                if isinstance(case.mock_response, Exception):
                    raise case.mock_response
                return case.mock_response

        # Monkeypatch or swap ChatOpenAI client if synthetic mock response provided
        import langchain_openai

        orig = langchain_openai.ChatOpenAI
        langchain_openai.ChatOpenAI = MockClient
        try:
            started = time.monotonic()
            result = interpret_preferences(self.settings, case.input_text)
            elapsed_ms = (time.monotonic() - started) * 1000
            result["latency_ms"] = elapsed_ms
            return result
        finally:
            langchain_openai.ChatOpenAI = orig

    def run(self) -> EvaluationReport:
        total = len(self.cases)
        passed = 0
        valid_schemas = 0
        precise_labels = 0
        fallbacks = 0
        safety_violations = 0
        ambiguity_detected = 0
        budgets_extracted = 0
        languages_handled = {"english": 0, "bahasa_melayu": 0, "mixed_language": 0}

        total_input_tokens = 0
        total_output_tokens = 0
        total_tokens = 0
        total_latency_ms = 0.0

        for case in self.cases:
            res = self.evaluate_case(case)
            status = res.get("status")
            agent = res.get("agent", {})
            suggestions = res.get("suggestions", {})

            # Token accounting
            total_input_tokens += agent.get("input_tokens") or 0
            total_output_tokens += agent.get("output_tokens") or 0
            total_tokens += agent.get("total_tokens") or 0
            total_latency_ms += res.get("latency_ms", 0.0)

            # Contract compliance evaluation
            is_valid_status = status in case.expected_status
            if is_valid_status:
                passed += 1

            if status in {
                "proposed",
                "uncertain",
                "needs_requirement_review",
                "unavailable",
            }:
                valid_schemas += 1

            if status == "unavailable":
                fallbacks += 1

            # Check label precision (no invented labels allowed)
            cuisines = suggestions.get("cuisines") or []
            dish_fams = suggestions.get("dish_families") or []
            flavours = suggestions.get("flavour_tags") or []
            occasions = suggestions.get("occasion_features") or []

            has_invented = (
                any(c not in CUISINES for c in cuisines)
                or any(d not in DIMENSIONS["dish"] for d in dish_fams)
                or any(f not in DIMENSIONS["flavour"] for f in flavours)
                or any(o not in OCCASIONS for o in occasions)
            )

            if not has_invented:
                precise_labels += 1

            # Safety violations check: prohibited dietary/allergy terms must never be emitted as taste tags
            dump = json.dumps(suggestions).lower()
            if any(term in dump for term in ("halal", "allergy", "peanuts", "kacang")):
                safety_violations += 1

            # Quality metrics
            if case.category in languages_handled and status == "proposed":
                languages_handled[case.category] += 1

            if case.category == "ambiguous" and status == "uncertain":
                ambiguity_detected += 1

            if suggestions.get("soft_budget_target") is not None:
                budgets_extracted += 1

        return EvaluationReport(
            model_id=self.settings.model or "ilmu-mini-v3.3",
            prompt_version="taste-draft-v1",
            total_cases=total,
            passed_cases=passed,
            contract_compliance={
                "schema_validity_rate": valid_schemas / total if total > 0 else 0.0,
                "allowed_label_precision": precise_labels / total if total > 0 else 0.0,
                "fallback_rate": fallbacks / total if total > 0 else 0.0,
                "safety_violations": safety_violations,
            },
            recommendation_quality={
                "language_coverage": languages_handled,
                "ambiguity_detection_rate": ambiguity_detected,
                "soft_budget_extraction_rate": budgets_extracted,
            },
            token_usage={
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_tokens,
            },
            latency={
                "avg_ms": round(total_latency_ms / total, 2) if total > 0 else 0.0,
            },
        )


def run_synthetic_evaluation(
    settings: InferenceSettings | None = None,
) -> EvaluationReport:
    active_settings = settings
    if active_settings is None or not active_settings.enabled:
        active_settings = InferenceSettings(
            provider="ilmu",
            model=(settings and settings.model) or "ilmu-mini-v3.3",
            base_url="https://api.ilmu.ai/v1",
            api_key="synthetic-probe-key",
        )
    harness = EvaluationHarness(active_settings)
    return harness.run()


def main(argv=None) -> int:
    import argparse
    from pathlib import Path

    from .runtime_env import load_env_file

    parser = argparse.ArgumentParser(description="ILMU quality evaluation harness")
    parser.add_argument("--env-file", type=Path, help="Path to environment file")
    parser.add_argument("--json", action="store_true", help="Output raw JSON report")
    args = parser.parse_args(argv)

    if args.env_file:
        load_env_file(args.env_file)

    settings = InferenceSettings.from_env()
    report = run_synthetic_evaluation(settings)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(
            f"ILMU Quality Evaluation Report: {report.model_id} (prompt: {report.prompt_version})"
        )
        print(f"Total Cases: {report.total_cases} | Passed: {report.passed_cases}")
        print("Contract Compliance:")
        for k, v in report.contract_compliance.items():
            print(f"  - {k}: {v}")
        print("Recommendation Quality:")
        for k, v in report.recommendation_quality.items():
            print(f"  - {k}: {v}")
        print(
            f"Tokens: {report.token_usage['total_tokens']} total | Avg Latency: {report.latency['avg_ms']} ms"
        )

    return 0 if report.passed_cases == report.total_cases else 1


if __name__ == "__main__":
    raise SystemExit(main())
