"""Durable generation behavior through HTTP and the worker's public tick interface."""

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from test_api import checkin, create_meal, make_client, setup_room
from test_recommendation import ready_catalog

from dining.agent import DiningAgent
from dining.api import build_router
from dining.store import DiningStore


def queued_pilot(tmp_path, recommend=None, **worker_options):
    store = DiningStore(tmp_path / "jobs.sqlite3")
    app = FastAPI()
    router = build_router(
        store,
        recommend or DiningAgent(ready_catalog()),
        async_generation=True,
        generation_options=worker_options,
    )
    app.include_router(router)
    people = [make_client(app, name) for name in ("QueueA", "QueueB", "Outsider")]
    room_id, _ = setup_room(people)
    at = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=4, minute=0, second=0
    )
    meal, _ = create_meal(people[0], room_id, meal_at=at.isoformat())
    for person in people[:2]:
        meal = checkin(person, meal["id"], budget=50)
    return store, router, people, meal


def request(people, meal):
    return people[0].post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )


def current(people, meal):
    return people[0].get(f"/api/meals/{meal['id']}").json()


def test_same_snapshot_queues_once_and_actual_graph_publishes_once(tmp_path):
    store, router, people, meal = queued_pilot(tmp_path)
    first = request(people, meal)
    assert first.status_code == 202, first.text
    queued = first.json()
    assert queued["status"] == "generating"
    assert queued["generation_job"]["status"] == "queued"
    second = request(people, meal)
    assert second.status_code == 202
    assert second.json()["generation_job"]["id"] == queued["generation_job"]["id"]
    assert router.generation_worker.tick() == 1
    completed = current(people, meal)
    assert completed["status"] == "shortlisted"
    assert len(completed["result"]["options"]) == 3
    assert completed["generation_job"]["status"] == "published"
    assert completed["generation_job"]["attempt_count"] == 1
    assert router.generation_worker.tick() == 0
    assert (
        request(people, meal).json()["generation_job"]["id"]
        == queued["generation_job"]["id"]
    )
    inbox = people[1].get("/api/notifications").json()
    assert (
        len([n for n in inbox["notifications"] if n["kind"] == "recommendations_ready"])
        == 1
    )
    assert people[2].get(f"/api/meals/{meal['id']}").status_code == 404
    history = str(completed["generation_history"])
    assert (
        "profile" not in history
        and "budget" not in history
        and people[1].user_id not in history
    )
    store.close()


def test_context_edit_immediately_supersedes_job_and_does_not_publish(tmp_path):
    store, router, people, meal = queued_pilot(tmp_path)
    queued = request(people, meal).json()
    changed = checkin(people[1], meal["id"], craving="PRIVATE_COULD_NOT_EAT", budget=40)
    assert changed["generation_job"] is None
    assert changed["generation_history"][0]["status"] == "superseded"
    assert router.generation_worker.tick() == 0
    assert current(people, meal)["result"] is None
    assert "PRIVATE_COULD_NOT_EAT" not in str(
        current(people, meal)["generation_history"]
    )
    again = request(people, changed)
    assert again.status_code == 202
    assert again.json()["generation_job"]["id"] != queued["generation_job"]["id"]
    assert router.generation_worker.tick() == 1
    assert current(people, meal)["generation_job"]["status"] == "published"
    store.close()


def test_restart_resumes_queued_job_without_changing_context(tmp_path):
    agent = DiningAgent(ready_catalog())
    store, _, people, meal = queued_pilot(tmp_path, agent)
    queued = request(people, meal).json()
    store.close()
    reopened = DiningStore(tmp_path / "jobs.sqlite3")
    app = FastAPI()
    recovered = build_router(reopened, agent, async_generation=True)
    app.include_router(recovered)
    from fastapi.testclient import TestClient

    owner = TestClient(app)
    owner.cookies.update(people[0].cookies)
    before = owner.get(f"/api/meals/{meal['id']}").json()
    assert before["revision"] == queued["revision"]
    assert before["generation_job"]["id"] == queued["generation_job"]["id"]
    assert recovered.generation_worker.tick() == 1
    after = owner.get(f"/api/meals/{meal['id']}").json()
    assert after["status"] == "shortlisted"
    assert after["generation_job"]["attempt_count"] == 1
    reopened.close()


