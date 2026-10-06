from test_api import checkin, create_meal, make_client, setup_room
from test_recommendation import ready_catalog, snapshot

from webapp import create_app


def test_personal_recommendations_generated_from_group_eligible_pool_only(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "personal_rec.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("HostAlice", "GuestBob")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room, meal_at=snapshot()["meal"]["meal_at"])

    # Host craves noodles, Guest craves rice
    meal = checkin(people[0], meal["id"], craving="spicy noodle soup", budget=50)
    meal = checkin(people[1], meal["id"], craving="fried rice", budget=50)

    people[0].post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    app.state.generation_worker.tick()

    host_view = people[0].get(f"/api/meals/{meal['id']}").json()
    guest_view = people[1].get(f"/api/meals/{meal['id']}").json()

    # 1. Both get owner-scoped personal recommendations
    assert "my_personal_recommendations" in host_view
    assert "my_personal_recommendations" in guest_view

    host_recs = host_view["my_personal_recommendations"]
    guest_recs = guest_view["my_personal_recommendations"]
    assert 1 <= len(host_recs) <= 3
    assert 1 <= len(guest_recs) <= 3

    # 2. Every personal recommendation outlet must come from the group-eligible pool
    group_outlets = {opt["outlet_id"] for opt in host_view["result"]["options"]}
    for rec in host_recs:
        assert rec["outlet_id"] in group_outlets
        assert rec["item_id"]
        assert rec["rank"] >= 1
        assert "reason_codes" in rec
        assert rec["status"] == "suggested"

    # 3. Privacy: Host cannot see Guest's personal recommendations and scores
    assert "scores" not in host_view["result"]
    assert str(guest_recs) not in str(host_view["result"])


def test_saving_backup_does_not_alter_group_votes_or_decisions(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "backup.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("BHost", "BGuest")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room, meal_at=snapshot()["meal"]["meal_at"])

    for p in people:
        meal = checkin(p, meal["id"], craving="soup", budget=50)

    people[0].post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    app.state.generation_worker.tick()
    view = people[1].get(f"/api/meals/{meal['id']}").json()
    rev_before = view["revision"]

    # Guest saves top personal recommendation as backup
    backup_res = people[1].post(
        f"/api/meals/{meal['id']}/personal-recommendations/1/action",
        json={"expected_revision": rev_before, "action": "save_backup"},
    )
    assert backup_res.status_code == 200

    # Revision and group state remain completely unaltered
    view_after = people[1].get(f"/api/meals/{meal['id']}").json()
    assert view_after["revision"] == rev_before
    assert view_after["status"] == view["status"]
    assert view_after["my_personal_recommendations"][0]["status"] == "saved_backup"


def test_choosing_separately_before_selection_withdraws_and_invalidates(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "separate_pre.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("SepHost", "SepGuest")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room, meal_at=snapshot()["meal"]["meal_at"])

    for p in people:
        meal = checkin(p, meal["id"], craving="soup", budget=50)

    people[0].post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    app.state.generation_worker.tick()
    view = people[1].get(f"/api/meals/{meal['id']}").json()
    rev_before = view["revision"]

    # Guest1 chooses personal alternative separately before final selection
    sep_res = people[1].post(
        f"/api/meals/{meal['id']}/personal-recommendations/1/action",
        json={"expected_revision": rev_before, "action": "choose_separately"},
    )
    assert sep_res.status_code == 200

    # Pre-selection separate choice increments revision and invalidates the shortlist
    view_after = people[0].get(f"/api/meals/{meal['id']}").json()
    assert view_after["revision"] > rev_before
    assert view_after["result"] is None  # Invalidated for fresh check-ins


def test_choosing_separately_after_selection_preserves_group_decision_history(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "separate_post.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("PostHost", "PostGuest")]
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
    opt_id = view["result"]["options"][0]["id"]

    for p in people:
        p.post(
            f"/api/meals/{meal['id']}/votes",
            json={
                "expected_revision": view["revision"],
                "option_id": opt_id,
                "choice": "works",
            },
        )
    people[0].post(
        f"/api/meals/{meal['id']}/select",
        json={
            "expected_revision": view["revision"],
            "option_id": opt_id,
            "choice_mode": "manual",
        },
    )

    # After final selection: Guest chooses personal alternative
    sep_res = people[1].post(
        f"/api/meals/{meal['id']}/personal-recommendations/1/action",
        json={"expected_revision": view["revision"], "action": "choose_separately"},
    )
    assert sep_res.status_code == 200

    # Historical group decision remains intact
    host_view = people[0].get(f"/api/meals/{meal['id']}").json()
    assert host_view["status"] == "selected"
    assert host_view["decision"]["option_id"] == opt_id
    assert host_view["decision"]["checked"] is True


def test_export_and_deletion_cover_personal_recommendations(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "export_del.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("ExpHost", "ExpGuest")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room, meal_at=snapshot()["meal"]["meal_at"])

    for p in people:
        meal = checkin(p, meal["id"], craving="soup", budget=50)

    people[0].post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    app.state.generation_worker.tick()

    # 1. Export includes personal recommendations
    exp_res = people[0].get("/api/export")
    assert exp_res.status_code == 200
    export_data = exp_res.json()
    assert "personal_recommendations" in export_data
    assert len(export_data["personal_recommendations"]) > 0

    # 2. Deletion deletes personal recommendations
    del_res = people[0].delete("/api/account")
    assert del_res.status_code == 200
    with app.state.store.transaction() as db:
        remaining = db.execute(
            "SELECT COUNT(*) FROM personal_recommendations WHERE user_id=?",
            (people[0].user_id,),
        ).fetchone()[0]
        assert remaining == 0
