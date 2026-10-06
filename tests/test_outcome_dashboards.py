from test_api import checkin, create_meal, make_client, setup_room
from test_recommendation import ready_catalog, snapshot

from dining.outcomes import compute_outcome_metrics
from webapp import create_app


def test_every_outcome_metric_declares_its_denominator(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "outcomes.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("HostA", "GuestB")]
    room, _ = setup_room(people)
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

    with app.state.store.transaction() as db:
        metrics = compute_outcome_metrics(db, min_cohort_size=1)

        # Every rate must explicitly declare numerator, denominator, and rate
        rates = [
            "shortlist_acceptance",
            "veto_frequency",
            "unanimous_agreement",
            "manual_plan_rate",
            "repeat_venue_rate",
            "price_accuracy",
            "route_feasibility",
            "data_error_frequency",
        ]
        for key in rates:
            assert key in metrics
            entry = metrics[key]
            assert "numerator" in entry
            assert "denominator" in entry
            assert "rate" in entry
            if entry["denominator"] > 0:
                assert entry["rate"] == entry["numerator"] / entry["denominator"]
            else:
                assert entry["rate"] is None

        # Time to decision
        assert "time_to_decision" in metrics
        assert "sample_count" in metrics["time_to_decision"]

        # Cuisine diversity
        assert "cuisine_diversity" in metrics
        assert "unique_cuisines" in metrics["cuisine_diversity"]
        assert "total_recommendations" in metrics["cuisine_diversity"]

        # Participant floor distribution
        assert "participant_floor_distribution" in metrics
        assert "bins" in metrics["participant_floor_distribution"]


def test_small_cohort_suppression_prevents_individual_exposure(tmp_path):
    app = create_app(tmp_path / "suppress.sqlite3", demo_mode=True)
    with app.state.store.transaction() as db:
        # Default min_cohort_size=5 should suppress cohort breakdowns when n < 5
        metrics = compute_outcome_metrics(db, min_cohort_size=5)
        assert metrics["cohort_status"] == "suppressed_small_cohort"
        assert metrics["total_meals"] < 5


def test_demo_data_exclusion(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "demo_ex.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("DHost", "DGuest")]
    room, _ = setup_room(people)
    _meal, _ = create_meal(people[0], room, meal_at=snapshot()["meal"]["meal_at"])

    with app.state.store.transaction() as db:
        all_metrics = compute_outcome_metrics(db, min_cohort_size=1, exclude_demo=False)
        assert all_metrics["total_meals"] == 1

        non_demo_metrics = compute_outcome_metrics(
            db, min_cohort_size=1, exclude_demo=True
        )
        # Demo meal should be excluded when configured
        assert non_demo_metrics["total_meals"] == 0


def test_no_private_answer_text_in_outcomes(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
    app = create_app(tmp_path / "priv_out.sqlite3", catalog, demo_mode=True)
    people = [make_client(app, name) for name in ("PHost", "PGuest")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room, meal_at=snapshot()["meal"]["meal_at"])

    PRIVATE_CRAVING = "PRIVATE_SPECIAL_CRAVING_HOT_POT"
    for p in people:
        meal = checkin(p, meal["id"], craving=PRIVATE_CRAVING, budget=50)

    with app.state.store.transaction() as db:
        metrics = compute_outcome_metrics(db, min_cohort_size=1)
        dump = str(metrics)
        assert PRIVATE_CRAVING not in dump
        assert "password" not in dump
        assert "profile" not in dump


def test_restricted_operations_endpoint_serves_outcome_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("DINING_OPERATIONS_TOKEN", "test-operator-outcomes-" * 3)
    app = create_app(tmp_path / "ops_dash.sqlite3", demo_mode=True)
    client = make_client(app, "OpsClient")

    res = client.get(
        "/api/operations/outcomes",
        headers={"X-Operations-Token": "test-operator-outcomes-" * 3},
    )
    assert res.status_code == 200
    data = res.json()
    assert "shortlist_acceptance" in data
    assert "veto_frequency" in data
    assert "unanimous_agreement" in data
    assert "manual_plan_rate" in data
