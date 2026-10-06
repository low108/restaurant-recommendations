import pytest
from fastapi import FastAPI
from test_api import make_client, recommendation

from dining.api import build_router
from dining.push import create_push_payload, format_safe_notification
from dining.store import DiningStore


@pytest.fixture
def pilot(tmp_path):
    store = DiningStore(tmp_path / "push.sqlite3")
    app = FastAPI()
    app.include_router(build_router(store, recommendation))
    clients = [make_client(app, name) for name in ("PushUserA", "PushUserB")]
    yield store, app, clients
    store.close()


def test_push_subscription_crud_and_owner_scoping(pilot):
    _store, _app, clients = pilot
    user_a, user_b = clients[:2]

    sub_payload = {
        "endpoint": "https://push.example.test/sub/device-alpha",
        "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM=",
        "auth": "tBHItJI5svbpez7KI4CCXg==",
        "user_agent": "Mozilla/5.0 TestBrowser",
    }

    # 1. User A registers push subscription
    res = user_a.post("/api/push/subscriptions", json=sub_payload)
    assert res.status_code == 201
    created = res.json()
    assert created["endpoint"] == sub_payload["endpoint"]

    # 2. User A can list their subscriptions
    list_a = user_a.get("/api/push/subscriptions").json()
    assert len(list_a) == 1
    assert list_a[0]["endpoint"] == sub_payload["endpoint"]

    # 3. User B cannot see User A's subscription
    list_b = user_b.get("/api/push/subscriptions").json()
    assert len(list_b) == 0

    # 4. User B cannot access or unsubscribe User A's endpoint
    del_b = user_b.post(
        "/api/push/unsubscribe", json={"endpoint": sub_payload["endpoint"]}
    )
    assert del_b.status_code in (403, 404)

    # 5. User A unsubscribes
    del_a = user_a.post(
        "/api/push/unsubscribe", json={"endpoint": sub_payload["endpoint"]}
    )
    assert del_a.status_code == 200
    assert len(user_a.get("/api/push/subscriptions").json()) == 0


def test_account_switching_cannot_reuse_prior_subscription(pilot):
    _store, _app, clients = pilot
    user_a, user_b = clients[:2]

    endpoint = "https://push.example.test/sub/shared-device"
    sub_payload = {
        "endpoint": endpoint,
        "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM=",
        "auth": "tBHItJI5svbpez7KI4CCXg==",
    }

    # User A registers device
    user_a.post("/api/push/subscriptions", json=sub_payload).raise_for_status()

    # User B logs in on same device and registers same endpoint
    user_b.post("/api/push/subscriptions", json=sub_payload).raise_for_status()

    # User A's association with that endpoint must be removed/disabled
    list_a = user_a.get("/api/push/subscriptions").json()
    assert len(list_a) == 0

    # User B now owns the subscription
    list_b = user_b.get("/api/push/subscriptions").json()
    assert len(list_b) == 1
    assert list_b[0]["endpoint"] == endpoint


def test_push_payloads_contain_no_private_dietary_or_location_data():
    raw_message = (
        "Diner peanut allergy confirmed. Location coordinates 3.1234, 101.5678. "
        "A meal shortlist is ready for your review."
    )
    safe = format_safe_notification("meal_shortlist", raw_message)

    payload = create_push_payload(
        kind="meal_shortlist",
        safe_message=safe,
        meal_id="meal-123",
    )

    text_dump = str(payload)
    # Never leak allergens or coordinates in push notifications
    assert "peanut" not in text_dump.lower()
    assert "allergy" not in text_dump.lower()
    assert "3.1234" not in text_dump
    assert "101.5678" not in text_dump
    assert "coordinates" not in text_dump.lower()


def test_account_deletion_removes_push_subscriptions(pilot):
    store, _app, clients = pilot
    user_a = clients[0]

    sub_payload = {
        "endpoint": "https://push.example.test/sub/device-deletion-test",
        "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM=",
        "auth": "tBHItJI5svbpez7KI4CCXg==",
    }
    user_a.post("/api/push/subscriptions", json=sub_payload).raise_for_status()

    # Delete account
    user_a.delete("/api/account").raise_for_status()

    # Subscriptions in database are removed
    with store.transaction() as db:
        rows = db.execute(
            "SELECT * FROM push_subscriptions WHERE user_id=?", (user_a.user_id,)
        ).fetchall()
        assert len(rows) == 0
