from dining.eval_harness import (
    EVALUATION_CASES,
    EvaluationHarness,
    run_synthetic_evaluation,
)
from dining.inference import InferenceSettings


def test_synthetic_evaluation_runs_all_categories_without_real_data():
    settings = InferenceSettings.from_env(
        {
            "DINING_LLM_PROVIDER": "ilmu",
            "DINING_LLM_MODEL": "ilmu-mini-v3.3",
            "DINING_LLM_API_KEY": "fake-eval-key",
        }
    )
    harness = EvaluationHarness(settings)
    assert len(harness.cases) >= 8

    categories = {case.category for case in harness.cases}
    expected_categories = {
        "english",
        "bahasa_melayu",
        "mixed_language",
        "ambiguous",
        "prohibited_dietary",
        "invalid_json",
        "invented_labels",
        "unavailable_provider",
    }
    assert expected_categories.issubset(categories)

    report = harness.run()
    assert report.total_cases >= 8
    assert report.model_id == "ilmu-mini-v3.3"
    assert report.prompt_version == "taste-draft-v1"
    assert report.contract_compliance["schema_validity_rate"] == 1.0
    assert report.contract_compliance["allowed_label_precision"] == 1.0
    assert report.contract_compliance["safety_violations"] == 0
    assert report.contract_compliance["fallback_rate"] >= 0.0


def test_prohibited_dietary_cases_never_publish_unsafe_claims():
    settings = InferenceSettings.from_env(
        {
            "DINING_LLM_PROVIDER": "ilmu",
            "DINING_LLM_MODEL": "ilmu-mini-v3.3",
            "DINING_LLM_API_KEY": "fake-eval-key",
        }
    )
    harness = EvaluationHarness(settings)
    dietary_cases = [c for c in harness.cases if c.category == "prohibited_dietary"]
    assert len(dietary_cases) >= 1

    for case in dietary_cases:
        res = harness.evaluate_case(case)
        # Prohibited dietary input must be caught locally or flagged for review
        assert res["status"] in {"needs_requirement_review", "unavailable"}
        # Must not propose dietary safety or medical conclusions
        suggestions = res.get("suggestions", {})
        assert "halal" not in suggestions.get("cuisines", [])
        assert "allergy" not in str(suggestions)


def test_report_clearly_separates_contract_compliance_from_quality():
    report = run_synthetic_evaluation()
    data = report.to_dict()

    assert "contract_compliance" in data
    assert "recommendation_quality" in data
    assert "token_usage" in data
    assert "latency" in data

    # Contract compliance metrics
    comp = data["contract_compliance"]
    assert "schema_validity_rate" in comp
    assert "allowed_label_precision" in comp
    assert "fallback_rate" in comp
    assert "safety_violations" in comp

    # Recommendation quality metrics
    qual = data["recommendation_quality"]
    assert "language_coverage" in qual
    assert "ambiguity_detection_rate" in qual
    assert "soft_budget_extraction_rate" in qual


def test_invented_labels_and_invalid_json_trigger_safe_fallback():
    settings = InferenceSettings.from_env(
        {
            "DINING_LLM_PROVIDER": "ilmu",
            "DINING_LLM_MODEL": "ilmu-mini-v3.3",
            "DINING_LLM_API_KEY": "fake-eval-key",
        }
    )
    harness = EvaluationHarness(settings)
    invalid_json_case = next(c for c in harness.cases if c.category == "invalid_json")
    res1 = harness.evaluate_case(invalid_json_case)
    assert res1["status"] in {"unavailable", "fallback"}
    assert res1["suggestions"] == {}

    invented_case = next(c for c in harness.cases if c.category == "invented_labels")
    res2 = harness.evaluate_case(invented_case)
    assert res2["status"] in {"unavailable", "fallback"}
    assert res2["suggestions"] == {}
