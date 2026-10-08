import json

from fastapi.testclient import TestClient
from test_recommendation import ready_catalog, snapshot

from dining.recommendation.agent import DiningAgent
from webapp import create_app


def test_every_agent_stage_has_a_safe_receipt_and_missing_usage_is_unknown():
    meal = snapshot()
    meal["participants"][0]["profile"]["private_note"] = "SECRET_HEALTH_709"
    meal["meal"]["private_origin"] = "SECRET_GPS_709"
    result = DiningAgent(ready_catalog())(meal)
    metrics = result["agent"]
    assert metrics["usage_source"] == "not_called"
    assert metrics["input_tokens"] == metrics["output_tokens"] == 0
    assert [stage["stage"] for stage in metrics["stages"]] == [
        "before_agent",
        "check_and_rank",
        "before_model",
        "after_model",
        "after_agent",
    ]
    for stage in metrics["stages"]:
        assert stage["receipt_ref"]
        assert stage["duration_ms"] >= 0
        assert stage["projection"] == "structural_only"
    serialized = json.dumps(metrics)
    assert "SECRET_HEALTH_709" not in serialized
    assert "SECRET_GPS_709" not in serialized
    assert "allergy_status" not in serialized


def test_failed_provider_call_retains_unknown_usage_and_safe_error(monkeypatch):
    class FailedProvider:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            raise RuntimeError("SECRET_PROVIDER_709")

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FailedProvider)
    result = DiningAgent(ready_catalog(), use_model=True)(snapshot())
    metrics = result["agent"]
    assert metrics["model_status"] == "template_fallback"
    assert metrics["usage_source"] == "unknown"
    assert metrics["input_tokens"] is None and metrics["output_tokens"] is None
    assert "SECRET_PROVIDER_709" not in json.dumps(metrics)


def test_operations_requires_separate_operator_access(tmp_path, monkeypatch):
    monkeypatch.setenv("DINING_OPERATIONS_TOKEN", "test-operator-709-" * 3)
    app = create_app(db_path=tmp_path / "ops.sqlite3")
    client = TestClient(app)
    assert client.get("/api/operations/summary").status_code == 403
    response = client.get(
        "/api/operations/summary",
        headers={"X-Operations-Token": "test-operator-709-" * 3},
    )
    assert response.status_code == 200
    assert response.json()["projection"] == "structural_only"
    assert response.json()["recent_runs"] == []
    app.state.store.close()


def test_operator_job_receipts_survive_result_invalidation_without_private_answers(
    tmp_path, monkeypatch
):
    from test_api import checkin, create_meal, make_client, setup_room

    monkeypatch.setenv("DINING_OPERATIONS_TOKEN", "test-operator-709-" * 3)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "ops-jobs.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("OpsOwner", "OpsFriend")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room, meal_at=snapshot()["meal"]["meal_at"])
    for person in people:
        meal = checkin(
            person, meal["id"], craving="PRIVATE_OBSERVABILITY_CRAVING", budget=50
        )
    queued = people[0].post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    assert queued.status_code == 202
    app.state.generation_worker.tick()
    people[0].patch("/api/profile", json={"spice": "hot"})
    result = people[0].get(
        "/api/operations/summary",
        headers={"X-Operations-Token": "test-operator-709-" * 3},
    )
    assert result.status_code == 200
    data = result.json()
    assert data["recent_runs"] == []  # The active result was invalidated.
    assert data["generation_jobs"]["published"] == 1
    job = data["recent_generation_jobs"][0]
    assert job["id"] == queued.json()["generation_job"]["id"]
    assert job["attempts"][0]["status"] == "published"
    assert "PRIVATE_OBSERVABILITY_CRAVING" not in result.text
    assert "opsowner@example.test" not in result.text
    assert '"profile"' not in result.text
    app.state.store.close()
