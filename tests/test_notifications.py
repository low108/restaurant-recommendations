from datetime import datetime, timedelta

from fastapi import FastAPI
from test_api import (
    approve,
    create_meal,
    generate,
    make_client,
    ready_meal,
    recommendation,
    select,
    setup_room,
)

from dining.api import build_router
from dining.core.store import DiningStore
from dining.notifications.reminders import NotificationService


def notification_app(tmp_path):
    store = DiningStore(tmp_path / "notifications.sqlite3")
    core = build_router(store, recommendation)
    service = NotificationService(store)
    app = FastAPI()
    app.include_router(core)
    app.include_router(service.router(core.authenticate, core.room_member))
    return app, store, service


def test_reminder_opt_in_survives_restart_and_does_not_duplicate(tmp_path):
    app, store, service = notification_app(tmp_path)
    people = [make_client(app, name) for name in ("ReminderA", "ReminderB")]
    room, _ = setup_room(people)
    assert (
        people[1]
        .patch("/api/notification-settings", json={"reminders_enabled": True})
        .status_code
        == 200
    )
    meal, _ = create_meal(people[0], room)
    due = datetime.fromisoformat(meal["answer_by"]) - timedelta(minutes=5)
    service.tick(at=due)
    NotificationService(store).tick(at=due + timedelta(seconds=10))
    inbox = people[1].get("/api/notifications").json()["notifications"]
    assert len([n for n in inbox if n["kind"] == "checkin_reminder"]) == 1
    assert not any(
        n["kind"] == "checkin_reminder"
        for n in people[0].get("/api/notifications").json()["notifications"]
    )
    store.close()


def test_muted_removed_or_expired_invitation_does_not_get_a_reminder(tmp_path):
    app, store, service = notification_app(tmp_path)
    people = [make_client(app, name) for name in ("QuietA", "QuietB")]
    room, _ = setup_room(people)
    people[1].patch("/api/notification-settings", json={"reminders_enabled": True})
    people[1].patch(f"/api/rooms/{room}/notification-settings", json={"muted": True})
    meal, _ = create_meal(people[0], room)
    service.tick(at=datetime.fromisoformat(meal["answer_by"]) - timedelta(minutes=5))
    people[1].patch(f"/api/rooms/{room}/notification-settings", json={"muted": False})
    service.tick(at=datetime.fromisoformat(meal["answer_by"]) + timedelta(minutes=1))
    inbox = people[1].get("/api/notifications").json()["notifications"]
    assert not any(n["kind"] == "checkin_reminder" for n in inbox)
    stranger = make_client(app, "QuietStranger")
    assert (
        stranger.patch(
            f"/api/rooms/{room}/notification-settings", json={"muted": True}
        ).status_code
        == 404
    )
    store.close()


def test_feedback_reminder_waits_until_finish_plus_one_hour(tmp_path):
    app, store, service = notification_app(tmp_path)
    people = [make_client(app, name) for name in ("AfterA", "AfterB")]
    _, meal = ready_meal(people)
    meal = generate(people[0], meal)
    for person in people:
        assert approve(person, meal).status_code == 200
    assert select(people[0], meal).status_code == 200
    due = datetime.fromisoformat(meal["meal_at"]) + timedelta(
        minutes=meal["duration_minutes"] + 60
    )
    service.tick(at=due - timedelta(seconds=1))
    assert not any(
        n["kind"] == "feedback_available"
        for n in people[0].get("/api/notifications").json()["notifications"]
    )
    service.tick(at=due)
    service.tick(at=due + timedelta(minutes=1))
    assert (
        len(
            [
                n
                for n in people[0].get("/api/notifications").json()["notifications"]
                if n["kind"] == "feedback_available"
            ]
        )
        == 1
    )
    store.close()


def test_muted_reminder_resumes_when_unmuted_before_the_same_deadline(tmp_path):
    app, store, service = notification_app(tmp_path)
    people = [make_client(app, name) for name in ("ResumeA", "ResumeB")]
    room, _ = setup_room(people)
    people[1].patch("/api/notification-settings", json={"reminders_enabled": True})
    meal, _ = create_meal(people[0], room)
    deadline = datetime.fromisoformat(meal["answer_by"])
    service.tick(at=deadline - timedelta(minutes=15))
    people[1].patch(f"/api/rooms/{room}/notification-settings", json={"muted": True})
    service.tick(at=deadline - timedelta(minutes=14))
    people[1].patch(f"/api/rooms/{room}/notification-settings", json={"muted": False})
    service.tick(at=deadline - timedelta(minutes=5))
    reminders = [
        n
        for n in people[1].get("/api/notifications").json()["notifications"]
        if n["kind"] == "checkin_reminder"
    ]
    assert len(reminders) == 1
    service.tick(at=deadline - timedelta(minutes=4))
    assert (
        len(
            [
                n
                for n in people[1].get("/api/notifications").json()["notifications"]
                if n["kind"] == "checkin_reminder"
            ]
        )
        == 1
    )
    store.close()
