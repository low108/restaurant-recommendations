import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_api import checkin, create_meal, make_client, recommendation

from dining.api import build_router
from dining.core.store import DiningStore


@pytest.fixture
def auth_fixture(tmp_path):
    store = DiningStore(tmp_path / "auth_matrix.sqlite3")
    app = FastAPI()
    app.include_router(build_router(store, recommendation))

    owner = make_client(app, "RoomOwner")
    participant = make_client(app, "ParticipantDiner")
    uninvited_member = make_client(app, "UninvitedMember")
    removed_member = make_client(app, "RemovedMember")
    outsider = make_client(app, "OutsiderUser")

    # Setup room with owner
    room_res = owner.post("/api/rooms", json={"name": "Auth Room"})
    room_id = room_res.json()["room"]["id"]
    invite_token = room_res.json()["invite_token"]

    # Join other members
    for client in (participant, uninvited_member, removed_member):
        client.post("/api/rooms/join", json={"token": invite_token}).raise_for_status()

    # Create meal inviting only owner and participant
    meal, _ = create_meal(
        owner, room_id, participant_ids=[owner.user_id, participant.user_id]
    )
    meal_id = meal["id"]

    # Remove removed_member from room
    owner.delete(
        f"/api/rooms/{room_id}/members/{removed_member.user_id}"
    ).raise_for_status()

    # Create an unauthenticated client
    unauth = TestClient(app)

    yield {
        "store": store,
        "app": app,
        "room_id": room_id,
        "meal_id": meal_id,
        "owner": owner,
        "participant": participant,
        "uninvited_member": uninvited_member,
        "removed_member": removed_member,
        "outsider": outsider,
        "unauth": unauth,
    }
    store.close()


def test_unauthenticated_requests_refused(auth_fixture):
    unauth = auth_fixture["unauth"]
    room_id = auth_fixture["room_id"]
    meal_id = auth_fixture["meal_id"]

    routes = [
        ("GET", "/api/profile"),
        ("PATCH", "/api/profile"),
        ("GET", "/api/rooms"),
        ("POST", "/api/rooms"),
        ("GET", f"/api/rooms/{room_id}"),
        ("POST", f"/api/rooms/{room_id}/invite"),
        ("POST", f"/api/rooms/{room_id}/meals"),
        ("GET", f"/api/meals/{meal_id}"),
        ("PUT", f"/api/meals/{meal_id}/response"),
        ("PUT", f"/api/meals/{meal_id}/origin"),
        ("GET", f"/api/meals/{meal_id}/origin"),
        ("DELETE", f"/api/meals/{meal_id}/origin"),
        ("POST", f"/api/meals/{meal_id}/preparation-confirmations"),
        ("GET", f"/api/meals/{meal_id}/preparation-confirmations"),
        ("POST", f"/api/meals/{meal_id}/generate"),
        ("POST", f"/api/meals/{meal_id}/votes"),
        ("POST", f"/api/meals/{meal_id}/select"),
        ("GET", "/api/learning"),
        ("GET", "/api/notifications"),
        ("GET", "/api/export"),
        ("DELETE", "/api/account"),
    ]

    for method, path in routes:
        if method == "GET":
            res = unauth.get(path)
        elif method == "POST":
            res = unauth.post(
                path,
                json={
                    "name": "test",
                    "token": "test",
                    "kind": "lunch",
                    "meal_at": "2026-10-07T12:00:00+08:00",
                    "location_label": "SS2",
                    "latitude": 3.12,
                    "longitude": 101.62,
                    "idempotency_key": "k",
                },
            )
        elif method == "PUT":
            res = unauth.put(
                path,
                json={
                    "origin_mode": "do_not_use",
                    "attendance": "join",
                    "expected_response_revision": 0,
                },
            )
        elif method == "PATCH":
            res = unauth.patch(path, json={})
        elif method == "DELETE":
            res = unauth.delete(path)
        # All unauthenticated calls must be refused with 401
        assert res.status_code == 401, f"{method} {path} returned {res.status_code}"


def test_expired_session_refused(auth_fixture):
    store = auth_fixture["store"]
    participant = auth_fixture["participant"]
    meal_id = auth_fixture["meal_id"]

    # Expire participant session in database
    with store.transaction() as db:
        db.execute("UPDATE sessions SET expires_at=?", (time.time() - 100,))

    res = participant.get(f"/api/meals/{meal_id}")
    assert res.status_code == 401


