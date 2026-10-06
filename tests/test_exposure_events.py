from test_api import checkin, create_meal, make_client, setup_room
from test_recommendation import ready_catalog, snapshot

from dining.exposure import (
    EVENT_CANDIDATE_ELIGIBLE,
    EVENT_CANDIDATE_SELECTED,
    EVENT_CANDIDATE_SHORTLISTED,
    EVENT_CANDIDATE_VOTED_ON,
    EVENT_CARD_SHOWN,
    get_exposure_metrics,
)
from webapp import create_app


def test_exposure_events_recorded_and_denominators_calculated(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "exposure.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("HostUser", "GuestUser")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room, meal_at=snapshot()["meal"]["meal_at"])

    for p in people:
        meal = checkin(p, meal["id"], craving="noodle", budget=50)

    queued = people[0].post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    assert queued.status_code == 202
    app.state.generation_worker.tick()

    with app.state.store.transaction() as db:
        events = db.execute(
            "SELECT event_type, outlet_id, option_id FROM exposure_events WHERE meal_id=?",
            (meal["id"],),
        ).fetchall()
        types = [e["event_type"] for e in events]
        assert EVENT_CANDIDATE_ELIGIBLE in types
        assert EVENT_CANDIDATE_SHORTLISTED in types

        metrics = get_exposure_metrics(db, meal_id=meal["id"])
        assert metrics["eligible_count"] > 0
        assert metrics["shortlisted_count"] > 0
        assert metrics["total_evaluated"] >= metrics["eligible_count"]
        assert 0.0 <= metrics["eligibility_rate"] <= 1.0


def test_card_shown_deduplicated_on_repeated_refreshes(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "dedup.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("Viewer1", "Viewer2")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room, meal_at=snapshot()["meal"]["meal_at"])

    for p in people:
        meal = checkin(p, meal["id"], craving="rice", budget=50)

    queued = people[0].post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    assert queued.status_code == 202
    app.state.generation_worker.tick()

    # View meal multiple times
    for _ in range(5):
        res = people[0].get(f"/api/meals/{meal['id']}")
        assert res.status_code == 200

    with app.state.store.transaction() as db:
        impressions = db.execute(
            "SELECT COUNT(*) FROM exposure_events WHERE meal_id=? AND event_type=? AND user_id=?",
            (meal["id"], EVENT_CARD_SHOWN, people[0].user_id),
        ).fetchone()[0]
        shortlisted_count = db.execute(
            "SELECT COUNT(*) FROM exposure_events WHERE meal_id=? AND event_type=?",
            (meal["id"], EVENT_CANDIDATE_SHORTLISTED),
        ).fetchone()[0]
        # Should only have 1 impression per shortlisted option, not 5x
        assert impressions == shortlisted_count


def test_private_veto_reason_omitted_from_vote_event(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "veto_priv.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("Voter1", "Voter2")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room, meal_at=snapshot()["meal"]["meal_at"])

    for p in people:
        meal = checkin(p, meal["id"], craving="soup", budget=50)

    queued = people[0].post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    assert queued.status_code == 202
    app.state.generation_worker.tick()
    view = people[0].get(f"/api/meals/{meal['id']}").json()
    option_id = view["result"]["options"][0]["id"]

    SECRET_VETO_REASON = "SECRET_PRIVATE_PEANUT_ALLERGY_ANAPHYLAXIS"
    vote_res = people[0].post(
        f"/api/meals/{meal['id']}/votes",
        json={
            "expected_revision": view["revision"],
            "option_id": option_id,
            "choice": "cannot_eat",
            "reason": SECRET_VETO_REASON,
        },
    )
    assert vote_res.status_code == 200

    with app.state.store.transaction() as db:
        vote_events = db.execute(
            "SELECT * FROM exposure_events WHERE meal_id=? AND event_type=?",
            (meal["id"], EVENT_CANDIDATE_VOTED_ON),
        ).fetchall()
        assert len(vote_events) == 1
        event = vote_events[0]
        assert event["option_id"] == option_id
        # Secret veto reason MUST NOT appear in exposure events
        dump = str(dict(event))
        assert SECRET_VETO_REASON not in dump


def test_historical_events_survive_result_invalidation(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "survive.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("InvUser1", "InvUser2")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room, meal_at=snapshot()["meal"]["meal_at"])

    for p in people:
        meal = checkin(p, meal["id"], craving="soup", budget=50)

    queued = people[0].post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    assert queued.status_code == 202
    app.state.generation_worker.tick()

    with app.state.store.transaction() as db:
        count_before = db.execute(
            "SELECT COUNT(*) FROM exposure_events WHERE meal_id=?", (meal["id"],)
        ).fetchone()[0]
        assert count_before > 0

    # Invalidate result by editing profile
    people[0].patch("/api/profile", json={"spice": "hot"})
    refreshed_meal = people[0].get(f"/api/meals/{meal['id']}").json()
    assert refreshed_meal["result"] is None

    # Events must survive invalidation
    with app.state.store.transaction() as db:
        count_after = db.execute(
            "SELECT COUNT(*) FROM exposure_events WHERE meal_id=?", (meal["id"],)
        ).fetchone()[0]
        assert count_after == count_before


def test_user_deletion_anonymizes_user_and_preserves_denominators(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "del_anon.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("DelUser1", "DelUser2")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room, meal_at=snapshot()["meal"]["meal_at"])

    for p in people:
        meal = checkin(p, meal["id"], craving="soup", budget=50)

    queued = people[0].post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    assert queued.status_code == 202
    app.state.generation_worker.tick()

    # View meal to trigger impressions
    people[0].get(f"/api/meals/{meal['id']}")

    user1_id = people[0].user_id
    with app.state.store.transaction() as db:
        impressions_user1 = db.execute(
            "SELECT COUNT(*) FROM exposure_events WHERE user_id=?", (user1_id,)
        ).fetchone()[0]
        assert impressions_user1 > 0
        total_before = db.execute("SELECT COUNT(*) FROM exposure_events").fetchone()[0]

    # Delete user1 account
    del_res = people[0].delete("/api/account")
    assert del_res.status_code == 200

    # Events must survive, user_id should be NULL
    with app.state.store.transaction() as db:
        total_after = db.execute("SELECT COUNT(*) FROM exposure_events").fetchone()[0]
        assert total_after == total_before
        remaining_user1 = db.execute(
            "SELECT COUNT(*) FROM exposure_events WHERE user_id=?", (user1_id,)
        ).fetchone()[0]
        assert remaining_user1 == 0


def test_candidate_selected_event_recorded_on_decision(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "sel_event.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("SelHost", "SelGuest")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room, meal_at=snapshot()["meal"]["meal_at"])

    for p in people:
        meal = checkin(p, meal["id"], craving="soup", budget=50)

    people[0].post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    app.state.generation_worker.tick()
    view = people[0].get(f"/api/meals/{meal['id']}").json()
    option_id = view["result"]["options"][0]["id"]

    # Both approve
    for p in people:
        p.post(
            f"/api/meals/{meal['id']}/votes",
            json={
                "expected_revision": view["revision"],
                "option_id": option_id,
                "choice": "works",
            },
        )

    # Host selects
    sel_res = people[0].post(
        f"/api/meals/{meal['id']}/select",
        json={
            "expected_revision": view["revision"],
            "option_id": option_id,
            "choice_mode": "manual",
        },
    )
    assert sel_res.status_code == 200

    with app.state.store.transaction() as db:
        events = db.execute(
            "SELECT * FROM exposure_events WHERE meal_id=? AND event_type=?",
            (meal["id"], EVENT_CANDIDATE_SELECTED),
        ).fetchall()
        assert len(events) == 1
        assert events[0]["option_id"] == option_id

        metrics = get_exposure_metrics(db, meal_id=meal["id"])
        assert metrics["selections_count"] == 1
        assert metrics["shortlist_acceptance_rate"] > 0.0


def test_operations_summary_and_export_include_exposure_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DINING_OPERATIONS_TOKEN", "test-operator-exposure-" * 3)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "ops_exp.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("OpsUser1", "OpsUser2")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room, meal_at=snapshot()["meal"]["meal_at"])

    for p in people:
        meal = checkin(p, meal["id"], craving="soup", budget=50)

    people[0].post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    app.state.generation_worker.tick()
    people[0].get(f"/api/meals/{meal['id']}")

    ops_res = people[0].get(
        "/api/operations/summary",
        headers={"X-Operations-Token": "test-operator-exposure-" * 3},
    )
    assert ops_res.status_code == 200
    ops_data = ops_res.json()
    assert "exposure_metrics" in ops_data
    assert ops_data["exposure_metrics"]["eligible_count"] > 0
    assert ops_data["exposure_metrics"]["impressions_count"] > 0

    export_res = people[0].get("/api/export")
    assert export_res.status_code == 200
    export_data = export_res.json()
    assert "exposure_events" in export_data
    assert len(export_data["exposure_events"]) > 0
