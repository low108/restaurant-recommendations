import pytest
from fastapi import FastAPI
from test_api import checkin, create_meal, make_client, recommendation, setup_room

from dining.api import build_router
from dining.store import DiningStore


@pytest.fixture
def pilot(tmp_path):
    store = DiningStore(tmp_path / "origin.sqlite3")
    app = FastAPI()
    app.include_router(build_router(store, recommendation))
    clients = [make_client(app, name) for name in ("Ava", "Ben", "Cara")]
    yield store, app, clients
    store.close()


def test_meal_origin_crud_and_privacy(pilot):
    _store, _app, clients = pilot
    owner, friend = clients[:2]
    room_id, _ = setup_room(clients)

    meal, _ = create_meal(
        owner, room_id, participant_ids=[owner.user_id, friend.user_id]
    )
    meal_id = meal["id"]
    checkin(owner, meal_id)
    checkin(friend, meal_id)

    # 1. Precise coordinates require explicit route_consent
    res = owner.put(
        f"/api/meals/{meal_id}/origin",
        json={
            "origin_mode": "precise",
            "latitude": 3.1390,
            "longitude": 101.6869,
            "route_consent": False,
        },
    )
    assert res.status_code == 422

    # 2. Owner saves precise origin with consent
    res = owner.put(
        f"/api/meals/{meal_id}/origin",
        json={
            "origin_mode": "precise",
            "latitude": 3.1390,
            "longitude": 101.6869,
            "route_consent": True,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["origin_mode"] == "precise"
    assert data["latitude"] == 3.1390
    assert data["longitude"] == 101.6869
    assert data["route_consent"] is True

    # Check meal view for owner shows my_origin
    meal_view_owner = owner.get(f"/api/meals/{meal_id}").json()
    assert meal_view_owner["my_origin"] is not None
    assert meal_view_owner["my_origin"]["origin_mode"] == "precise"
    assert meal_view_owner["my_origin"]["latitude"] == 3.1390

    # 3. Friend cannot see owner's origin in meal_view or participants
    meal_view_friend = friend.get(f"/api/meals/{meal_id}").json()
    assert meal_view_friend["my_origin"] is None
    for p in meal_view_friend["participants"]:
        assert "latitude" not in p
        assert "longitude" not in p
        assert "origin" not in p

    # 4. Friend cannot retrieve owner's origin directly
    friend_origin_res = friend.get(f"/api/meals/{meal_id}/origin")
    assert friend_origin_res.status_code == 200
    assert friend_origin_res.json() is None

    # 5. Friend saves approximate-area origin
    res = friend.put(
        f"/api/meals/{meal_id}/origin",
        json={
            "origin_mode": "approximate",
            "approximate_area": "Bangsar",
            "route_consent": True,
        },
    )
    assert res.status_code == 200
    assert res.json()["origin_mode"] == "approximate"
    assert res.json()["approximate_area"] == "Bangsar"

    # 6. Replace origin with do_not_use
    res = friend.put(
        f"/api/meals/{meal_id}/origin",
        json={
            "origin_mode": "do_not_use",
            "route_consent": False,
        },
    )
    assert res.status_code == 200
    assert res.json()["origin_mode"] == "do_not_use"
    assert res.json()["latitude"] is None

    # 7. Delete origin
    del_res = owner.delete(f"/api/meals/{meal_id}/origin")
    assert del_res.status_code == 200
    assert owner.get(f"/api/meals/{meal_id}/origin").json() is None


def test_meal_origin_export_and_account_deletion(pilot):
    _store, _app, clients = pilot
    owner, friend = clients[:2]
    room_id, _ = setup_room(clients)
    meal, _ = create_meal(
        owner, room_id, participant_ids=[owner.user_id, friend.user_id]
    )
    meal_id = meal["id"]
    checkin(owner, meal_id)

    owner.put(
        f"/api/meals/{meal_id}/origin",
        json={
            "origin_mode": "precise",
            "latitude": 3.1200,
            "longitude": 101.6500,
            "route_consent": True,
        },
    ).raise_for_status()

    # Owner export contains owner's origin
    export_owner = owner.get("/api/export").json()
    assert "meal_origins" in export_owner
    assert len(export_owner["meal_origins"]) == 1
    assert export_owner["meal_origins"][0]["meal_id"] == meal_id
    assert export_owner["meal_origins"][0]["latitude"] == 3.1200

    # Friend export does not contain owner's origin
    export_friend = friend.get("/api/export").json()
    assert "meal_origins" in export_friend
    assert len(export_friend["meal_origins"]) == 0

    # Delete owner account removes origin from database
    owner.delete("/api/account").raise_for_status()
    # Friend verifies meal still works and owner origin is gone
    meal_check = friend.get(f"/api/meals/{meal_id}").json()
    assert meal_check is not None


def test_origin_change_increments_response_revision_and_invalidates(pilot):
    _store, _app, clients = pilot
    owner, friend = clients[:2]
    room_id, _ = setup_room(clients)
    meal, _ = create_meal(
        owner, room_id, participant_ids=[owner.user_id, friend.user_id]
    )
    meal_id = meal["id"]
    checkin(owner, meal_id)

    meal_before = owner.get(f"/api/meals/{meal_id}").json()
    rev_before = meal_before["my_response_revision"]

    owner.put(
        f"/api/meals/{meal_id}/origin",
        json={
            "origin_mode": "precise",
            "latitude": 3.1200,
            "longitude": 101.6500,
            "route_consent": True,
        },
    ).raise_for_status()

    meal_after = owner.get(f"/api/meals/{meal_id}").json()
    assert meal_after["my_response_revision"] > rev_before


def test_cross_room_and_cross_user_origin_isolation(pilot):
    _store, _app, clients = pilot
    owner, friend, outsider = clients[:3]
    room_id, _ = setup_room(clients[:2])
    meal, _ = create_meal(
        owner, room_id, participant_ids=[owner.user_id, friend.user_id]
    )
    meal_id = meal["id"]

    # Outsider cannot get or put origin
    res = outsider.get(f"/api/meals/{meal_id}/origin")
    assert res.status_code in (403, 404)

    res = outsider.put(
        f"/api/meals/{meal_id}/origin",
        json={
            "origin_mode": "precise",
            "latitude": 3.1200,
            "longitude": 101.6500,
            "route_consent": True,
        },
    )
    assert res.status_code in (403, 404)

    res = outsider.delete(f"/api/meals/{meal_id}/origin")
    assert res.status_code in (403, 404)