def test_cross_room_and_outsider_access_refused(auth_fixture):
    outsider = auth_fixture["outsider"]
    room_id = auth_fixture["room_id"]
    meal_id = auth_fixture["meal_id"]

    routes = [
        ("GET", f"/api/rooms/{room_id}"),
        ("POST", f"/api/rooms/{room_id}/meals"),
        ("GET", f"/api/meals/{meal_id}"),
        ("GET", f"/api/meals/{meal_id}/origin"),
        ("PUT", f"/api/meals/{meal_id}/origin"),
        ("DELETE", f"/api/meals/{meal_id}/origin"),
        ("POST", f"/api/meals/{meal_id}/preparation-confirmations"),
        ("GET", f"/api/meals/{meal_id}/preparation-confirmations"),
        ("POST", f"/api/meals/{meal_id}/generate"),
        ("POST", f"/api/meals/{meal_id}/votes"),
        ("POST", f"/api/meals/{meal_id}/select"),
    ]

    meal_payload = {
        "kind": "lunch",
        "meal_at": "2026-10-07T12:00:00+08:00",
        "location_label": "SS2",
        "latitude": 3.12,
        "longitude": 101.62,
        "idempotency_key": "cross-test-k",
    }

    for method, path in routes:
        if method == "GET":
            res = outsider.get(path)
        elif method == "POST":
            payload = meal_payload
            if "preparation-confirmations" in path:
                payload = {
                    "outlet_id": "test",
                    "requirement_category": "allergen",
                    "exact_bounded_claim": "test claim",
                    "confirmed_by": "Chef",
                    "confirmation_channel": "phone",
                    "confirmed_at": "2026-10-07T12:00:00+08:00",
                    "expires_at": "2026-10-08T12:00:00+08:00",
                }
            elif "generate" in path:
                payload = {"expected_revision": 1}
            elif "votes" in path:
                payload = {
                    "option_id": "opt1",
                    "choice": "works",
                    "expected_revision": 1,
                }
            elif "select" in path:
                payload = {"option_id": "opt1", "expected_revision": 1}
            res = outsider.post(path, json=payload)
        elif method == "PUT":
            res = outsider.put(path, json={"origin_mode": "do_not_use"})
        elif method == "DELETE":
            res = outsider.delete(path)
        # Never reveals private resources to outsiders
        assert res.status_code in (403, 404), (
            f"{method} {path} returned {res.status_code}"
        )


def test_removed_member_immediately_loses_access(auth_fixture):
    removed = auth_fixture["removed_member"]
    room_id = auth_fixture["room_id"]
    meal_id = auth_fixture["meal_id"]

    assert removed.get(f"/api/rooms/{room_id}").status_code in (403, 404)
    assert removed.get(f"/api/meals/{meal_id}").status_code in (403, 404)
    assert removed.get(f"/api/meals/{meal_id}/origin").status_code in (403, 404)


def test_uninvited_room_member_cannot_modify_meal_or_origin(auth_fixture):
    uninvited = auth_fixture["uninvited_member"]
    meal_id = auth_fixture["meal_id"]

    # Member not invited to meal receives 404 (does not disclose existence to uninvited members)
    assert uninvited.get(f"/api/meals/{meal_id}").status_code == 404

    # Cannot set origin (not an invited participant)
    put_origin = uninvited.put(
        f"/api/meals/{meal_id}/origin",
        json={
            "origin_mode": "precise",
            "latitude": 3.12,
            "longitude": 101.62,
            "route_consent": True,
        },
    )
    assert put_origin.status_code == 404

    # Cannot check in
    checkin_res = uninvited.put(
        f"/api/meals/{meal_id}/response",
        json={"attendance": "join", "expected_response_revision": 0},
    )
    assert checkin_res.status_code == 404


def test_shared_response_projections_omit_private_details(auth_fixture):
    owner = auth_fixture["owner"]
    participant = auth_fixture["participant"]
    meal_id = auth_fixture["meal_id"]

    # Owner sets origin and private craving
    checkin(owner, meal_id, craving="Secret craving spicy tom yum", budget=45)
    owner.put(
        f"/api/meals/{meal_id}/origin",
        json={
            "origin_mode": "precise",
            "latitude": 3.1234,
            "longitude": 101.6543,
            "route_consent": True,
        },
    ).raise_for_status()

    # Participant checks in
    checkin(participant, meal_id, craving="Mild noodles", budget=30)

    # Participant fetches meal view
    view = participant.get(f"/api/meals/{meal_id}").json()

    # Participant sees their own response and origin
    assert view["my_response"]["craving"] == "Mild noodles"
    assert view["my_origin"] is None

    # In participants list, no private answers, origins or sensitive fields are exposed
    for p in view["participants"]:
        assert "craving" not in p
        assert "budget" not in p
        assert "origin" not in p
        assert "latitude" not in p
        assert "longitude" not in p
        assert "Secret craving" not in str(p)
        assert "3.1234" not in str(p)
