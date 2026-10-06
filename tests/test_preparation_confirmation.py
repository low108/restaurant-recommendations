from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from test_api import create_meal, make_client, setup_room
from test_recommendation import ready_catalog, snapshot

from dining.api import build_router
from dining.recommendation import Recommender
from dining.store import DiningStore


@pytest.fixture
def pilot(tmp_path):
    store = DiningStore(tmp_path / "prep.sqlite3")
    catalog = ready_catalog()
    recommender = Recommender(catalog)
    app = FastAPI()
    app.include_router(build_router(store, recommender))
    clients = [make_client(app, name) for name in ("PrepUserA", "PrepUserB")]
    yield store, app, clients, catalog
    store.close()


def test_menu_alone_cannot_prove_allergen_absence():
    catalog = ready_catalog()
    state = snapshot()
    state["participants"][0]["profile"]["allergy_status"] = "declared"
    state["participants"][0]["profile"]["allergens"] = ["peanut"]
    state["participants"][0]["response"]["requirements_confirmed"] = True

    # Without preparation confirmation, cannot pass checked
    recommender = Recommender(catalog)
    result = recommender(state)
    assert result["status"] == "needs_verification"
    assert not result["options"]


def test_bounded_confirmation_satisfies_exact_claim():
    catalog = ready_catalog()
    outlet = catalog.outlets[0]
    now = datetime.now(timezone.utc)
    future = now + timedelta(days=2)

    state = snapshot()
    state["participants"][0]["profile"]["allergy_status"] = "declared"
    state["participants"][0]["profile"]["allergens"] = ["peanut"]
    state["participants"][0]["response"]["requirements_confirmed"] = True

    # Add confirmation for peanut at this outlet
    state["preparation_confirmations"] = [
        {
            "outlet_id": outlet.outlet_id,
            "requirement_category": "allergen",
            "exact_bounded_claim": "Dedicated peanut-free preparation area and separate utensils verified",
            "confirmed_by": "Chef Zul",
            "confirmation_channel": "phone",
            "confirmed_at": now.isoformat(),
            "expires_at": future.isoformat(),
        }
    ]

    recommender = Recommender(catalog)
    result = recommender(state)

    # Outlet with exact confirmation passes, while unconfirmed outlets do not pass for allergy
    assert result["status"] == "shortlisted"
    assert len(result["options"]) == 1
    assert result["options"][0]["outlet_id"] == outlet.outlet_id


def test_expired_confirmation_becomes_unknown():
    catalog = ready_catalog()
    outlet = catalog.outlets[0]
    now = datetime.now(timezone.utc)
    past = now - timedelta(days=10)
    expired = now - timedelta(days=1)

    state = snapshot()
    state["participants"][0]["profile"]["allergy_status"] = "declared"
    state["participants"][0]["profile"]["allergens"] = ["peanut"]
    state["participants"][0]["response"]["requirements_confirmed"] = True

    # Expired confirmation
    state["preparation_confirmations"] = [
        {
            "outlet_id": outlet.outlet_id,
            "requirement_category": "allergen",
            "exact_bounded_claim": "Dedicated peanut-free area",
            "confirmed_by": "Chef Zul",
            "confirmation_channel": "phone",
            "confirmed_at": past.isoformat(),
            "expires_at": expired.isoformat(),
        }
    ]

    recommender = Recommender(catalog)
    result = recommender(state)

    # Expired confirmation is rejected and treated as unknown
    assert result["status"] == "needs_verification" or not result["options"]


def test_different_allergen_claim_does_not_satisfy():
    catalog = ready_catalog()
    outlet = catalog.outlets[0]
    now = datetime.now(timezone.utc)
    future = now + timedelta(days=2)

    state = snapshot()
    state["participants"][0]["profile"]["allergy_status"] = "declared"
    state["participants"][0]["profile"]["allergens"] = ["shellfish"]
    state["participants"][0]["response"]["requirements_confirmed"] = True

    # Confirmation covers peanut, but user declared shellfish
    state["preparation_confirmations"] = [
        {
            "outlet_id": outlet.outlet_id,
            "requirement_category": "allergen",
            "exact_bounded_claim": "Dedicated peanut-free prep area",
            "confirmed_by": "Chef Zul",
            "confirmation_channel": "phone",
            "confirmed_at": now.isoformat(),
            "expires_at": future.isoformat(),
        }
    ]

    recommender = Recommender(catalog)
    result = recommender(state)

    # Shellfish is unconfirmed, so cannot pass
    assert result["status"] == "needs_verification" or not result["options"]


def test_confirmation_api_crud_and_learning_isolation(pilot):
    _store, _app, clients, _catalog = pilot
    owner, friend = clients[:2]
    room_id, _ = setup_room(clients)
    meal, _ = create_meal(
        owner, room_id, participant_ids=[owner.user_id, friend.user_id]
    )
    meal_id = meal["id"]

    now = datetime.now(timezone.utc)
    future = now + timedelta(days=1)

    # 1. Add preparation confirmation via API
    res = owner.post(
        f"/api/meals/{meal_id}/preparation-confirmations",
        json={
            "outlet_id": "demo-outlet-soup",
            "requirement_category": "allergen",
            "exact_bounded_claim": "No peanut or nut ingredients handled on separate preparation line",
            "confirmed_by": "Manager Sarah",
            "confirmation_channel": "phone",
            "confirmed_at": now.isoformat(),
            "expires_at": future.isoformat(),
        },
    )
    assert res.status_code == 201
    record = res.json()
    assert (
        record["exact_bounded_claim"]
        == "No peanut or nut ingredients handled on separate preparation line"
    )
    conf_id = record["id"]

    # 2. Query confirmations
    list_res = friend.get(f"/api/meals/{meal_id}/preparation-confirmations")
    assert list_res.status_code == 200
    confirmations = list_res.json()
    assert len(confirmations) == 1
    assert confirmations[0]["id"] == conf_id

    # 3. Confirmation text must never enter taste learning observations
    obs_res = owner.get("/api/learning").json()
    obs_text = str(obs_res)
    assert "No peanut or nut ingredients handled" not in obs_text

    # 4. Delete confirmation
    del_res = owner.delete(f"/api/meals/{meal_id}/preparation-confirmations/{conf_id}")
    assert del_res.status_code == 200
    assert len(owner.get(f"/api/meals/{meal_id}/preparation-confirmations").json()) == 0
