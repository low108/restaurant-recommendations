import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dining.api import build_router
from dining.store import DiningStore, decode


def recommendation(snapshot):
    return {
        "status": "shortlisted",
        "catalog_id": "test-catalog",
        "catalog_version": "v1",
        "options": [
            {
                "id": "outlet-option",
                "outlet_id": "outlet-a",
                "name": "Fixture only",
                "eligible": True,
            }
        ],
        "verification": [],
        "explanation": "Fixture, not a real recommendation",
    }


def make_client(app, name):
    client = TestClient(app)
    result = client.post(
        "/api/auth/register",
        json={
            "name": name,
            "email": name.lower() + "@example.test",
            "password": "long-test-password",
            "adult_confirmed": True,
            "terms_accepted": True,
        },
    )
    assert result.status_code == 201, result.text
    data = result.json()
    client.headers["X-CSRF-Token"] = data["csrf_token"]
    client.user_id = data["user"]["id"]
    return client


@pytest.fixture
def pilot(tmp_path):
    store = DiningStore(tmp_path / "pilot.sqlite3")
    app = FastAPI()
    app.include_router(build_router(store, recommendation))
    clients = [make_client(app, name) for name in ("Ava", "Ben", "Cara")]
    yield store, app, clients
    store.close()


def setup_room(clients):
    owner, friend = clients[:2]
    response = owner.post("/api/rooms", json={"name": "Lunch friends"})
    assert response.status_code == 201, response.text
    room = response.json()
    joined = friend.post("/api/rooms/join", json={"token": room["invite_token"]})
    assert joined.status_code == 200, joined.text
    return room["room"]["id"], room["invite_token"]


def create_meal(owner, room_id, **overrides):
    # Schedule during lunch hours tomorrow to remain within operating hours regardless of test execution time.
    tomorrow_lunch = datetime.now(timezone.utc).replace(
        hour=4, minute=30, second=0, microsecond=0
    ) + timedelta(days=1)
    payload = {
        "kind": "lunch",
        "meal_at": tomorrow_lunch.isoformat(),
        "location_label": "SS2",
        "latitude": 3.12,
        "longitude": 101.62,
        "idempotency_key": "meal-test-unique",
    }
    payload.update(overrides)
    response = owner.post(f"/api/rooms/{room_id}/meals", json=payload)
    assert response.status_code == 201, response.text
    return response.json(), payload


def checkin(client, meal_id, **overrides):
    response = client.patch(
        "/api/profile",
        json={
            "allergy_status": "none",
            "halal_policy": "none",
            "requirements_reviewed": True,
        },
    )
    assert response.status_code == 200, response.text
    answer = {
        "expected_response_revision": client.get(f"/api/meals/{meal_id}").json()[
            "my_response_revision"
        ],
        "attendance": "join",
        "cuisines": ["Malaysian"],
        "budget": 25,
        "requirements_confirmed": True,
        "ready": True,
    }
    answer.update(overrides)
    result = client.put(f"/api/meals/{meal_id}/response", json=answer)
    assert result.status_code == 200, result.text
    return result.json()


def ready_meal(clients):
    room_id, _ = setup_room(clients)
    meal, _ = create_meal(clients[0], room_id)
    for client in clients[:2]:
        meal = checkin(client, meal["id"])
    return room_id, meal


def generate(client, meal):
    result = client.post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    assert result.status_code == 200, result.text
    return result.json()


def approve(client, meal, approve=True, revision=None):
    return client.post(
        f"/api/meals/{meal['id']}/votes",
        json={
            "expected_revision": revision or meal["revision"],
            "option_id": "outlet-option",
            "approve": approve,
        },
    )


def select(client, meal):
    return client.post(
        f"/api/meals/{meal['id']}/select",
        json={"expected_revision": meal["revision"], "option_id": "outlet-option"},
    )