def test_retry_budget_and_receipts_do_not_expose_provider_errors(tmp_path):
    clock = [1000.0]

    def unavailable(snapshot):
        raise RuntimeError("PRIVATE_PROVIDER_ERROR budget=50 allergy=SECRET")

    store, router, people, meal = queued_pilot(
        tmp_path, unavailable, clock=lambda: clock[0], retry_seconds=10
    )
    queued = request(people, meal).json()
    for attempt in range(1, 4):
        assert router.generation_worker.tick() == 1
        progress = current(people, meal)
        assert progress["generation_job"]["attempt_count"] == attempt
        assert "PRIVATE_PROVIDER_ERROR" not in str(progress)
        if attempt < 3:
            assert progress["generation_job"]["status"] == "retry_wait"
            assert router.generation_worker.tick() == 0
            retried = request(people, meal).json()
            assert retried["generation_job"]["id"] == queued["generation_job"]["id"]
    failed = current(people, meal)
    assert failed["generation_job"]["status"] == "failed"
    assert not failed["generation_job"]["retryable"]
    receipt = failed["generation_history"]
    assert (
        request(people, meal).json()["generation_job"]["id"]
        == queued["generation_job"]["id"]
    )
    assert router.generation_worker.tick() == 0
    assert current(people, meal)["generation_history"] == receipt
    store.close()


