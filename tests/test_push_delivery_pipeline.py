import time

import pytest

from dining.push import PushSubscriptionManager
from dining.push_worker import FakePushGateway, PushDeliveryWorker
from dining.store import DiningStore


@pytest.fixture
def store(tmp_path):
    store = DiningStore(tmp_path / "push_pipeline.sqlite3")
    with store.transaction() as db:
        db.execute(
            "INSERT INTO users(id,email,name,password_hash,profile,created_at) VALUES('u1','u1@test','U1','hash','{}','2026-10-06T00:00:00Z')"
        )
    yield store
    store.close()


def test_push_delivery_worker_delivers_and_deduplicates(store):
    with store.transaction() as db:
        sub = PushSubscriptionManager.register(
            db,
            user_id="u1",
            endpoint="https://push.test/ep1",
            p256dh="key123456789012345",
            auth="auth12345678",
        )
        sub_id = sub["id"]

    gateway = FakePushGateway()
    worker = PushDeliveryWorker(store, gateway=gateway)

    now = time.time()
    # Enqueue a push job
    worker.enqueue_job(
        user_id="u1",
        subscription_id=sub_id,
        event_id="evt-checkin-1",
        kind="checkin_reminder",
        payload={"title": "Makan Together", "body": "Check-in reminder"},
        expires_at=now + 3600,
    )

    # Enqueue duplicate event for same user/device - must be deduplicated
    worker.enqueue_job(
        user_id="u1",
        subscription_id=sub_id,
        event_id="evt-checkin-1",
        kind="checkin_reminder",
        payload={"title": "Makan Together", "body": "Check-in reminder"},
        expires_at=now + 3600,
    )

    result = worker.tick()
    assert result["delivered"] == 1
    assert gateway.delivered_count == 1

    # Worker restart does not duplicate delivered event
    worker_restarted = PushDeliveryWorker(store, gateway=gateway)
    restarted_result = worker_restarted.tick()
    assert restarted_result["delivered"] == 0
    assert gateway.delivered_count == 1


def test_expired_meal_reminders_are_suppressed(store):
    with store.transaction() as db:
        sub = PushSubscriptionManager.register(
            db,
            user_id="u1",
            endpoint="https://push.test/ep2",
            p256dh="key123456789012345",
            auth="auth12345678",
        )
        sub_id = sub["id"]

    gateway = FakePushGateway()
    worker = PushDeliveryWorker(store, gateway=gateway)

    # Job expired 10 minutes ago
    worker.enqueue_job(
        user_id="u1",
        subscription_id=sub_id,
        event_id="evt-expired-reminder",
        kind="checkin_reminder",
        payload={"title": "Makan", "body": "Old reminder"},
        expires_at=time.time() - 600,
    )

    result = worker.tick()
    assert result["expired"] == 1
    assert result["delivered"] == 0
    assert gateway.delivered_count == 0


def test_provider_uncertainty_is_tracked_and_visible(store):
    with store.transaction() as db:
        sub = PushSubscriptionManager.register(
            db,
            user_id="u1",
            endpoint="https://push.test/ep3",
            p256dh="key123456789012345",
            auth="auth12345678",
        )
        sub_id = sub["id"]

    # Gateway simulates ambiguous timeout response
    gateway = FakePushGateway(simulate_ambiguous=True)
    worker = PushDeliveryWorker(store, gateway=gateway)

    worker.enqueue_job(
        user_id="u1",
        subscription_id=sub_id,
        event_id="evt-ambiguous-ack",
        kind="meal_invitation",
        payload={"title": "Makan", "body": "Invited to meal"},
        expires_at=time.time() + 3600,
    )

    result = worker.tick()
    assert result["ambiguous"] == 1
    health = worker.health()
    assert health["ambiguous_count"] >= 1


def test_invalid_subscription_410_disables_subscription(store):
    with store.transaction() as db:
        sub = PushSubscriptionManager.register(
            db,
            user_id="u1",
            endpoint="https://push.test/ep4-gone",
            p256dh="key123456789012345",
            auth="auth12345678",
        )
        sub_id = sub["id"]

    # Gateway simulates 410 Gone (unsubscribed at browser level)
    gateway = FakePushGateway(simulate_status_code=410)
    worker = PushDeliveryWorker(store, gateway=gateway)

    worker.enqueue_job(
        user_id="u1",
        subscription_id=sub_id,
        event_id="evt-unsub-device",
        kind="meal_decision",
        payload={"title": "Makan", "body": "Decision confirmed"},
        expires_at=time.time() + 3600,
    )

    result = worker.tick()
    assert result["failed"] == 1
    # Subscription in store must now be disabled
    with store.transaction() as db:
        row = db.execute(
            "SELECT status FROM push_subscriptions WHERE id=?", (sub_id,)
        ).fetchone()
        assert row["status"] == "disabled"
