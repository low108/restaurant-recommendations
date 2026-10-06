from datetime import datetime, timedelta, timezone

from test_api import checkin, create_meal, make_client, setup_room
from test_recommendation import ready_catalog, snapshot

from dining.ranking import score_item
from webapp import create_app


def test_service_complaint_does_not_reduce_cuisine_affinity(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "service_taste.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("HostUser", "GuestUser")]
    room, _ = setup_room(people)
    people[0].patch(
        "/api/profile",
        json={
            "memory_enabled": True,
            "taste_preferences": {"cuisine:malaysian": "like"},
        },
    )

    meal, _ = create_meal(people[0], room)
    for p in people:
        meal = checkin(p, meal["id"], craving="rice", budget=50)

    people[0].post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    app.state.generation_worker.tick()
    view = people[0].get(f"/api/meals/{meal['id']}").json()
    option_id = view["result"]["options"][0]["id"]
    outlet_id = view["result"]["options"][0]["outlet_id"]

    for p in people:
        p.post(
            f"/api/meals/{meal['id']}/votes",
            json={
                "expected_revision": view["revision"],
                "option_id": option_id,
                "choice": "works",
            },
        )
    people[0].post(
        f"/api/meals/{meal['id']}/select",
        json={
            "expected_revision": view["revision"],
            "option_id": option_id,
            "choice_mode": "manual",
        },
    )

    # Fast forward past meal planned finish
    monkeypatch.setattr(
        "dining.api.now",
        lambda: datetime.fromisoformat(meal["meal_at"]) + timedelta(hours=3),
    )

    # Submit feedback with service complaint
    fb_res = people[0].post(
        f"/api/meals/{meal['id']}/feedback",
        json={
            "visited": True,
            "option_id": option_id,
            "enjoyment": "did_not_enjoy",
            "influences": ["service"],
            "comment": "Rude staff and slow service",
        },
    )
    assert fb_res.status_code == 200

    # Verify cuisine affinity is NOT reduced by service complaint
    profile = people[0].get("/api/profile").json()
    assert profile["taste_preferences"].get("cuisine:malaysian") == "like"

    # Score an item: lasting taste "H" should not be penalized by service complaint
    cat = ready_catalog()
    malay_item = next(
        item for item in cat.menu_items if "Malaysian" in item.cuisine_tags
    )
    fit = score_item(
        malay_item,
        profile,
        {"craving": ""},
        outlet_id=outlet_id,
        at=datetime.now(timezone.utc),
        observations=people[0].get("/api/learning").json()["observations"],
    )
    assert fit.features["H"].value >= 0.7  # Preserves high lasting taste affinity


def test_value_complaint_affects_only_value_ranking(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "value_learn.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("ValHost", "ValGuest")]
    room, _ = setup_room(people)
    people[0].patch("/api/profile", json={"memory_enabled": True})

    start_date = datetime.now(timezone.utc).replace(
        hour=4, minute=30, second=0, microsecond=0
    ) + timedelta(days=2)

    # Submit 3 meals with consistent value complaints
    for i in range(3):
        meal_time = start_date + timedelta(days=i * 2)
        monkeypatch.setattr(
            "dining.api.now", (lambda mt=meal_time: mt - timedelta(hours=5))
        )
        meal, _ = create_meal(
            people[0],
            room,
            meal_at=meal_time.isoformat(),
            idempotency_key=f"val-meal-{i}",
        )
        for p in people:
            meal = checkin(p, meal["id"], craving="rice", budget=50)

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

        monkeypatch.setattr(
            "dining.api.now", (lambda mt=meal_time: mt + timedelta(hours=3))
        )
        people[0].post(
            f"/api/meals/{meal['id']}/feedback",
            json={
                "visited": True,
                "option_id": opt_id,
                "enjoyment": "did_not_enjoy",
                "influences": ["value"],
                "cost_expectation": "higher",
                "comment": f"Overpriced meal {i}",
            },
        )

    learning = people[0].get("/api/learning").json()
    proposals = learning["proposals"]
    val_prop = next((p for p in proposals if p.get("attribute") == "value"), None)
    assert val_prop is not None
    assert val_prop["status"] == "pending"

    # Accept the proposal
    accept_res = people[0].post(
        f"/api/learning/proposals/{val_prop['id']}", json={"decision": "accept"}
    )
    assert accept_res.status_code == 200

    updated_learning = people[0].get("/api/learning").json()
    accepted = [p for p in updated_learning["proposals"] if p["status"] == "accepted"]
    assert any(p.get("attribute") == "value" for p in accepted)

    # Value preference affects only value ranking ("B"), not lasting taste ("H")
    cat = ready_catalog()
    test_item = cat.menu_items[0]
    fit_with_val = score_item(
        test_item,
        {"taste_preferences": {}},
        {"soft_budget_target": test_item.price.payable_amount_minor / 100 - 1},
        outlet_id=test_item.outlet_id,
        attribute_preferences=[
            {"attribute": "value", "proposed_value": "budget_sensitive"}
        ],
    )
    fit_without_val = score_item(
        test_item,
        {"taste_preferences": {}},
        {"soft_budget_target": test_item.price.payable_amount_minor / 100 - 1},
        outlet_id=test_item.outlet_id,
    )
    assert fit_with_val.features["B"].value < fit_without_val.features["B"].value
    assert fit_with_val.features["H"].value == fit_without_val.features["H"].value

    # Accept the proposal
    accept_res = people[0].post(
        f"/api/learning/proposals/{val_prop['id']}", json={"decision": "accept"}
    )
    assert accept_res.status_code == 200

    updated_learning = people[0].get("/api/learning").json()
    accepted = [p for p in updated_learning["proposals"] if p["status"] == "accepted"]
    assert any(p.get("attribute") == "value" for p in accepted)


def test_nonattendance_creates_no_taste_signal(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "nonattend.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("UserA", "UserB")]
    room, _ = setup_room(people)
    people[0].patch("/api/profile", json={"memory_enabled": True})

    meal, _ = create_meal(people[0], room, meal_at=snapshot()["meal"]["meal_at"])
    for p in people:
        meal = checkin(p, meal["id"], craving="rice", budget=50)

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

    # Did not visit / nonattendance
    people[0].post(
        f"/api/meals/{meal['id']}/feedback",
        json={
            "visited": False,
            "outcome": "did_not_join",
            "option_id": opt_id,
            "comment": "Could not attend",
        },
    )
    learning = people[0].get("/api/learning").json()
    # Nonattendance must create zero observations and zero proposals
    assert len(learning["observations"]) == 0
    assert len(learning["proposals"]) == 0


def test_source_deletion_removes_dependent_attribute_proposal(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "src_del.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("DelA", "DelB")]
    room, _ = setup_room(people)
    people[0].patch("/api/profile", json={"memory_enabled": True})

    start_date = datetime.now(timezone.utc).replace(
        hour=4, minute=30, second=0, microsecond=0
    ) + timedelta(days=2)

    # Submit 3 meals with portion feedback
    for i in range(3):
        meal_time = start_date + timedelta(days=i * 2)
        monkeypatch.setattr(
            "dining.api.now", (lambda mt=meal_time: mt - timedelta(hours=5))
        )
        meal, _ = create_meal(
            people[0],
            room,
            meal_at=meal_time.isoformat(),
            idempotency_key=f"del-meal-{i}",
        )
        for p in people:
            meal = checkin(p, meal["id"], craving="rice", budget=50)

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

        monkeypatch.setattr(
            "dining.api.now", (lambda mt=meal_time: mt + timedelta(hours=3))
        )
        people[0].post(
            f"/api/meals/{meal['id']}/feedback",
            json={
                "visited": True,
                "option_id": opt_id,
                "enjoyment": "enjoyed",
                "attribute_ratings": {"portion": "positive"},
                "influences": ["portion"],
                "comment": f"Large portions at meal {i}",
            },
        )

    learning = people[0].get("/api/learning").json()
    assert len(learning["observations"]) == 3
    proposals = learning["proposals"]
    portion_prop = next((p for p in proposals if p.get("attribute") == "portion"), None)
    assert portion_prop is not None

    # Delete one supporting observation
    obs_id = learning["observations"][0]["id"]
    del_res = people[0].delete(f"/api/learning/observations/{obs_id}")
    assert del_res.status_code == 200

    # Removing supporting observation must remove the dependent proposal
    learning_after = people[0].get("/api/learning").json()
    assert not any(p["id"] == portion_prop["id"] for p in learning_after["proposals"])


def test_user_can_inspect_and_reject_proposed_preference(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "reject_prop.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("RejA", "RejB")]
    room, _ = setup_room(people)
    people[0].patch("/api/profile", json={"memory_enabled": True})

    start_date = datetime.now(timezone.utc).replace(
        hour=4, minute=30, second=0, microsecond=0
    ) + timedelta(days=2)

    for i in range(3):
        meal_time = start_date + timedelta(days=i * 2)
        monkeypatch.setattr(
            "dining.api.now", (lambda mt=meal_time: mt - timedelta(hours=5))
        )
        meal, _ = create_meal(
            people[0],
            room,
            meal_at=meal_time.isoformat(),
            idempotency_key=f"rej-meal-{i}",
        )
        for p in people:
            meal = checkin(p, meal["id"], craving="rice", budget=50)

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

        monkeypatch.setattr(
            "dining.api.now", (lambda mt=meal_time: mt + timedelta(hours=3))
        )
        people[0].post(
            f"/api/meals/{meal['id']}/feedback",
            json={
                "visited": True,
                "option_id": opt_id,
                "enjoyment": "did_not_enjoy",
                "influences": ["quietness"],
                "comment": f"Too noisy at meal {i}",
            },
        )

    learning = people[0].get("/api/learning").json()
    quiet_prop = next(
        (p for p in learning["proposals"] if p.get("attribute") == "quietness"), None
    )
    assert quiet_prop is not None
    assert quiet_prop["status"] == "pending"

    # Reject proposal
    rej_res = people[0].post(
        f"/api/learning/proposals/{quiet_prop['id']}", json={"decision": "reject"}
    )
    assert rej_res.status_code == 200

    learning_after = people[0].get("/api/learning").json()
    rejected_prop = next(
        p for p in learning_after["proposals"] if p["id"] == quiet_prop["id"]
    )
    assert rejected_prop["status"] == "rejected"
    assert not any(
        p["id"] == quiet_prop["id"]
        for p in learning_after.get("attribute_preferences", [])
    )