def test_only_one_worker_publishes_after_expired_lease_recovery(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    clock = [1000.0]
    started, resume = threading.Event(), threading.Event()
    actual = DiningAgent(ready_catalog())
    calls = [0]

    def delayed(snapshot):
        calls[0] += 1
        if calls[0] == 1:
            started.set()
            assert resume.wait(5)
        return actual(snapshot)

    delayed.recommender = actual.recommender
    store, router, people, meal = queued_pilot(
        tmp_path, delayed, clock=lambda: clock[0], timeout_seconds=3, lease_seconds=4
    )
    request(people, meal)
    other = build_router(
        store,
        delayed,
        async_generation=True,
        generation_options={
            "clock": lambda: clock[0],
            "timeout_seconds": 3,
            "lease_seconds": 4,
        },
    )
    with ThreadPoolExecutor() as pool:
        first = pool.submit(router.generation_worker.tick)
        assert started.wait(2)
        clock[0] += 5
        assert other.generation_worker.tick() == 1
        published = current(people, meal)
        assert published["status"] == "shortlisted"
        assert [a["status"] for a in published["generation_job"]["attempts"]] == [
            "interrupted",
            "published",
        ]
        resume.set()
        assert first.result(timeout=3) == 1
    assert (
        current(people, meal)["generation_history"] == published["generation_history"]
    )
    inbox = people[0].get("/api/notifications").json()
    assert (
        len([n for n in inbox["notifications"] if n["kind"] == "recommendations_ready"])
        == 1
    )
    store.close()


def test_evidence_change_during_call_supersedes_and_new_evidence_queues(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    started, resume = threading.Event(), threading.Event()
    actual = DiningAgent(ready_catalog())
    evidence = ["bundle-one"]

    def delayed(snapshot):
        started.set()
        assert resume.wait(3)
        return actual(snapshot)

    delayed.evidence_revision = lambda: evidence[0]
    store, router, people, meal = queued_pilot(tmp_path, delayed)
    old = request(people, meal).json()["generation_job"]["id"]
    with ThreadPoolExecutor() as pool:
        work = pool.submit(router.generation_worker.tick)
        assert started.wait(2)
        evidence[0] = "bundle-two"
        resume.set()
        assert work.result(timeout=3) == 1
    stale = current(people, meal)
    assert stale["result"] is None
    assert stale["generation_job"]["status"] == "superseded"
    fresh = request(people, meal).json()
    assert fresh["generation_job"]["id"] != old
    assert router.generation_worker.tick() == 1
    assert current(people, meal)["generation_job"]["status"] == "published"
    store.close()


def test_attempt_receipts_reject_private_or_malformed_provider_metadata(tmp_path):
    from test_api import recommendation

    def corrupted(snapshot):
        return {
            **recommendation(snapshot),
            "run_id": "PRIVATE_TRACE_VALUE",
            "agent": {
                "input_tokens": "PRIVATE_TRACE_VALUE",
                "output_tokens": True,
                "usage_source": "PRIVATE_TRACE_VALUE",
                "tool_calls": -1,
                "stages": [
                    {
                        "stage": "after_model",
                        "status": "PRIVATE_TRACE_VALUE",
                        "duration_ms": "PRIVATE_TRACE_VALUE",
                    }
                ],
            },
        }

    store, router, people, meal = queued_pilot(tmp_path, corrupted)
    request(people, meal)
    router.generation_worker.tick()
    receipt = current(people, meal)["generation_job"]["attempts"][0]["metadata"]
    assert "PRIVATE_TRACE_VALUE" not in str(receipt)
    assert receipt["input_tokens"] is None and receipt["output_tokens"] is None
    assert receipt["usage_source"] == "unknown"
    store.close()


def test_timeout_is_bounded_and_late_output_cannot_publish(tmp_path):
    import threading

    started, resume, completed = threading.Event(), threading.Event(), threading.Event()
    actual = DiningAgent(ready_catalog())

    def stuck(snapshot):
        started.set()
        assert resume.wait(3)
        try:
            return actual(snapshot)
        finally:
            completed.set()

    stuck.recommender = actual.recommender
    store, router, people, meal = queued_pilot(
        tmp_path, stuck, timeout_seconds=0.02, retry_seconds=0
    )
    request(people, meal)
    try:
        assert router.generation_worker.tick() == 1
        failed = current(people, meal)
        assert failed["generation_job"]["status"] == "retry_wait"
        assert failed["generation_job"]["error_code"] == "GENERATION_TIMEOUT"
        assert (
            failed["generation_worker_status"]["status"]
            == "waiting_for_provider_shutdown"
        )
        assert failed["generation_worker_status"]["blocked_since"] is not None
        assert failed["result"] is None
        assert (
            router.generation_worker.tick() == 0
        )  # Do not accumulate stuck invocations.
    finally:
        resume.set()
    assert started.wait(1)
    # The late thread has no database/publication capability.
    assert completed.wait(2)
    assert current(people, meal)["result"] is None
    import time

    limit = time.monotonic() + 2
    while router.generation_worker.tick() == 0:
        assert time.monotonic() < limit
        time.sleep(0.001)
    assert current(people, meal)["status"] == "shortlisted"
    store.close()


def test_deletion_revokes_queued_snapshot_and_erases_private_job_inputs(tmp_path):
    store, router, people, meal = queued_pilot(tmp_path)
    request(people, meal)
    deleted = people[1].delete("/api/account")
    assert deleted.status_code == 200, deleted.text
    assert router.generation_worker.tick() == 0
    remaining = current(people, meal)
    assert remaining["result"] is None
    assert remaining["generation_history"][0]["status"] == "superseded"
    assert people[1].user_id not in str(remaining["generation_history"])
    # Persistence inspection is part of the deletion/security contract, not a behavior stub.
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM generation_inputs").fetchone()[0] == 0
        assert all(
            "PRIVATE" not in str(tuple(row))
            for row in db.execute("SELECT * FROM generation_attempts")
        )
    store.close()


def test_evidence_only_replacement_requires_new_delegation(tmp_path):
    actual = DiningAgent(ready_catalog())
    revision = ["one"]
    actual.evidence_revision = lambda: revision[0]
    store, router, people, meal = queued_pilot(tmp_path, actual)
    request(people, meal)
    router.generation_worker.tick()
    before = current(people, meal)
    delegated = people[0].post(
        f"/api/meals/{meal['id']}/delegation",
        json={"expected_revision": before["revision"], "enabled": True},
    )
    assert delegated.status_code == 200
    assert delegated.json()["my_delegation"]["enabled"]
    # An idempotent repeat is not a new set and must preserve explicit consent.
    assert request(people, meal).json()["my_delegation"]["enabled"]
    revision[0] = "two"
    replaced = request(people, meal).json()
    assert not replaced["my_delegation"]["enabled"]
    assert replaced["revision"] > before["revision"]
    assert router.generation_worker.tick() == 1
    after = current(people, meal)
    stale_vote = people[0].post(
        f"/api/meals/{meal['id']}/votes",
        json={
            "expected_revision": before["revision"],
            "option_id": before["result"]["options"][0]["id"],
            "choice": "works",
        },
    )
    assert stale_vote.status_code == 409
    assert not after["my_delegation"]["enabled"]
    assert (
        after["acceptance"][after["result"]["options"][0]["id"]]["approved_count"] == 0
    )
    store.close()
