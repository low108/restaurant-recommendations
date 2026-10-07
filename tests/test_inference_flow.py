"""Application/worker flow with a simulated provider; never live inference."""

import json
from dataclasses import replace

from langchain_core.messages import AIMessage
from test_api import checkin, create_meal, make_client, setup_room
from test_recommendation import ready_catalog, snapshot

from dining.agent import DiningAgent
from dining.generation import result_metadata
from dining.inference import InferenceSettings
from webapp import create_app


def test_queued_meal_records_actual_inference_in_result_and_operator_receipts(
    tmp_path, monkeypatch
):
    calls = []

    class Provider:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            calls.append(messages)
            options = json.loads(messages[1].content)["options"]
            return AIMessage(
                content=json.dumps({o["id"]: ["budget", "nearby"] for o in options}),
                usage_metadata={
                    "input_tokens": 80,
                    "output_tokens": 40,
                    "total_tokens": 120,
                },
            )

    monkeypatch.setattr("langchain_openai.ChatOpenAI", Provider)
    monkeypatch.setenv("DINING_OPERATIONS_TOKEN", "operator-test-" * 3)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    settings = InferenceSettings(
        "ilmu", "ilmu-mini-v3.3", "https://api.ilmu.ai/v1", "SECRET_KEY"
    )
    app = create_app(
        tmp_path / "app.sqlite3", catalog, demo_mode=True, inference_settings=settings
    )
    people = [make_client(app, name) for name in ("InferenceOwner", "InferenceFriend")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room, meal_at=snapshot()["meal"]["meal_at"])
    for person in people:
        meal = checkin(person, meal["id"], craving="PRIVATE_CRAVING_MARKER", budget=50)
    queued = people[0].post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    assert queued.status_code == 202
    assert app.state.generation_worker.tick() == 1
    result = people[0].get(f"/api/meals/{meal['id']}").json()
    assert result["status"] == "shortlisted"
    assert result["result"]["agent"]["inference_status"] == "validated"
    assert len(calls) == 1
    assert "PRIVATE_CRAVING_MARKER" not in str(calls)
    receipt = result["generation_history"][0]["attempts"][0]["metadata"]
    assert receipt["inference_status"] == "validated"
    assert receipt["provider"] == "ilmu"
    assert receipt["model_id"] == "ilmu-mini-v3.3"
    assert receipt["input_tokens"] == 80
    ops = people[0].get(
        "/api/operations/summary", headers={"X-Operations-Token": "operator-test-" * 3}
    )
    assert ops.json()["recent_runs"][0]["inference_status"] == "validated"
    assert "SECRET_KEY" not in ops.text
    assert "PRIVATE_CRAVING_MARKER" not in ops.text
    assert "SECRET_KEY" not in json.dumps(result)
    app.state.store.close()


def test_inference_change_replaces_old_job_but_secret_rotation_does_not(tmp_path):
    from test_generation_jobs import queued_pilot, request

    agent = DiningAgent(ready_catalog(), settings=InferenceSettings())
    store, router, people, meal = queued_pilot(tmp_path, agent)
    first = request(people, meal).json()
    router.generation_worker.tick()
    agent.settings = InferenceSettings(
        "ilmu", "ilmu-mini-v3.3", "https://api.ilmu.ai/v1", None
    )
    second = request(people, meal).json()
    assert second["generation_job"]["id"] != first["generation_job"]["id"]
    assert second["revision"] > first["revision"]
    agent.settings = replace(agent.settings, api_key="unused-key")
    ready = request(people, second).json()
    assert ready["generation_job"]["id"] != second["generation_job"]["id"]
    agent.settings = replace(agent.settings, api_key="rotated-unused-key")
    assert (
        request(people, ready).json()["generation_job"]["id"]
        == ready["generation_job"]["id"]
    )
    store.close()


def test_historical_inference_fields_do_not_copy_arbitrary_adapter_values():
    result = {
        "status": "shortlisted",
        "options": [],
        "agent": {
            "inference_status": {"secret": "PRIVATE"},
            "provider": ["PRIVATE"],
            "model_status": "PRIVATE",
            "model_id": "PRIVATE\nPROMPT",
            "returned_model_id": {"private": 1},
            "model_error_code": "PRIVATE",
            "role": "PRIVATE",
            "configuration_status": "PRIVATE",
        },
    }
    safe = result_metadata(result)
    assert "PRIVATE" not in json.dumps(safe)
    assert safe["inference_status"] == "unknown"
