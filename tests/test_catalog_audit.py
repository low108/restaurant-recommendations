"""Offline ingestion review: flags may block claims but never manufacture facts."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from dining.catalog.audit import audit_catalog, public_catalog_status, upgrade_catalog
from dining.catalog.cli import main
from dining.catalog.models import Catalog

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.fromisoformat("2026-10-06T12:00:00+08:00")


@pytest.fixture
def data():
    return json.loads(
        (ROOT / "data" / "catalog.example.json").read_text(encoding="utf-8")
    )


def test_upgrade_preserves_collected_claims_and_unknowns(data):
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
    data["sources"][0]["expires_at"] = None
    data["sources"][0]["rights"]["embed"] = "unknown"
    before = Catalog.model_validate(data)
    after = upgrade_catalog(before)
    assert after.schema_version == "2"
    assert after.catalog_id == before.catalog_id and after.version == before.version
    assert after.sources == before.sources and after.outlets == before.outlets
    assert after.menu_items == before.menu_items
    assert all(item.review_status == "unreviewed" for item in after.menu_items)
    assert all(item.price.unit == "unknown" for item in after.menu_items)
    assert upgrade_catalog(after) == after


@pytest.mark.parametrize(
    "claim,variant",
    [("vegetarian", "WITH BEEF MEATBALLS"), ("vegan", "3 PCS CHICKEN MEATBALLS")],
)
def test_contradictory_variants_are_quarantined_not_rewritten(data, claim, variant):
    data["menu_items"][0].update(
        name="Mushroom pasta",
        description="Mushroom sauce",
        ingredients=[],
        variant=variant,
        dietary_claims=[claim],
        review_status="reviewed",
    )
    before = Catalog.model_validate(data)
    after = upgrade_catalog(before)
    item = after.menu_items[0]
    assert item.review_status == "quarantined"
    assert "dietary_claim_conflicts_with_variant" in item.review_reasons
    assert item.dietary_claims == (claim,)
    assert item.variant == variant
    assert item.price == before.menu_items[0].price


def test_vegetarian_chicken_description_is_queued_for_review(data):
    data["menu_items"][0].update(
        name="Minestrone (Vegetarian)",
        variant="CLASSIC",
        description="Vegetables in light chicken pomodoro reduction.",
        ingredients=[],
        dietary_claims=["vegetarian"],
    )
    item = upgrade_catalog(Catalog.model_validate(data)).menu_items[0]
    assert item.review_status == "quarantined"
    assert "dietary_claim_conflicts_with_text" in item.review_reasons


def test_plant_based_meat_words_do_not_imply_animal_ingredients(data):
    data["menu_items"][0].update(
        name="Plant-based Meatball Pasta",
        variant="3 PCS PLANT-BASED MEATBALLS",
        description="Omni meatballs in tomato sauce.",
        ingredients=[],
        dietary_claims=["vegan"],
        review_status="unreviewed",
    )
    item = upgrade_catalog(Catalog.model_validate(data)).menu_items[0]
    assert item.review_status == "unreviewed"
    assert item.review_reasons == ()


def test_public_status_is_aggregate_only_even_for_quarantined_content(data):
    data["menu_items"][0].update(
        name="SECRET DISH", variant="BEEF", dietary_claims=["vegetarian"]
    )
    catalog = upgrade_catalog(Catalog.model_validate(data))
    status = public_catalog_status(catalog, at=NOW)
    assert status["item_count"] == 6 and status["outlet_count"] == 3
    assert status["quarantined_item_count"] == 1
    assert status["status"] == "demo"
    rendered = json.dumps(status)
    for record in (*catalog.menu_items, *catalog.outlets, *catalog.sources):
        for key in (
            "name",
            "url",
            "evidence_text",
            "description",
            "item_id",
            "outlet_id",
            "source_id",
        ):
            value = getattr(record, key, None)
            if value:
                assert value not in rendered
    assert all(set(issue) == {"code", "message", "count"} for issue in status["issues"])


def test_real_status_requires_review_and_current_evidence(data):
    data["synthetic"] = False
    for source in data["sources"]:
        source["kind"] = "official_website"
    catalog = Catalog.model_validate(data)
    assert public_catalog_status(catalog, at=NOW)["status"] == "reviewed"
    data["sources"][0]["rights"]["display"] = "unknown"
    status = public_catalog_status(Catalog.model_validate(data), at=NOW)
    assert status["status"] == "review_required"
    assert status["sources_current_for_display"] == 0
    assert status["sources_current_for_embed"] == 1


def test_operator_report_contains_actionable_ids_without_changing_catalog(data):
    data["menu_items"][0]["review_status"] = "unreviewed"
    catalog = Catalog.model_validate(data)
    report = audit_catalog(catalog, at=NOW)
    assert report["summary"] == public_catalog_status(catalog, at=NOW)
    assert any(
        row["item_id"] == catalog.menu_items[0].item_id
        for row in report["item_reviews"]
    )
    assert catalog.menu_items[0].review_status == "unreviewed"


def test_cli_upgrade_stages_separately_and_writes_audit(
    data, tmp_path, monkeypatch, capsys
):
    data["schema_version"] = "1"
    data["menu_items"][0].update(
        name="Mushroom pasta", variant="BEEF", dietary_claims=["vegetarian"]
    )
    source = tmp_path / "source.json"
    staged = tmp_path / "catalog.v2.json"
    report = tmp_path / "audit.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    before = source.read_bytes()
    monkeypatch.setattr(
        "sys.argv",
        [
            "catalog_cli",
            str(source),
            "--allow-synthetic",
            "--upgrade",
            "--install",
            str(staged),
            "--report",
            str(report),
        ],
    )
    main()
    assert source.read_bytes() == before
    installed = Catalog.model_validate_json(staged.read_text(encoding="utf-8"))
    assert installed.schema_version == "2"
    assert installed.menu_items[0].review_status == "quarantined"
    assert (
        json.loads(report.read_text(encoding="utf-8"))["summary"][
            "quarantined_item_count"
        ]
        == 1
    )
    assert "does not approve" in capsys.readouterr().out


def test_cli_report_cannot_overwrite_source(data, tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    before = source.read_bytes()
    monkeypatch.setattr(
        "sys.argv",
        ["catalog_cli", str(source), "--allow-synthetic", "--report", str(source)],
    )
    with pytest.raises(SystemExit):
        main()
    assert source.read_bytes() == before
