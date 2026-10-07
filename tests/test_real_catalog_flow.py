import json
from pathlib import Path

from test_api import generate, make_client, ready_meal

from webapp import create_app


def test_partial_import_flows_through_real_agent_without_publishing_blocked_content(
    tmp_path,
):
    raw = json.loads(
        (Path(__file__).resolve().parents[1] / "data/catalog.example.json").read_text()
    )
    raw.update(synthetic=False, catalog_id="pending-test-import")
    for source in raw["sources"]:
        source.update(kind="official_website", expires_at=None)
        source["rights"].update(
            display="unknown", embed="unknown", basis="Awaiting review"
        )
    for item in raw["menu_items"]:
        item.update(
            review_status="unreviewed",
            meal_role="unknown",
            serves_min=None,
            serves_max=None,
        )
        item["price"].update(channel="unknown", unit="unknown", minimum_quantity=None)
    path = tmp_path / "pending.json"
    path.write_text(json.dumps(raw))
    app = create_app(
        async_generation=False, db_path=tmp_path / "pilot.sqlite3", catalog_path=path
    )
    people = [make_client(app, name) for name in ("ReviewA", "ReviewB")]
    _, meal = ready_meal(people)
    result = generate(people[0], meal)["result"]
    assert result["status"] == "needs_verification"
    assert result["options"] == [] and result["verification"] == []
    assert result["coverage"]["outlet_count"] == 3
    assert result["coverage"]["sources_current_for_display"] == 0
    assert result["agent"]["model_calls"] == 0
    assert "Demo Soup Kitchen" not in json.dumps(result)
    assert "reviewa@example.test" not in json.dumps(result)
    assert "restaurant.example" not in json.dumps(result)
    app.state.store.close()
