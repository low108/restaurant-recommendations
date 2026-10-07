"""Cross-component workflow tests with SQLite, HTTP routes and the actual dining graph."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from test_api import create_meal, make_client, setup_room
from test_recommendation import ready_catalog

from webapp import create_app


@pytest.fixture
def workflow(tmp_path):
    path = tmp_path / "catalog.json"
    catalog = ready_catalog().model_dump(mode="json")
    for source in catalog["sources"]:
        source["expires_at"] = (
            datetime.now(timezone.utc) + timedelta(days=365)
        ).isoformat()
    for outlet in catalog["outlets"]:
        outlet["opening_exceptions_coverage"]["ends_on"] = (
            (datetime.now(timezone.utc) + timedelta(days=365)).date().isoformat()
        )
    from dining.catalog import Catalog

    path.write_text(Catalog.model_validate(catalog).model_dump_json())
    app = create_app(
        async_generation=False,
        db_path=tmp_path / "pilot.sqlite3",
        catalog_path=path,
        demo_mode=True,
    )
    people = [make_client(app, name) for name in ("FlowA", "FlowB", "FlowC")]
    room, _ = setup_room(people)
    at = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=4, minute=0, second=0, microsecond=0
    )
    meal, _ = create_meal(people[0], room, meal_at=at.isoformat())
    for person in people[:2]:
        assert (
            person.patch(
                "/api/profile",
                json={
                    "allergy_status": "none",
                    "halal_policy": "none",
                    "requirements_reviewed": True,
                },
            ).status_code
            == 200
        )
    yield app, people, meal
    app.state.store.close()


def answer(person, meal, expected=0, **values):
    data = {
        "attendance": "join",
        "budget": 50,
        "ready": True,
        "requirements_confirmed": True,
        "expected_response_revision": expected,
    }
    data.update(values)
    return person.put(f"/api/meals/{meal['id']}/response", json=data)


def shortlist(people, meal):
    for person in people[:2]:
        result = answer(person, meal)
        assert result.status_code == 200, result.text
        meal = result.json()
    response = people[0].post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    assert response.status_code == 200, response.text
    meal = response.json()
    assert meal["status"] == "shortlisted", meal
    return meal, meal["result"]["options"][0]["id"]


def vote(person, meal, option, choice="works", **values):
    return person.post(
        f"/api/meals/{meal['id']}/votes",
        json=dict(
            expected_revision=meal["revision"],
            option_id=option,
            choice=choice,
            **values,
        ),
    )


def select(person, meal, option):
    return person.post(
        f"/api/meals/{meal['id']}/select",
        json={"expected_revision": meal["revision"], "option_id": option},
    )


def test_personal_answer_cas_blocks_stale_tabs_without_conflicting_other_people(
    workflow,
):
    _, people, meal = workflow
    with ThreadPoolExecutor() as pool:
        results = list(pool.map(lambda p: answer(p, meal), people[:2]))
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json()["my_response_revision"] == 1
    stale = answer(people[0], meal, expected=0, budget=9)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "RESPONSE_CHANGED"
    assert stale.json()["detail"]["current_response"]["budget"] == 50
    current = answer(people[0], meal, expected=1, budget=35)
    assert current.status_code == 200
    assert current.json()["my_response_revision"] == 2
    missing = people[0].put(
        f"/api/meals/{meal['id']}/response", json={"attendance": "decline"}
    )
    assert missing.status_code == 422


def test_three_state_votes_and_scoped_delegation_respect_hard_veto_privately(workflow):
    _, people, meal = workflow
    meal, option = shortlist(people, meal)
    assert vote(people[0], meal, option).status_code == 200
    endpoint = f"/api/meals/{meal['id']}/delegation"
    assert (
        people[1]
        .post(endpoint, json={"expected_revision": meal["revision"], "enabled": True})
        .status_code
        == 200
    )
    rejected = vote(
        people[1], meal, option, "cannot_eat", reason="Private reason sentinel"
    )
    assert rejected.status_code == 200
    assert select(people[0], meal, option).status_code == 409
    public = people[0].get(f"/api/meals/{meal['id']}").json()
    assert "Private reason sentinel" not in str(public)
    assert public["acceptance"][option]["approved_count"] == 1
    own = people[1].get(f"/api/meals/{meal['id']}").json()
    assert own["my_votes"][option]["choice"] == "cannot_eat"
    assert vote(people[1], meal, option, "works").status_code == 200
    assert (
        people[1]
        .post(endpoint, json={"expected_revision": meal["revision"], "enabled": False})
        .status_code
        == 200
    )
    assert select(people[0], meal, option).status_code == 200


def test_delegation_can_cover_unanswered_option_but_expires_with_context(workflow):
    _, people, meal = workflow
    meal, option = shortlist(people, meal)
    assert vote(people[0], meal, option).status_code == 200
    endpoint = f"/api/meals/{meal['id']}/delegation"
    assert (
        people[1]
        .post(endpoint, json={"expected_revision": meal["revision"], "enabled": True})
        .status_code
        == 200
    )
    assert select(people[0], meal, option).status_code == 200
    assert (
        people[1].patch("/api/profile", json={"cuisines": ["Thai"]}).status_code == 200
    )
    updated = people[1].get(f"/api/meals/{meal['id']}").json()
    assert updated["status"] == "reconfirmation_required"
    assert updated["decision"] is None and updated["my_delegation"]["enabled"] is False
    assert updated["reconfirmation_required"] is True
    assert select(people[0], meal, option).status_code == 409


def test_overdue_and_expiration_are_persisted_without_inferred_winner(
    workflow, monkeypatch
):
    _, people, meal = workflow
    meal, option = shortlist(people, meal)
    deadline = datetime.fromisoformat(meal["decision_by"])
    monkeypatch.setattr("dining.api.now", lambda: deadline + timedelta(seconds=1))
    overdue = people[0].get(f"/api/meals/{meal['id']}").json()
    assert overdue["status"] == "decision_overdue"
    assert overdue["original_decision_by"] == meal["decision_by"]
    assert select(people[0], meal, option).status_code == 409
    end = datetime.fromisoformat(meal["meal_at"]) + timedelta(
        minutes=meal["duration_minutes"]
    )
    monkeypatch.setattr("dining.api.now", lambda: end + timedelta(seconds=1))
    expired = people[0].get(f"/api/meals/{meal['id']}").json()
    assert expired["status"] == "expired" and expired["decision"] is None
    assert people[1].get(f"/api/meals/{meal['id']}").json()["status"] == "expired"


def test_rich_feedback_learning_proposal_and_source_deletion_are_private_and_reversible(
    workflow, monkeypatch
):
    _, people, meal = workflow
    assert (
        people[0].patch("/api/profile", json={"memory_enabled": True}).status_code
        == 200
    )
    visit = datetime.now(timezone.utc)
    for number in range(3):
        if number:
            meal, _ = create_meal(
                people[0],
                meal["room_id"],
                meal_at=(visit + timedelta(days=1)).isoformat(),
                idempotency_key=f"repeat-visit-{number}",
            )
        meal, option = shortlist(people, meal)
        for person in people[:2]:
            assert vote(person, meal, option).status_code == 200
        assert select(people[0], meal, option).status_code == 200
        visit = datetime.fromisoformat(meal["meal_at"]) + timedelta(hours=2)
        monkeypatch.setattr("dining.api.now", lambda value=visit: value)
        feedback = {
            "visited": True,
            "outcome": "ate_here",
            "option_id": option,
            "enjoyment": "enjoyed",
            "repeat_intent": "yes",
            "influences": ["taste", "value"],
            "cost_expectation": "within_estimate",
            "actual_cost_minor": 2000,
            "dish_text": "My noodles",
            "fairness": "somewhat",
        }
        saved = people[0].post(f"/api/meals/{meal['id']}/feedback", json=feedback)
        assert saved.status_code == 200, saved.text
        learning = people[0].get("/api/learning").json()
        assert len(learning["observations"]) == number + 1
        assert len(learning["proposals"]) == (1 if number == 2 else 0)
    proposal = learning["proposals"][0]
    assert proposal["status"] == "pending" and proposal["would_repeat"] is True
    assert len(proposal["source_observation_ids"]) == 3
    assert (
        people[1]
        .post(f"/api/learning/proposals/{proposal['id']}", json={"decision": "accept"})
        .status_code
        == 404
    )
    accepted = people[0].post(
        f"/api/learning/proposals/{proposal['id']}", json={"decision": "accept"}
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert not people[1].get("/api/learning").json()["observations"]
    assert "My noodles" not in str(people[1].get(f"/api/meals/{meal['id']}").json())
    observation = learning["observations"][
        -1
    ]  # Delete an older supporting meal, not only the latest anchor.
    assert (
        people[1].delete(f"/api/learning/observations/{observation['id']}").status_code
        == 404
    )
    assert (
        people[0].delete(f"/api/learning/observations/{observation['id']}").status_code
        == 200
    )
    cleared = people[0].get("/api/learning").json()
    assert len(cleared["observations"]) == 2
    assert not cleared["proposals"] and not cleared["venue_preferences"]
    assert people[0].post("/api/learning/clear").status_code == 200
    assert people[0].get("/api/learning").json()["observations"] == []


def test_profile_conflict_and_logistics_edit_preserve_original_deadlines(workflow):
    _, people, meal = workflow
    current = people[0].get("/api/profile").json()
    changed = people[0].patch(
        "/api/profile",
        json={
            "expected_profile_revision": current["profile_revision"],
            "spice": "mild",
        },
    )
    assert changed.status_code == 200
    stale = people[0].patch(
        "/api/profile",
        json={"expected_profile_revision": current["profile_revision"], "spice": "hot"},
    )
    assert (
        stale.status_code == 409 and stale.json()["detail"]["code"] == "PROFILE_CHANGED"
    )
    assert people[0].get("/api/profile").json()["spice"] == "mild"
    meal = people[0].get(f"/api/meals/{meal['id']}").json()
    new_answer = datetime.fromisoformat(meal["answer_by"]) + timedelta(minutes=10)
    edit = people[0].patch(
        f"/api/meals/{meal['id']}",
        json={
            "expected_revision": meal["revision"],
            "answer_by": new_answer.isoformat(),
        },
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["original_answer_by"] == meal["answer_by"]
    assert edit.json()["original_decision_by"] == meal["decision_by"]
    assert edit.json()["original_invited_count"] == 2


def test_rich_feedback_never_infers_taste_from_nonattendance_or_service(
    workflow, monkeypatch
):
    _, people, meal = workflow
    people[0].patch("/api/profile", json={"memory_enabled": True})
    meal, option = shortlist(people, meal)
    for person in people[:2]:
        vote(person, meal, option)
    assert select(people[0], meal, option).status_code == 200
    endpoint = f"/api/meals/{meal['id']}/feedback"
    bad = people[0].post(
        endpoint,
        json={
            "visited": False,
            "outcome": "plans_changed",
            "option_id": option,
            "enjoyment": "did_not_enjoy",
        },
    )
    assert bad.status_code == 422
    good = people[0].post(
        endpoint,
        json={
            "visited": False,
            "outcome": "plans_changed",
            "option_id": option,
            "comment": "Went elsewhere",
        },
    )
    assert good.status_code == 200
    assert people[0].get("/api/learning").json()["observations"] == []
    monkeypatch.setattr(
        "dining.api.now",
        lambda: datetime.fromisoformat(meal["meal_at"]) + timedelta(hours=2),
    )
    good = people[0].post(
        endpoint,
        json={
            "visited": True,
            "option_id": option,
            "influences": ["service"],
            "comment": "Long wait",
        },
    )
    assert good.status_code == 200
    assert people[0].get("/api/learning").json()["observations"] == []


def test_delegation_revocation_during_final_check_prevents_selection(tmp_path):
    import threading

    from fastapi import FastAPI
    from test_api import approve, ready_meal, recommendation

    from dining.api import build_router
    from dining.store import DiningStore

    entered, release = threading.Event(), threading.Event()

    class Delayed:
        def __call__(self, snapshot):
            return recommendation(snapshot)

        def revalidate(self, snapshot):
            entered.set()
            assert release.wait(5)
            return recommendation(snapshot)

    store = DiningStore(tmp_path / "delegate.sqlite3")
    app = FastAPI()
    app.include_router(build_router(store, Delayed()))
    people = [make_client(app, name) for name in ("DelegateA", "DelegateB")]
    _, meal = ready_meal(people)
    meal = (
        people[0]
        .post(
            f"/api/meals/{meal['id']}/generate",
            json={"expected_revision": meal["revision"]},
        )
        .json()
    )
    assert approve(people[0], meal).status_code == 200
    endpoint = f"/api/meals/{meal['id']}/delegation"
    assert (
        people[1]
        .post(endpoint, json={"expected_revision": meal["revision"], "enabled": True})
        .status_code
        == 200
    )
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(select, people[0], meal, "outlet-option")
        assert entered.wait(5)
        assert (
            people[1]
            .post(
                endpoint, json={"expected_revision": meal["revision"], "enabled": False}
            )
            .status_code
            == 200
        )
        release.set()
        assert pending.result().status_code == 409
    assert people[0].get(f"/api/meals/{meal['id']}").json()["decision"] is None
    store.close()


def test_manual_unverified_plan_needs_every_ack_and_never_creates_checked_or_learned_claim(
    workflow, monkeypatch
):
    _, people, meal = workflow
    people[0].patch("/api/profile", json={"memory_enabled": True})
    # Manual coordination is separate even when normal checks cannot pass.
    people[1].patch("/api/profile", json={"allergy_status": "withheld"})
    for person in people[:2]:
        response = answer(person, meal)
        assert response.status_code == 200, response.text
        meal = response.json()
    plan = people[0].post(
        f"/api/meals/{meal['id']}/manual-plan",
        json={
            "expected_revision": meal["revision"],
            "name": "Our own unverified venue",
            "address": "Shared location",
            "note": "Call to verify requirements first",
        },
    )
    assert plan.status_code == 200, plan.text
    meal = plan.json()
    assert (
        meal["status"] == "manual_proposed"
        and meal["manual_plan"]["unverified"] is True
    )
    assert meal["result"] is None
    endpoint = f"/api/meals/{meal['id']}/manual-select"
    assert (
        people[0]
        .post(endpoint, json={"expected_revision": meal["revision"]})
        .status_code
        == 409
    )
    for person in people[:2]:
        ack = person.post(
            f"/api/meals/{meal['id']}/manual-ack",
            json={
                "expected_revision": meal["revision"],
                "acknowledge_unverified": True,
            },
        )
        assert ack.status_code == 200, ack.text
    chosen = people[0].post(endpoint, json={"expected_revision": meal["revision"]})
    assert chosen.status_code == 200, chosen.text
    meal = chosen.json()
    assert meal["status"] == "manual_selected"
    assert (
        meal["decision"]["checked"] is False
        and meal["decision"]["choice_mode"] == "manual_unverified"
    )
    assert meal["decision"]["requirements_status"] == "not_verified"
    assert meal["result"] is None
    monkeypatch.setattr(
        "dining.api.now",
        lambda: datetime.fromisoformat(meal["meal_at"]) + timedelta(hours=2),
    )
    feedback = people[0].post(
        f"/api/meals/{meal['id']}/feedback",
        json={
            "option_id": meal["decision"]["option_id"],
            "visited": True,
            "would_repeat": True,
        },
    )
    assert feedback.status_code == 200, feedback.text
    assert people[0].get("/api/learning").json()["observations"] == []


def test_manual_plan_revision_revokes_all_acknowledgements(workflow):
    _, people, meal = workflow
    for person in people[:2]:
        meal = answer(person, meal).json()
    path = f"/api/meals/{meal['id']}/manual-plan"
    meal = (
        people[0]
        .post(
            path,
            json={
                "expected_revision": meal["revision"],
                "name": "Place A",
                "address": "Area A",
            },
        )
        .json()
    )
    for person in people[:2]:
        assert (
            person.post(
                f"/api/meals/{meal['id']}/manual-ack",
                json={
                    "expected_revision": meal["revision"],
                    "acknowledge_unverified": True,
                },
            ).status_code
            == 200
        )
    stale = meal["revision"]
    replacement = people[0].post(
        path, json={"expected_revision": stale, "name": "Place B", "address": "Area B"}
    )
    assert replacement.status_code == 200, replacement.text
    updated = replacement.json()
    assert updated["manual_acceptance"]["acknowledged_count"] == 0
    assert (
        people[0]
        .post(
            f"/api/meals/{meal['id']}/manual-select", json={"expected_revision": stale}
        )
        .status_code
        == 409
    )
    assert (
        people[0]
        .post(
            f"/api/meals/{meal['id']}/manual-select",
            json={"expected_revision": updated["revision"]},
        )
        .status_code
        == 409
    )


def test_upcoming_selected_logistics_can_be_revised_only_with_fresh_acknowledgements(
    workflow,
):
    _, people, meal = workflow
    meal, option = shortlist(people, meal)
    for person in people[:2]:
        vote(person, meal, option)
    assert select(people[0], meal, option).status_code == 200
    changed = people[0].patch(
        f"/api/meals/{meal['id']}",
        json={
            "expected_revision": meal["revision"],
            "location_label": "New shared meeting point",
        },
    )
    assert changed.status_code == 200, changed.text
    current = changed.json()
    assert current["status"] == "reconfirmation_required"
    assert current["decision"] is None and current["result"] is None
    assert current["my_votes"] == {} and current["my_delegation"]["enabled"] is False
    assert all(not p["ready"] for p in current["participants"])
    assert current["my_response_revision"] > 1
    assert select(people[0], meal, option).status_code == 409


def test_password_reset_cannot_race_old_verified_password_into_new_session(
    workflow, monkeypatch
):
    from dining.api import PASSWORD_HASHER

    app, people, _ = workflow
    verified = PASSWORD_HASHER.verify
    replacement = PASSWORD_HASHER.hash("replacement-password")

    def reset_between_check_and_session(password, hash_value):
        valid = verified(password, hash_value)
        with app.state.store.transaction() as db:
            db.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (replacement, people[0].user_id),
            )
            db.execute("DELETE FROM sessions WHERE user_id=?", (people[0].user_id,))
        return valid

    monkeypatch.setattr(PASSWORD_HASHER, "verify", reset_between_check_and_session)
    response = people[0].post(
        "/api/auth/login",
        json={"email": "flowa@example.test", "password": "long-test-password"},
    )
    assert response.status_code == 401
    assert people[0].get("/api/rooms").status_code == 401


def test_repeat_pattern_rejection_suppresses_same_suggestion_after_monthly_cooldown(
    workflow, monkeypatch
):
    # This behavioral test uses a deliberately long-lived synthetic catalog to advance the clock.
    _, people, meal = workflow
    people[0].patch("/api/profile", json={"memory_enabled": True})
    current_time = datetime.now(timezone.utc)
    for number in range(4):
        if number:
            meal, _ = create_meal(
                people[0],
                meal["room_id"],
                meal_at=(current_time + timedelta(days=1))
                .replace(hour=4, minute=0)
                .isoformat(),
                idempotency_key=f"reject-pattern-{number}",
            )
        meal, option = shortlist(people, meal)
        for person in people[:2]:
            vote(person, meal, option)
        assert select(people[0], meal, option).status_code == 200
        current_time = datetime.fromisoformat(meal["meal_at"]) + timedelta(hours=2)
        monkeypatch.setattr("dining.api.now", lambda value=current_time: value)
        response = people[0].post(
            f"/api/meals/{meal['id']}/feedback",
            json={"visited": True, "option_id": option, "would_repeat": True},
        )
        assert response.status_code == 200, response.text
        learning = people[0].get("/api/learning").json()
        if number == 2:
            proposal = learning["proposals"][0]
            assert (
                people[0]
                .post(
                    f"/api/learning/proposals/{proposal['id']}",
                    json={"decision": "reject"},
                )
                .status_code
                == 200
            )
            current_time += timedelta(days=32)
            monkeypatch.setattr("dining.api.now", lambda value=current_time: value)
        if number == 3:
            assert (
                len(learning["proposals"]) == 1
                and learning["proposals"][0]["status"] == "rejected"
            )


def test_additive_migration_preserves_legacy_profiles_and_votes(tmp_path):
    import sqlite3

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_api import approve, generate, ready_meal, recommendation

    from dining.api import build_router
    from dining.store import DiningStore

    path = tmp_path / "legacy.sqlite3"
    store = DiningStore(path)
    app = FastAPI()
    app.include_router(build_router(store, recommendation))
    people = [make_client(app, name) for name in ("LegacyA", "LegacyB")]
    _, meal = ready_meal(people)
    meal = generate(people[0], meal)
    assert approve(people[0], meal, approve=False).status_code == 200
    cookies = dict(people[0].cookies)
    store.close()
    # Recreate the pre-upgrade table shape in this isolated fixture.
    with sqlite3.connect(path) as db:
        for table in (
            "proposal_sources",
            "preference_proposals",
            "learning_suggestion_log",
            "manual_acknowledgements",
            "delegations",
        ):
            db.execute(f"DROP TABLE {table}")
        for field in ("choice", "reason"):
            db.execute(f"ALTER TABLE votes DROP COLUMN {field}")
        for field in (
            "original_answer_by",
            "original_decision_by",
            "reconfirmation_required",
            "lifecycle_reason",
            "original_invited_count",
            "manual_plan",
        ):
            db.execute(f"ALTER TABLE meals DROP COLUMN {field}")
    restored = DiningStore(path)
    resumed = FastAPI()
    resumed.include_router(build_router(restored, recommendation))
    client = TestClient(resumed)
    client.cookies.update(cookies)
    assert client.get("/api/profile").json()["allergy_status"] == "none"
    loaded = client.get(f"/api/meals/{meal['id']}").json()
    assert loaded["my_votes"]["outlet-option"]["choice"] == "prefer_another"
    assert loaded["original_decision_by"] == meal["decision_by"]
    assert loaded["my_delegation"]["enabled"] is False
    restored.close()


def test_a26_deletion_of_allergy_asserts_post_deletion_review_state_and_owner_confirmation(
    workflow,
):
    _, people, meal = workflow
    organizer, allergic_guest = people[0], people[1]

    # Allergic guest starts with a declared peanut allergy
    init_profile = allergic_guest.patch(
        "/api/profile",
        json={
            "allergy_status": "declared",
            "allergens": ["peanuts"],
            "sensitive_data_consent": True,
            "requirements_reviewed": True,
        },
    )
    assert init_profile.status_code == 200
    rev1 = init_profile.json()["profile_revision"]

    # Allergic guest checks in and confirms requirements
    meal_info = allergic_guest.get(f"/api/meals/{meal['id']}").json()
    resp = allergic_guest.put(
        f"/api/meals/{meal['id']}/response",
        json={
            "expected_response_revision": meal_info["my_response_revision"],
            "attendance": "join",
            "cuisines": ["Malaysian"],
            "budget": 30,
            "requirements_confirmed": True,
            "ready": True,
        },
    )
    assert resp.status_code == 200

    # Organizer also checks in and marks ready
    org_resp = answer(organizer, meal)
    assert org_resp.status_code == 200

    # Organizer generates recommendations: with an unverified peanut allergy,
    # PRD §8.2 hard checks require verification rather than guessing safety
    curr_meal = organizer.get(f"/api/meals/{meal['id']}").json()
    gen_res = organizer.post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": curr_meal["revision"]},
    )
    assert gen_res.status_code == 200, gen_res.text
    generated = gen_res.json()
    assert generated["status"] == "needs_verification"

    # Guest deletes the allergy from their profile
    del_profile = allergic_guest.patch(
        "/api/profile",
        json={
            "expected_profile_revision": rev1,
            "allergy_status": "none",
            "allergens": [],
            "requirements_reviewed": True,
        },
    )
    assert del_profile.status_code == 200
    updated = del_profile.json()
    assert updated["allergy_status"] == "none"
    assert updated["allergens"] == []
    assert updated["profile_revision"] > rev1

    # Post-deletion review state assertions:
    # 1. Meal invalidates and resets participants' readiness and requirement confirmation
    guest_view = allergic_guest.get(f"/api/meals/{meal['id']}").json()
    assert guest_view["my_response"]["requirements_confirmed"] is False
    assert guest_view["my_response"]["ready"] is False

    # 2. Allergic guest explicitly re-confirms the updated allergy-free requirements
    reconfirm_resp = allergic_guest.put(
        f"/api/meals/{meal['id']}/response",
        json={
            "expected_response_revision": guest_view["my_response_revision"],
            "attendance": "join",
            "cuisines": ["Malaysian"],
            "budget": 30,
            "requirements_confirmed": True,
            "ready": True,
        },
    )
    assert reconfirm_resp.status_code == 200
    assert reconfirm_resp.json()["my_response"]["requirements_confirmed"] is True
    assert reconfirm_resp.json()["my_response"]["ready"] is True

    # 3. Now that the allergy is cleared and requirements re-confirmed, shortlist succeeds
    recheck_meal = organizer.get(f"/api/meals/{meal['id']}").json()
    gen2 = organizer.post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": recheck_meal["revision"]},
    )
    assert gen2.status_code == 200
    shortlisted = gen2.json()
    assert shortlisted["status"] == "shortlisted"

    # 4. If selected, a subsequent addition/modification of an allergy requires reconfirmation
    opt_id = shortlisted["result"]["options"][0]["id"]
    for p in (organizer, allergic_guest):
        p.post(
            f"/api/meals/{meal['id']}/votes",
            json={
                "option_id": opt_id,
                "expected_revision": shortlisted["revision"],
                "choice": "works",
            },
        )
    sel = organizer.post(
        f"/api/meals/{meal['id']}/select",
        json={"option_id": opt_id, "expected_revision": shortlisted["revision"]},
    )
    assert sel.status_code == 200
    assert sel.json()["status"] == "selected"

    # Modifying allergy on upcoming selected meal requires reconfirmation
    allergic_guest.patch(
        "/api/profile",
        json={
            "allergy_status": "declared",
            "allergens": ["shellfish"],
            "sensitive_data_consent": True,
            "requirements_reviewed": True,
        },
    )
    selected_meal = organizer.get(f"/api/meals/{meal['id']}").json()
    assert selected_meal["status"] == "reconfirmation_required"
    assert selected_meal["decision"] is None