def test_registration_requires_explicit_choices_and_private_auth_storage(tmp_path):
    store = DiningStore(tmp_path / "auth.sqlite3")
    app = FastAPI()
    app.include_router(build_router(store, recommendation))
    client = TestClient(app)
    base = {
        "name": "Alice",
        "email": "alice@example.test",
        "password": "not-a-short-password",
    }
    assert client.post("/api/auth/register", json=base).status_code == 422
    base.update(adult_confirmed=True, terms_accepted=True)
    response = client.post("/api/auth/register", json=base)
    assert response.status_code == 201
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    with store.transaction() as db:
        user = db.execute("SELECT * FROM users").fetchone()
        session = db.execute("SELECT * FROM sessions").fetchone()
        assert user["password_hash"].startswith("$argon2id$")
        assert session["token_hash"] != client.cookies["dining_session"]
    store.close()


def test_auth_csrf_origin_and_logout(pilot):
    _, app, clients = pilot
    owner = clients[0]
    assert TestClient(app).get("/api/rooms").status_code == 401
    assert (
        owner.post(
            "/api/rooms", json={"name": "bad"}, headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    assert (
        owner.post(
            "/api/rooms",
            json={"name": "bad"},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert owner.post("/api/auth/logout").status_code == 200
    assert owner.get("/api/rooms").status_code == 401
    login = owner.post(
        "/api/auth/login",
        json={"email": "ava@example.test", "password": "long-test-password"},
    )
    assert login.status_code == 200
    assert owner.get("/api/rooms").status_code == 200


def test_login_rate_limit_survives_new_router(pilot):
    store, app, _ = pilot
    client = TestClient(app)
    for _ in range(10):
        assert (
            client.post(
                "/api/auth/login",
                json={"email": "absent@example.test", "password": "long-test-password"},
            ).status_code
            == 401
        )
    another_app = FastAPI()
    another_app.include_router(build_router(store, recommendation))
    result = TestClient(another_app).post(
        "/api/auth/login",
        json={"email": "absent@example.test", "password": "long-test-password"},
    )
    assert result.status_code == 429


def test_cross_room_isolation_and_private_projections(pilot):
    _, _, clients = pilot
    owner, friend, outsider = clients
    room_id, meal = ready_meal(clients)
    assert outsider.get(f"/api/rooms/{room_id}").status_code == 404
    assert outsider.get(f"/api/meals/{meal['id']}").status_code == 404
    assert (
        outsider.put(
            f"/api/meals/{meal['id']}/response",
            json={"attendance": "join", "expected_response_revision": 0},
        ).status_code
        == 404
    )
    room = owner.get(f"/api/rooms/{room_id}").json()
    assert all(set(member) == {"id", "name", "is_owner"} for member in room["members"])
    meal_data = owner.get(f"/api/meals/{meal['id']}").json()
    assert all(
        "response" not in participant and "profile" not in participant
        for participant in meal_data["participants"]
    )
    assert friend.user_id not in str(owner.get("/api/export").json()["meal_responses"])


def test_sensitive_profile_requires_consent_and_review(pilot):
    _, _, clients = pilot
    client = clients[0]
    payload = {"allergy_status": "declared", "allergens": ["peanut"]}
    assert client.patch("/api/profile", json=payload).status_code == 422
    payload["sensitive_data_consent"] = True
    assert client.patch("/api/profile", json=payload).status_code == 200
    assert (
        client.patch("/api/profile", json={"allergy_status": "none"}).status_code == 422
    )
    assert (
        client.patch(
            "/api/profile", json={"requirements_reviewed": "not a boolean"}
        ).status_code
        == 422
    )


def test_invite_rotation_owner_transfer_and_revocation(pilot):
    _, _, clients = pilot
    owner, friend, outsider = clients
    room_id, old_token = setup_room(clients)
    assert friend.post(f"/api/rooms/{room_id}/invite").status_code == 403
    new_token = owner.post(f"/api/rooms/{room_id}/invite").json()["invite_token"]
    assert (
        outsider.post("/api/rooms/join", json={"token": old_token}).status_code == 404
    )
    assert (
        outsider.post("/api/rooms/join", json={"token": new_token}).status_code == 200
    )
    assert owner.post(f"/api/rooms/{room_id}/leave").status_code == 409
    assert (
        owner.post(
            f"/api/rooms/{room_id}/transfer", json={"user_id": friend.user_id}
        ).status_code
        == 200
    )
    assert owner.post(f"/api/rooms/{room_id}/leave").status_code == 200
    assert owner.get(f"/api/rooms/{room_id}").status_code == 404
    assert (
        friend.delete(f"/api/rooms/{room_id}/members/{outsider.user_id}").status_code
        == 200
    )
    assert outsider.get(f"/api/rooms/{room_id}").status_code == 404


def test_meal_creation_is_idempotent_and_deadlines_validated(pilot):
    store, _, clients = pilot
    room_id, _ = setup_room(clients)
    meal, payload = create_meal(clients[0], room_id)
    repeated = clients[0].post(f"/api/rooms/{room_id}/meals", json=payload)
    assert repeated.json()["id"] == meal["id"]
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM meals").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT COUNT(*) FROM notifications WHERE kind='meal_invitation'"
            ).fetchone()[0]
            == 2
        )
    payload["answer_by"] = payload["meal_at"]
    assert (
        clients[0].post(f"/api/rooms/{room_id}/meals", json=payload).status_code == 422
    )


def test_pending_invitation_needs_explicit_exclusion(pilot):
    _, _, clients = pilot
    room_id, token = setup_room(clients)
    clients[2].post("/api/rooms/join", json={"token": token})
    meal, _ = create_meal(clients[0], room_id)
    for client in clients[:2]:
        meal = checkin(client, meal["id"])
    path = f"/api/meals/{meal['id']}/generate"
    assert (
        clients[0].post(path, json={"expected_revision": meal["revision"]}).status_code
        == 409
    )
    result = clients[0].post(
        path, json={"expected_revision": meal["revision"], "exclude_pending": True}
    )
    assert result.status_code == 200, result.text
    assert clients[2].user_id not in result.json()["frozen_participant_ids"]
    assert approve(clients[2], result.json()).status_code == 403


def test_unknown_requirements_never_pass_even_with_permissive_callback(pilot):
    _, _, clients = pilot
    _, meal = ready_meal(clients)
    assert (
        clients[1]
        .patch("/api/profile", json={"allergy_status": "withheld"})
        .status_code
        == 200
    )
    result = clients[1].put(
        f"/api/meals/{meal['id']}/response",
        json={
            "expected_response_revision": clients[1]
            .get(f"/api/meals/{meal['id']}")
            .json()["my_response_revision"],
            "attendance": "join",
            "budget": 25,
            "ready": True,
            "requirements_confirmed": True,
        },
    )
    assert result.status_code == 200
    meal = generate(clients[0], result.json())
    assert meal["status"] == "needs_verification"
    assert meal["result"]["options"] == []
    assert approve(clients[0], meal).status_code == 409


def test_profile_change_invalidates_shortlist_and_stale_vote(pilot):
    _, _, clients = pilot
    _, meal = ready_meal(clients)
    meal = generate(clients[0], meal)
    assert approve(clients[0], meal).status_code == 200
    assert (
        clients[1].patch("/api/profile", json={"cuisines": ["Thai"]}).status_code == 200
    )
    current = clients[0].get(f"/api/meals/{meal['id']}").json()
    assert current["revision"] > meal["revision"]
    assert current["result"] is None and current["my_vote"] == {}
    assert approve(clients[0], meal).status_code == 409
    assert not next(
        p for p in current["participants"] if p["id"] == clients[1].user_id
    )["ready"]


def test_checkin_edit_and_removal_invalidate(pilot):
    _, _, clients = pilot
    room_id, meal = ready_meal(clients)
    meal = generate(clients[0], meal)
    updated = (
        clients[1]
        .put(
            f"/api/meals/{meal['id']}/response",
            json={
                "expected_response_revision": clients[1]
                .get(f"/api/meals/{meal['id']}")
                .json()["my_response_revision"],
                "attendance": "join",
                "budget": 30,
                "ready": True,
                "requirements_confirmed": True,
            },
        )
        .json()
    )
    assert updated["revision"] > meal["revision"] and updated["result"] is None
    assert (
        clients[0]
        .delete(f"/api/rooms/{room_id}/members/{clients[1].user_id}")
        .status_code
        == 200
    )
    assert clients[1].get(f"/api/meals/{meal['id']}").status_code == 404
    assert clients[1].get("/api/notifications").json()["notifications"] == []
    assert clients[0].get(f"/api/meals/{meal['id']}").json()["result"] is None


def test_unanimous_votes_and_no_shared_private_vote_values(pilot):
    _, _, clients = pilot
    _, meal = ready_meal(clients)
    meal = generate(clients[0], meal)
    assert select(clients[0], meal).status_code == 409
    assert approve(clients[0], meal).status_code == 200
    assert approve(clients[1], meal, approve=False).status_code == 200
    assert select(clients[0], meal).status_code == 409
    other_view = clients[0].get(f"/api/meals/{meal['id']}").json()
    assert other_view["my_vote"] == {"outlet-option": True}
    assert other_view["acceptance"]["outlet-option"] == {
        "approved_count": 1,
        "required_count": 2,
    }
    assert approve(clients[1], meal).status_code == 200
    selected = select(clients[0], meal)
    assert selected.status_code == 200, selected.text
    assert selected.json()["status"] == "selected"
    assert (
        clients[1]
        .put(
            f"/api/meals/{meal['id']}/response",
            json={"attendance": "decline", "expected_response_revision": 1},
        )
        .status_code
        == 409
    )


def test_feedback_needs_real_visit_and_learning_optout(pilot):
    store, _, clients = pilot
    _, meal = ready_meal(clients)
    meal = generate(clients[0], meal)
    for client in clients[:2]:
        approve(client, meal)
    assert select(clients[0], meal).status_code == 200
    path = f"/api/meals/{meal['id']}/feedback"
    assert (
        clients[0]
        .post(path, json={"option_id": "outlet-option", "visited": False, "rating": 5})
        .status_code
        == 422
    )
    assert (
        clients[0]
        .post(path, json={"option_id": "outlet-option", "visited": True, "rating": 5})
        .status_code
        == 409
    )
    # Advance the saved planned time to exercise post-visit behaviour without wall-clock sleeps.
    with store.transaction() as db:
        row = db.execute(
            "SELECT payload FROM meals WHERE id=?", (meal["id"],)
        ).fetchone()
        payload = decode(row["payload"])
        payload["meal_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat()
        from dining.store import encode

        db.execute(
            "UPDATE meals SET payload=? WHERE id=?", (encode(payload), meal["id"])
        )
    assert (
        clients[0]
        .post(path, json={"option_id": "outlet-option", "visited": True, "rating": 5})
        .status_code
        == 200
    )
    assert clients[0].get("/api/export").json()["observations"] == []
    clients[0].patch("/api/profile", json={"memory_enabled": True})
    assert (
        clients[0]
        .post(path, json={"option_id": "outlet-option", "visited": True, "rating": 5})
        .status_code
        == 200
    )
    assert len(clients[0].get("/api/export").json()["observations"]) == 1
    clients[0].patch("/api/profile", json={"memory_enabled": False})
    assert clients[0].get("/api/export").json()["observations"] == []
    assert clients[1].get(f"/api/meals/{meal['id']}").json()["my_feedback"] is None


def test_stale_generation_cannot_publish_after_concurrent_profile_edit(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def slow_recommend(snapshot):
        entered.set()
        assert release.wait(10)
        return recommendation(snapshot)

    store = DiningStore(tmp_path / "concurrent.sqlite3")
    app = FastAPI()
    app.include_router(build_router(store, slow_recommend))
    clients = [make_client(app, name) for name in ("SlowA", "SlowB")]
    _, meal = ready_meal(clients)
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(
            clients[0].post,
            f"/api/meals/{meal['id']}/generate",
            json={"expected_revision": meal["revision"]},
        )
        assert entered.wait(10)
        assert (
            clients[1]
            .patch("/api/profile", json={"cuisines": ["Japanese"]})
            .status_code
            == 200
        )
        release.set()
        assert pending.result().status_code == 409
    assert clients[0].get(f"/api/meals/{meal['id']}").json()["result"] is None
    store.close()


def test_account_delete_cascades_private_records_and_archives_owned_rooms(pilot):
    store, _, clients = pilot
    room_id, _ = ready_meal(clients)
    assert clients[0].delete("/api/account").status_code == 200
    assert clients[0].get("/api/session").json()["user"] is None
    assert clients[1].get(f"/api/rooms/{room_id}").json()["archived"] is True
    with store.transaction() as db:
        for table in (
            "users",
            "sessions",
            "participants",
            "feedback",
            "observations",
            "notifications",
            "members",
            "data_error_reports",
        ):
            column = "id" if table == "users" else "user_id"
            assert (
                db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column}=?",
                    (clients[0].user_id,),
                ).fetchone()[0]
                == 0
            )


def test_future_selected_meal_reopens_on_profile_change(pilot):
    _, _, clients = pilot
    _, meal = ready_meal(clients)
    meal = generate(clients[0], meal)
    for client in clients[:2]:
        approve(client, meal)
    assert select(clients[0], meal).status_code == 200
    assert (
        clients[1].patch("/api/profile", json={"cuisines": ["Thai"]}).status_code == 200
    )
    current = clients[0].get(f"/api/meals/{meal['id']}").json()
    assert current["status"] == "reconfirmation_required"
    assert (
        current["decision"] is None
        and current["result"] is None
        and current["my_vote"] == {}
    )
    assert current["revision"] > meal["revision"]


def test_archive_clears_upcoming_decision_and_frozen_participants(pilot):
    _, _, clients = pilot
    room_id, meal = ready_meal(clients)
    meal = generate(clients[0], meal)
    for client in clients[:2]:
        approve(client, meal)
    assert select(clients[0], meal).status_code == 200
    assert clients[0].post(f"/api/rooms/{room_id}/archive").status_code == 200
    current = clients[1].get(f"/api/meals/{meal['id']}").json()
    assert current["status"] == "cancelled"
    assert current["decision"] is None and current["frozen_participant_ids"] == []


def test_deleting_owner_scrubs_frozen_identity_before_selection(pilot):
    _, _, clients = pilot
    _, meal = ready_meal(clients)
    meal = generate(clients[0], meal)
    assert clients[0].delete("/api/account").status_code == 200
    current = clients[1].get(f"/api/meals/{meal['id']}").json()
    assert clients[0].user_id not in str(current)
    assert current["status"] == "cancelled" and current["frozen_participant_ids"] == []


def test_restart_recovers_interrupted_generation(tmp_path):
    path = tmp_path / "restart.sqlite3"
    store = DiningStore(path)
    app = FastAPI()
    app.include_router(build_router(store, recommendation))
    clients = [make_client(app, name) for name in ("RestartA", "RestartB")]
    _, meal = ready_meal(clients)
    with store.transaction() as db:
        db.execute("UPDATE meals SET status='generating' WHERE id=?", (meal["id"],))
    store.close()
    restarted = DiningStore(path)
    with restarted.transaction() as db:
        state = db.execute(
            "SELECT status,revision FROM meals WHERE id=?", (meal["id"],)
        ).fetchone()
        assert (
            state["status"] == "collecting"
            and state["revision"] == meal["revision"] + 1
        )
    restarted.close()


def test_selection_rechecks_catalog_version_and_invalidates(tmp_path):
    class ChangingCatalog:
        def __call__(self, snapshot):
            return recommendation(snapshot)

        def revalidate(self, snapshot):
            return {**recommendation(snapshot), "catalog_version": "v2"}

    store = DiningStore(tmp_path / "changed.sqlite3")
    app = FastAPI()
    app.include_router(build_router(store, ChangingCatalog()))
    clients = [make_client(app, name) for name in ("ChangeA", "ChangeB")]
    _, meal = ready_meal(clients)
    meal = generate(clients[0], meal)
    for client in clients:
        approve(client, meal)
    assert select(clients[0], meal).status_code == 409
    current = clients[0].get(f"/api/meals/{meal['id']}").json()
    assert current["status"] == "collecting" and current["result"] is None
    store.close()


def test_revoked_vote_during_revalidation_blocks_selection(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class SlowValidation:
        def __call__(self, snapshot):
            return recommendation(snapshot)

        def revalidate(self, snapshot):
            entered.set()
            assert release.wait(10)
            return recommendation(snapshot)

    store = DiningStore(tmp_path / "vote-revoke.sqlite3")
    app = FastAPI()
    app.include_router(build_router(store, SlowValidation()))
    clients = [make_client(app, name) for name in ("RevokeA", "RevokeB")]
    _, meal = ready_meal(clients)
    meal = generate(clients[0], meal)
    for client in clients:
        approve(client, meal)
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(select, clients[0], meal)
        assert entered.wait(10)
        assert approve(clients[1], meal, approve=False).status_code == 200
        release.set()
        assert pending.result().status_code == 409
    assert clients[0].get(f"/api/meals/{meal['id']}").json()["decision"] is None
    store.close()


def test_organizer_can_cancel_future_selected_meal_and_stale_selection_fails(pilot):
    _, _, clients = pilot
    _, meal = ready_meal(clients)
    meal = generate(clients[0], meal)
    for client in clients[:2]:
        approve(client, meal)
    assert select(clients[0], meal).status_code == 200
    path = f"/api/meals/{meal['id']}/cancel"
    assert (
        clients[1].post(path, json={"expected_revision": meal["revision"]}).status_code
        == 403
    )
    assert (
        clients[2].post(path, json={"expected_revision": meal["revision"]}).status_code
        == 404
    )
    result = clients[0].post(path, json={"expected_revision": meal["revision"]})
    assert result.status_code == 200, result.text
    cancelled = result.json()
    assert (
        cancelled["status"] == "cancelled"
        and cancelled["revision"] == meal["revision"] + 1
    )
    assert cancelled["decision"] is None and cancelled["result"] is None
    assert cancelled["frozen_participant_ids"] == [] and cancelled["my_vote"] == {}
    assert select(clients[0], meal).status_code == 409
    assert approve(clients[1], meal).status_code == 409
    assert (
        clients[0]
        .post(path, json={"expected_revision": cancelled["revision"]})
        .status_code
        == 409
    )
    assert any(
        n["kind"] == "meal_cancelled"
        for n in clients[1].get("/api/notifications").json()["notifications"]
    )


def test_cancellation_during_generation_prevents_publication(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def slow_recommend(snapshot):
        entered.set()
        assert release.wait(10)
        return recommendation(snapshot)

    store = DiningStore(tmp_path / "cancel-generation.sqlite3")
    app = FastAPI()
    app.include_router(build_router(store, slow_recommend))
    clients = [make_client(app, name) for name in ("CancelA", "CancelB")]
    _, meal = ready_meal(clients)
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(
            clients[0].post,
            f"/api/meals/{meal['id']}/generate",
            json={"expected_revision": meal["revision"]},
        )
        assert entered.wait(10)
        cancelled = clients[0].post(
            f"/api/meals/{meal['id']}/cancel",
            json={"expected_revision": meal["revision"]},
        )
        assert cancelled.status_code == 200, cancelled.text
        release.set()
        assert pending.result().status_code == 409
    current = clients[0].get(f"/api/meals/{meal['id']}").json()
    assert current["status"] == "cancelled" and current["result"] is None
    store.close()


def test_past_selected_meal_cannot_be_cancelled(pilot):
    store, _, clients = pilot
    _, meal = ready_meal(clients)
    meal = generate(clients[0], meal)
    for client in clients[:2]:
        approve(client, meal)
    assert select(clients[0], meal).status_code == 200
    with store.transaction() as db:
        from dining.store import encode

        payload = decode(
            db.execute(
                "SELECT payload FROM meals WHERE id=?", (meal["id"],)
            ).fetchone()["payload"]
        )
        payload["meal_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat()
        db.execute(
            "UPDATE meals SET payload=? WHERE id=?", (encode(payload), meal["id"])
        )
    assert (
        clients[0]
        .post(
            f"/api/meals/{meal['id']}/cancel",
            json={"expected_revision": meal["revision"]},
        )
        .status_code
        == 409
    )
    current = clients[0].get(f"/api/meals/{meal['id']}").json()
    assert current["status"] == "awaiting_feedback" and current["decision"] is not None


@pytest.mark.skipif(
    os.name != "posix", reason="POSIX permission bits are not Windows ACLs"
)
def test_database_permissions_are_private_without_changing_existing_parent(tmp_path):
    private_path = tmp_path / "new-app-data" / "pilot.sqlite3"
    store = DiningStore(private_path)
    assert stat.S_IMODE(private_path.parent.stat().st_mode) == 0o700
    for path in (
        private_path,
        private_path.with_name(private_path.name + "-wal"),
        private_path.with_name(private_path.name + "-shm"),
    ):
        assert path.exists()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    store.close()

    existing_parent = tmp_path / "existing-folder"
    existing_parent.mkdir(mode=0o755)
    existing_parent.chmod(0o755)
    existing_db = existing_parent / "pilot.sqlite3"
    existing_db.touch(mode=0o644)
    existing_db.chmod(0o644)
    reopened = DiningStore(existing_db)
    assert stat.S_IMODE(existing_parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(existing_db.stat().st_mode) == 0o600
    reopened.close()


def test_room_pre_join_preview_without_member_leakage(pilot):
    _, _, clients = pilot
    owner, _friend, outsider = clients
    room_id, token = setup_room(clients)

    preview = outsider.get(f"/api/rooms/preview?token={token}")
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["room_id"] == room_id
    assert data["name"] == "Lunch friends"
    assert data["member_count"] == 2
    assert data["owner_name"] == "Ava"
    # Ensure no member identities, lists, or private responses leak
    assert "members" not in data
    assert "meals" not in data
    assert "user_id" not in data

    # Rotate invite token -> old token gives 404, new token gives preview
    rotated = owner.post(f"/api/rooms/{room_id}/invite").json()["invite_token"]
    assert outsider.get(f"/api/rooms/preview?token={token}").status_code == 404
    new_preview = outsider.get(f"/api/rooms/preview?token={rotated}")
    assert new_preview.status_code == 200
    assert new_preview.json()["name"] == "Lunch friends"

    # Invalid token returns 404
    assert outsider.get("/api/rooms/preview?token=invalid-token").status_code == 404


def test_shortlist_tie_breaking_and_random_draw_choice_modes(tmp_path):
    multi_options = [
        {
            "id": "opt-1",
            "outlet_id": "out-1",
            "name": "Noodle House",
            "_score": 0.80,
            "distance_km": 2.5,
            "evidence": [{"source_id": "s1"}],
        },
        {
            "id": "opt-2",
            "outlet_id": "out-2",
            "name": "Rice House",
            "_score": 0.85,
            "distance_km": 1.2,
            "evidence": [{"source_id": "s1"}, {"source_id": "s2"}],
        },
    ]

    def multi_rec(snapshot):
        return {
            "status": "shortlisted",
            "catalog_id": "test-cat",
            "catalog_version": "v1",
            "options": multi_options,
            "verification": [],
            "explanation": "Multiple options fixture",
        }

    store = DiningStore(tmp_path / "choice_modes.sqlite3")
    app = FastAPI()
    app.include_router(build_router(store, multi_rec))
    clients = [make_client(app, name) for name in ("Host", "Guest")]

    # 1. Test score tie-break
    _, meal = ready_meal(clients)
    generated = (
        clients[0]
        .post(
            f"/api/meals/{meal['id']}/generate",
            json={"expected_revision": meal["revision"]},
        )
        .json()
    )

    for client in clients:
        for opt in ("opt-1", "opt-2"):
            vote = client.post(
                f"/api/meals/{meal['id']}/votes",
                json={
                    "option_id": opt,
                    "expected_revision": generated["revision"],
                    "choice": "works",
                },
            )
            assert vote.status_code == 200

    tiebreak = clients[0].post(
        f"/api/meals/{meal['id']}/select",
        json={
            "expected_revision": generated["revision"],
            "choice_mode": "tie_break",
        },
    )
    assert tiebreak.status_code == 200, tiebreak.text
    dec = tiebreak.json()["decision"]
    assert dec["choice_mode"] == "tie_break"
    # opt-2 has higher _score (0.85 vs 0.80) and lower distance (1.2 vs 2.5)
    assert dec["option_id"] == "opt-2"

    # 2. Test agreed random draw on a new meal
    room_id = generated["room_id"]
    meal2, _ = create_meal(clients[0], room_id, idempotency_key="draw-meal-1")
    for client in clients:
        checkin(client, meal2["id"])
    meal2 = clients[0].get(f"/api/meals/{meal2['id']}").json()
    gen_resp = clients[0].post(
        f"/api/meals/{meal2['id']}/generate",
        json={"expected_revision": meal2["revision"]},
    )
    assert gen_resp.status_code == 200, gen_resp.text
    gen2 = gen_resp.json()
    for opt in ("opt-1", "opt-2"):
        clients[0].post(
            f"/api/meals/{meal2['id']}/votes",
            json={
                "option_id": opt,
                "expected_revision": gen2["revision"],
                "choice": "works",
            },
        )
        clients[1].post(
            f"/api/meals/{meal2['id']}/votes",
            json={
                "option_id": opt,
                "expected_revision": gen2["revision"],
                "choice": "works",
            },
        )

    draw = clients[0].post(
        f"/api/meals/{meal2['id']}/select",
        json={
            "expected_revision": gen2["revision"],
            "choice_mode": "random_draw",
        },
    )
    assert draw.status_code == 200, draw.text
    assert draw.json()["decision"]["choice_mode"] == "random_draw"
    assert draw.json()["decision"]["option_id"] in {"opt-1", "opt-2"}
    store.close()


def test_dietary_data_error_report_and_investigation_boundary(pilot):
    store, _, clients = pilot
    _, meal = ready_meal(clients)
    clients[0].post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )

    # Participant reports a dietary claim error on a shortlisted venue
    report = clients[1].post(
        f"/api/meals/{meal['id']}/report-data-error",
        json={
            "outlet_id": "outlet-a",
            "category": "dietary_claim",
            "description": "Menu incorrectly marked dish as vegan when it contains oyster sauce.",
        },
    )
    assert report.status_code == 200, report.text
    data = report.json()
    assert data["status"] == "investigating"
    assert data["report_id"].startswith("rpt_")

    with store.transaction() as db:
        # Verify report persisted
        row = db.execute(
            "SELECT * FROM data_error_reports WHERE id=?", (data["report_id"],)
        ).fetchone()
        assert row is not None
        assert row["category"] == "dietary_claim"
        assert row["status"] == "investigating"
        # Crucial PRD requirement: zero taste observations created
        assert (
            db.execute(
                "SELECT COUNT(*) FROM observations WHERE user_id=?",
                (clients[1].user_id,),
            ).fetchone()[0]
            == 0
        )
        # Verify audit trail
        assert (
            db.execute(
                "SELECT COUNT(*) FROM audit_events WHERE kind='data_error_reported'"
            ).fetchone()[0]
            == 1
        )


def test_recommendation_archives_survive_meal_invalidation(pilot):
    _, _, clients = pilot
    _, meal = ready_meal(clients)
    generated = (
        clients[0]
        .post(
            f"/api/meals/{meal['id']}/generate",
            json={"expected_revision": meal["revision"]},
        )
        .json()
    )

    # Initial history contains the first generation result
    history = clients[0].get(f"/api/meals/{meal['id']}/recommendation-history")
    assert history.status_code == 200
    assert len(history.json()) == 1
    assert history.json()[0]["revision"] == generated["revision"]
    assert history.json()[0]["result"]["status"] == "shortlisted"

    # User profile changes, invalidating current meal result to NULL
    clients[1].patch("/api/profile", json={"cuisines": ["Japanese"]})
    current = clients[0].get(f"/api/meals/{meal['id']}").json()
    assert current["result"] is None

    # Even though meals.result was cleared, the recommendation archive remains intact!
    preserved = clients[0].get(f"/api/meals/{meal['id']}/recommendation-history")
    assert preserved.status_code == 200
    archives = preserved.json()
    assert len(archives) == 1
    assert archives[0]["result"]["options"][0]["id"] == "outlet-option"
