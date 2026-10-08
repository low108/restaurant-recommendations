from datetime import datetime, timedelta

from fastapi import FastAPI
from test_api import approve, generate, make_client, ready_meal, recommendation, select

from dining.api import build_router
from dining.core.store import DiningStore, decode
from dining.notifications.reminders import NotificationService


def selected_meal(tmp_path, *, notifications=False):
    store = DiningStore(tmp_path / "lifecycle.sqlite3")
    core = build_router(store, recommendation)
    app = FastAPI()
    app.include_router(core)
    service = None
    if notifications:
        service = NotificationService(store)
        app.include_router(service.router(core.authenticate, core.room_member))
    people = [make_client(app, name) for name in ("LifecycleA", "LifecycleB")]
    _, meal = ready_meal(people)
    meal = generate(people[0], meal)
    for person in people:
        assert approve(person, meal).status_code == 200
    chosen = select(people[0], meal)
    assert chosen.status_code == 200, chosen.text
    return store, people, chosen.json(), service


def test_selected_meal_persists_feedback_window_then_closes_after_seven_days(
    tmp_path, monkeypatch
):
    store, people, meal, _ = selected_meal(tmp_path)
    finish = datetime.fromisoformat(meal["meal_at"]) + timedelta(
        minutes=meal["duration_minutes"]
    )

    monkeypatch.setattr("dining.api.now", lambda: finish + timedelta(seconds=1))
    awaiting = people[0].get(f"/api/meals/{meal['id']}")
    assert awaiting.status_code == 200
    view = awaiting.json()
    assert view["status"] == "awaiting_feedback"
    assert view["decision"] == meal["decision"]
    assert view["result"] == meal["result"]
    with store.transaction() as db:
        saved = db.execute(
            "SELECT status,lifecycle_reason FROM meals WHERE id=?", (meal["id"],)
        ).fetchone()
        assert saved["status"] == "awaiting_feedback"
        assert saved["lifecycle_reason"] == "The feedback window is open"

    feedback = people[0].post(
        f"/api/meals/{meal['id']}/feedback",
        json={
            "option_id": meal["decision"]["option_id"],
            "visited": True,
            "rating": 5,
        },
    )
    assert feedback.status_code == 200, feedback.text
    assert feedback.json()["status"] == "awaiting_feedback"

    monkeypatch.setattr("dining.api.now", lambda: finish + timedelta(days=7, seconds=1))
    closed = people[1].get(f"/api/meals/{meal['id']}")
    assert closed.status_code == 200
    view = closed.json()
    assert view["status"] == "closed"
    assert view["decision"] == meal["decision"]
    assert view["result"] == meal["result"]
    with store.transaction() as db:
        saved = db.execute(
            "SELECT status,lifecycle_reason FROM meals WHERE id=?", (meal["id"],)
        ).fetchone()
        assert saved["status"] == "closed"
        assert saved["lifecycle_reason"] == "The seven-day feedback window ended"
        assert (
            decode(
                db.execute(
                    "SELECT payload FROM feedback WHERE meal_id=? AND user_id=?",
                    (meal["id"], people[0].user_id),
                ).fetchone()["payload"]
            )["rating"]
            == 5
        )

    too_late = people[1].post(
        f"/api/meals/{meal['id']}/feedback",
        json={
            "option_id": meal["decision"]["option_id"],
            "visited": True,
            "rating": 4,
        },
    )
    assert too_late.status_code == 409
    store.close()


def test_feedback_worker_persists_awaiting_feedback_and_delivers_notification(tmp_path):
    store, people, meal, service = selected_meal(tmp_path, notifications=True)
    finish = datetime.fromisoformat(meal["meal_at"]) + timedelta(
        minutes=meal["duration_minutes"]
    )
    service.tick(at=finish + timedelta(hours=1))
    with store.transaction() as db:
        assert (
            db.execute("SELECT status FROM meals WHERE id=?", (meal["id"],)).fetchone()[
                "status"
            ]
            == "awaiting_feedback"
        )
    for person in people:
        inbox = person.get("/api/notifications").json()["notifications"]
        assert len([row for row in inbox if row["kind"] == "feedback_available"]) == 1
    store.close()


def test_feedback_worker_closes_expired_window_without_late_notification(tmp_path):
    store, people, meal, service = selected_meal(tmp_path, notifications=True)
    finish = datetime.fromisoformat(meal["meal_at"]) + timedelta(
        minutes=meal["duration_minutes"]
    )

    service.tick(at=finish + timedelta(days=7, seconds=1))

    with store.transaction() as db:
        saved = db.execute(
            "SELECT status,lifecycle_reason FROM meals WHERE id=?", (meal["id"],)
        ).fetchone()
        assert saved["status"] == "closed"
        assert saved["lifecycle_reason"] == "The seven-day feedback window ended"
    for person in people:
        inbox = person.get("/api/notifications").json()["notifications"]
        assert not [row for row in inbox if row["kind"] == "feedback_available"]
    store.close()
