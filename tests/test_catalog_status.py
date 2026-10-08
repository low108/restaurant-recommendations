from pathlib import Path

from fastapi.testclient import TestClient

from webapp import create_app


def test_catalog_status_exposes_only_aggregate_coverage(tmp_path):
    app = create_app(db_path=tmp_path / "status.sqlite3", demo_mode=True)
    response = TestClient(app).get("/api/catalog/status")
    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == "2"
    assert data["status"] == "demo"
    assert data["outlet_count"] == 3
    assert data["outlets_with_menus"] == 3
    assert data["reviewed_item_count"] == 6
    assert data["retrieval"]["mode"] == "structured"
    assert "no-store" in response.headers["cache-control"]
    assert "Demo Soup Kitchen" not in response.text
    assert "restaurant.example" not in response.text


def test_no_catalog_has_explicit_empty_coverage(tmp_path):
    app = create_app(db_path=tmp_path / "empty.sqlite3")
    data = TestClient(app).get("/api/catalog/status").json()
    assert data["status"] == "empty"
    assert data["item_count"] == 0
    assert data["outlets_with_menus"] == 0
    assert data["retrieval"]["index_available"] is False


def test_v1_catalog_remains_loadable_without_promoting_review(tmp_path):
    import json

    data = json.loads(
        (Path(__file__).resolve().parents[1] / "data/catalog.example.json").read_text()
    )
    data["schema_version"] = "1"
    for item in data["menu_items"]:
        for key in (
            "review_status",
            "review_reasons",
            "meal_role",
            "serves_min",
            "serves_max",
        ):
            item.pop(key, None)
        item["price"].pop("unit", None)
        item["price"].pop("minimum_quantity", None)
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(data))
    app = create_app(db_path=tmp_path / "v1.sqlite3", catalog_path=path, demo_mode=True)
    data = TestClient(app).get("/api/catalog/status").json()
    assert data["reviewed_item_count"] == 0
