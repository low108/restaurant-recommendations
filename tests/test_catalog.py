"""Contract tests are offline and use conspicuously fictional data only."""

import json
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from dining.catalog import Catalog, load_catalog

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "catalog.example.json"


@pytest.fixture
def data():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_demo_requires_explicit_opt_in():
    with pytest.raises(ValueError, match="allow_synthetic"):
        load_catalog(FIXTURE)
    catalog = load_catalog(FIXTURE, allow_synthetic=True)
    assert len(catalog.outlets) == 3
    assert len(catalog.menu_items) == 6
    assert Catalog.model_validate(catalog.model_dump(mode="json")) == catalog
    with pytest.raises(ValidationError):
        catalog.version = "changed"


@pytest.mark.parametrize(
    "table,key",
    [
        ("brands", "brand_id"),
        ("outlets", "outlet_id"),
        ("menu_items", "item_id"),
        ("sources", "source_id"),
    ],
)
def test_rejects_duplicate_identity(data, table, key):
    data[table].append(data[table][0])
    with pytest.raises(ValidationError, match=f"duplicate {key}"):
        Catalog.model_validate(data)


@pytest.mark.parametrize(
    "table,field,value",
    [
        ("outlets", "brand_id", "missing"),
        ("menu_items", "outlet_id", "missing"),
        ("menu_items", "source_ids", ["missing"]),
    ],
)
def test_rejects_orphan_references(data, table, field, value):
    data[table][0][field] = value
    with pytest.raises(ValidationError, match="unknown"):
        Catalog.model_validate(data)


def test_rejects_mixed_real_and_synthetic(data):
    data["synthetic"] = False
    with pytest.raises(ValidationError, match="synthetic"):
        Catalog.model_validate(data)


@pytest.mark.parametrize("value", [True, 18.50, "1850", -1])
def test_money_is_nonnegative_integer_sen(data, value):
    data["menu_items"][0]["price"]["amount_minor"] = value
    with pytest.raises(ValidationError):
        Catalog.model_validate(data)


def test_unknown_charges_cannot_claim_payable_total(data):
    data["menu_items"][0]["price"]["all_mandatory_charges_known"] = False
    with pytest.raises(ValidationError, match="payable total"):
        Catalog.model_validate(data)


def test_unknown_fields_can_be_imported_but_not_published(data):
    price = data["menu_items"][0]["price"]
    price.update(
        mandatory_charges_included=None,
        all_mandatory_charges_known=False,
        payable_amount_minor=None,
    )
    data["sources"][0]["rights"]["display"] = "unknown"
    catalog = Catalog.model_validate(data)
    assert not catalog.evidence_usable(
        catalog.menu_items[0].source_ids,
        at=datetime.fromisoformat("2026-10-06T12:00:00+08:00"),
    )


def test_source_expiry_and_embedding_rights_are_independent(data):
    data["sources"][0]["expires_at"] = "2026-10-10T00:00:00+08:00"
    data["sources"][0]["rights"]["embed"] = "prohibited"
    catalog = Catalog.model_validate(data)
    ids = catalog.menu_items[0].source_ids
    now = datetime.fromisoformat("2026-10-06T12:00:00+08:00")
    assert catalog.evidence_usable(ids, "display", now)
    assert not catalog.evidence_usable(ids, "embed", now)
    assert not catalog.evidence_usable(
        ids, "display", datetime.fromisoformat("2026-10-11T12:00:00+08:00")
    )
    assert not catalog.evidence_usable(("missing",), "display", now)


def test_unknown_expiry_does_not_pass_freshness(data):
    data["sources"][0]["expires_at"] = None
    catalog = Catalog.model_validate(data)
    assert not catalog.evidence_usable(catalog.outlets[0].source_ids)


def test_certification_needs_certificate_and_authority(data):
    data["outlets"][0]["halal"] = {
        "status": "certified",
        "source_ids": ["synthetic-menu-v1"],
    }
    with pytest.raises(ValidationError, match="certificate_id and authority"):
        Catalog.model_validate(data)


def test_timezone_and_opening_intervals_are_validated(data):
    data["outlets"][0]["opening_hours"][0]["opens"] = "27:00"
    with pytest.raises(ValidationError):
        Catalog.model_validate(data)


def test_empty_real_catalog_can_start_without_fake_restaurants():
    catalog = Catalog.model_validate(
        {
            "schema_version": "1",
            "catalog_id": "empty-pilot",
            "version": "1",
            "generated_at": "2026-10-05T00:00:00Z",
            "synthetic": False,
            "brands": [],
            "outlets": [],
            "menu_items": [],
            "sources": [],
        }
    )
    assert not catalog.synthetic
    assert not catalog.outlets


def test_generated_schema_matches_contract():
    schema = json.loads(
        (ROOT / "schemas" / "restaurant-catalog.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert schema == Catalog.model_json_schema()


def test_v1_load_keeps_new_facts_unknown(data):
    data["schema_version"] = "1"
    for item in data["menu_items"]:
        for key in (
            "meal_role",
            "serves_min",
            "serves_max",
            "review_status",
            "review_reasons",
        ):
            item.pop(key, None)
        item["price"].pop("unit", None)
        item["price"].pop("minimum_quantity", None)
    item = Catalog.model_validate(data).menu_items[0]
    assert item.meal_role == "unknown"
    assert item.serves_min is None and item.serves_max is None
    assert item.review_status == "unreviewed"
    assert item.review_reasons == ()
    assert item.price.unit == "unknown"
    assert item.price.minimum_quantity is None


@pytest.mark.parametrize("value", [0, -1, True, "2", 1.5])
def test_minimum_quantity_is_a_positive_integer(data, value):
    data["menu_items"][0]["price"]["minimum_quantity"] = value
    with pytest.raises(ValidationError):
        Catalog.model_validate(data)


def test_unknown_channel_and_unit_can_be_stored_for_review(data):
    data["menu_items"][0]["price"].update(
        channel="unknown", unit="unknown", minimum_quantity=None
    )
    item = Catalog.model_validate(data).menu_items[0]
    assert item.price.channel == "unknown"
    assert item.price.unit == "unknown"


@pytest.mark.parametrize(
    "minimum,maximum", [(0, 1), (2, 1), (True, 2), ("1", 2), (1, None), (None, 2)]
)
def test_servings_require_a_complete_ordered_positive_range(data, minimum, maximum):
    data["menu_items"][0].update(serves_min=minimum, serves_max=maximum)
    with pytest.raises(ValidationError):
        Catalog.model_validate(data)


def test_unit_total_does_not_silently_multiply_minimum_order(data):
    data["menu_items"][0]["price"].update(
        amount_minor=1300, payable_amount_minor=1300, unit="piece", minimum_quantity=2
    )
    price = Catalog.model_validate(data).menu_items[0].price
    assert price.payable_amount_minor == 1300
    assert price.minimum_quantity == 2
