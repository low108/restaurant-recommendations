from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dining.catalog import Catalog, load_catalog
from dining.recommendation import Recommender, _open_for


def ready_catalog():
    original = load_catalog(
        Path(__file__).resolve().parents[1] / "data/catalog.example.json",
        allow_synthetic=True,
    ).model_dump(mode="json")
    for outlet in original["outlets"]:
        outlet["holiday_exceptions_known"] = True
        outlet["opening_exceptions_coverage"] = {
            "starts_on": (datetime.now(timezone.utc) - timedelta(days=30))
            .date()
            .isoformat(),
            "ends_on": (datetime.now(timezone.utc) + timedelta(days=30))
            .date()
            .isoformat(),
            "source_ids": outlet["source_ids"],
        }
        for interval in outlet["opening_hours"]:
            interval.update(
                last_order=interval["closes"],
                last_order_next_day=interval.get("closes_next_day", False),
                last_order_source_ids=outlet["source_ids"],
            )
    for source in original["sources"]:
        source["observed_at"] = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        source["expires_at"] = (
            datetime.now(timezone.utc) + timedelta(days=14)
        ).isoformat()
    return Catalog.model_validate(original)


def snapshot():
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    at = tomorrow.replace(hour=4, minute=0, second=0, microsecond=0)
    profile = {
        "allergy_status": "none",
        "halal_policy": "none",
        "requirements_reviewed": True,
        "dietary_requirements": [],
    }
    response = {"craving": "light soup", "budget": 50, "requirements_confirmed": True}
    return {
        "revision": 1,
        "meal": {
            "id": "test",
            "meal_at": at.isoformat(),
            "latitude": 3.12,
            "longitude": 101.62,
            "radius_km": 10,
        },
        "participants": [
            {"profile": deepcopy(profile), "response": deepcopy(response)}
            for _ in range(2)
        ],
    }


def test_complete_outlet_pool_and_private_projection():
    result = Recommender(ready_catalog())(snapshot())
    assert result["examined_outlets"] == 3
    assert result["status"] == "shortlisted"
    assert len({option["outlet_id"] for option in result["options"]}) == len(
        result["options"]
    )
    assert "participants" not in result and "profile" not in str(result)


def test_duplicate_menu_does_not_change_ranking():
    catalog = ready_catalog()
    before = Recommender(catalog)(snapshot())
    raw = catalog.model_dump(mode="json")
    item = raw["menu_items"][0]
    raw["menu_items"] += [dict(item, item_id=f"copy-{number}") for number in range(100)]
    after = Recommender(Catalog.model_validate(raw))(snapshot())
    assert [o["outlet_id"] for o in before["options"]] == [
        o["outlet_id"] for o in after["options"]
    ]
    assert [o["menu_items"] for o in before["options"]] == [
        o["menu_items"] for o in after["options"]
    ]


def test_irrelevant_menu_does_not_increase_rank():
    catalog = ready_catalog()
    raw = catalog.model_dump(mode="json")
    item = raw["menu_items"][0]
    raw["menu_items"] += [
        dict(
            item,
            item_id=f"new-{number}",
            name=f"Unrelated cake {number}",
            description="Sweet dessert",
            cuisine_tags=[],
            attributes=[],
        )
        for number in range(50)
    ]
    a = Recommender(catalog)(snapshot())
    b = Recommender(Catalog.model_validate(raw))(snapshot())
    assert [o["outlet_id"] for o in a["options"]] == [
        o["outlet_id"] for o in b["options"]
    ]


def test_no_allergy_guarantee_from_scraped_menu():
    state = snapshot()
    state["participants"][0]["profile"]["allergy_status"] = "declared"
    result = Recommender(ready_catalog())(state)
    assert result["status"] == "needs_verification"
    assert not result["options"]


def test_unknown_not_pass():
    state = snapshot()
    state["participants"][0]["profile"]["halal_policy"] = "unknown"
    assert Recommender(ready_catalog())(state)["status"] == "needs_verification"


def test_missing_charges_and_budget_cannot_pass():
    raw = ready_catalog().model_dump(mode="json")
    for item in raw["menu_items"]:
        item["price"] = None
    assert not Recommender(Catalog.model_validate(raw))(snapshot())["options"]
    state = snapshot()
    state["participants"][0]["response"]["budget"] = 0.01
    assert not Recommender(ready_catalog())(state)["options"]


def test_a08_same_dish_must_satisfy_diet_and_complete_price():
    raw = ready_catalog().model_dump(mode="json")
    outlet = raw["outlets"][0]
    raw["outlets"] = [outlet]
    vegetarian = deepcopy(raw["menu_items"][0])
    vegetarian.update(
        item_id="suitable-but-over-budget",
        name="Vegetarian noodle soup",
        description="Vegetable noodle soup",
        dietary_claims=["vegetarian"],
        ingredients=["noodles", "vegetables"],
        attributes=["light", "soup"],
    )
    vegetarian["price"].update(amount_minor=3000, payable_amount_minor=3000)
    chicken = deepcopy(vegetarian)
    chicken.update(
        item_id="cheap-but-unsuitable",
        name="Chicken noodle soup",
        description="Chicken noodle soup",
        ingredients=["chicken", "noodles"],
        dietary_claims=[],
    )
    chicken["price"].update(amount_minor=1000, payable_amount_minor=1000)
    raw["menu_items"] = [vegetarian, chicken]
    state = snapshot()
    for person in state["participants"]:
        person["profile"]["dietary_requirements"] = ["vegetarian"]
        person["response"]["budget"] = 15
    # Outlet has a vegetarian dish AND a cheap dish, but no dish that meets both.
    blocked = Recommender(Catalog.model_validate(raw))(state)
    assert not blocked["options"]

    vegetarian["price"].update(amount_minor=1400, payable_amount_minor=1400)
    suitable = Recommender(Catalog.model_validate(raw))(state)
    assert len(suitable["options"]) == 1
    assert suitable["options"][0]["menu_items"] == [
        {
            "id": vegetarian["item_id"],
            "name": vegetarian["name"],
            "price_minor": 1400,
        }
    ]
    assert suitable["options"][0]["price_range"] == {
        "min_minor": 1400,
        "max_minor": 1400,
    }


def test_expired_source_not_published():
    raw = ready_catalog().model_dump(mode="json")
    for source in raw["sources"]:
        source["observed_at"] = (
            datetime.now(timezone.utc) - timedelta(days=10)
        ).isoformat()
        source["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
    result = Recommender(Catalog.model_validate(raw))(snapshot())
    assert not result["options"] and not result["verification"]


def test_overnight_hours():
    raw = ready_catalog().model_dump(mode="json")
    outlet = raw["outlets"][0]
    outlet["opening_hours"] = [
        {
            "weekday": 0,
            "opens": "20:00",
            "closes": "02:00",
            "closes_next_day": True,
            "last_order": "01:00",
            "last_order_next_day": True,
            "last_order_source_ids": outlet["source_ids"],
        }
    ]
    outlet["opening_exceptions_coverage"].update(
        starts_on="2026-10-01", ends_on="2026-10-31"
    )
    parsed = Catalog.model_validate(raw).outlets[0]
    assert _open_for(parsed, datetime.fromisoformat("2026-10-06T00:30:00+08:00"), 60)
    assert not _open_for(
        parsed, datetime.fromisoformat("2026-10-06T01:30:00+08:00"), 60
    )


def test_delivery_copies_do_not_hide_dine_in_items():
    original = ready_catalog()
    raw = original.model_dump(mode="json")
    delivery = deepcopy(raw["menu_items"])
    for item in delivery:
        item["item_id"] += "-delivery"
        item["price"]["channel"] = "delivery"
    raw["menu_items"] = delivery + raw["menu_items"]
    updated = Recommender(Catalog.model_validate(raw))(snapshot())["options"]
    prior = Recommender(original)(snapshot())["options"]
    assert [o["outlet_id"] for o in updated] == [o["outlet_id"] for o in prior]
    assert [o["menu_items"] for o in updated] == [o["menu_items"] for o in prior]


def test_free_text_avoid_is_not_guessed_safe():
    state = snapshot()
    state["participants"][0]["response"]["avoid"] = ["peanut"]
    result = Recommender(ready_catalog())(state)
    assert result["status"] == "needs_verification" and not result["options"]
    assert "peanut" not in str(result)


def test_future_menu_source_not_accepted_today():
    raw = ready_catalog().model_dump(mode="json")
    source = dict(
        raw["sources"][0],
        source_id="future-menu",
        observed_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )
    raw["sources"].append(source)
    for item in raw["menu_items"]:
        item["source_ids"] = ["future-menu"]
    assert not Recommender(Catalog.model_validate(raw))(snapshot())["options"]


def test_verification_does_not_disclose_private_requirement_type():
    state = snapshot()
    state["participants"][0]["profile"]["halal_policy"] = "certified"
    result = Recommender(ready_catalog())(state)
    assert result["status"] == "needs_verification"
    shared_explanation = str((result["explanation"], result["verification"]))
    assert "halal" not in shared_explanation.lower()
    assert "certif" not in shared_explanation.lower()
    assert result["coverage"] == Recommender(ready_catalog())(snapshot())["coverage"]


@pytest.mark.parametrize("role", ["beverage", "dessert", "side", "add_on", "unknown"])
def test_non_meal_items_cannot_become_affordable_lunch(role):
    raw = ready_catalog().model_dump(mode="json")
    for item in raw["menu_items"]:
        item.update(meal_role=role, name="Bottled water", description="Light soup")
        item["price"].update(amount_minor=100, payable_amount_minor=100)
    result = Recommender(Catalog.model_validate(raw))(snapshot())
    assert result["status"] == "needs_verification"
    assert not result["options"]


@pytest.mark.parametrize("status", ["unreviewed", "quarantined"])
def test_unreviewed_and_quarantined_dishes_cannot_be_shortlisted(status):
    raw = ready_catalog().model_dump(mode="json")
    for item in raw["menu_items"]:
        item.update(review_status=status, review_reasons=["Internal review detail"])
    result = Recommender(Catalog.model_validate(raw))(snapshot())
    assert result["status"] == "needs_verification"
    assert not result["options"]
    assert "Internal review detail" not in str(result)


@pytest.mark.parametrize(
    ("unit", "minimum_quantity"),
    [
        ("piece", 1),
        ("portion", 2),
        ("person", 2),
        ("unknown", 1),
        ("portion", None),
        ("pot", 1),
        ("weight_100g", 1),
        ("weight_kg", 1),
    ],
)
def test_unallocated_or_minimum_order_prices_do_not_fit_individual_budget(
    unit, minimum_quantity
):
    raw = ready_catalog().model_dump(mode="json")
    for item in raw["menu_items"]:
        item["price"].update(unit=unit, minimum_quantity=minimum_quantity)
    result = Recommender(Catalog.model_validate(raw))(snapshot())
    assert not result["options"]
    assert result["status"] == "needs_verification"


@pytest.mark.parametrize("servings", [(None, None), (1, 2), (2, 2)])
def test_shared_or_unknown_serving_size_is_not_divided_between_people(servings):
    raw = ready_catalog().model_dump(mode="json")
    for item in raw["menu_items"]:
        item.update(serves_min=servings[0], serves_max=servings[1])
    result = Recommender(Catalog.model_validate(raw))(snapshot())
    assert not result["options"]
    assert result["status"] == "needs_verification"


@pytest.mark.parametrize("unit", ["portion", "person", "set"])
def test_reviewed_single_person_meal_has_usable_price_basis(unit):
    raw = ready_catalog().model_dump(mode="json")
    for item in raw["menu_items"]:
        item.update(
            meal_role="set", serves_min=1, serves_max=1, review_status="reviewed"
        )
        item["price"].update(unit=unit, minimum_quantity=1)
    assert (
        Recommender(Catalog.model_validate(raw))(snapshot())["status"] == "shortlisted"
    )


def test_reviewed_flag_does_not_approve_contradictory_dietary_claim():
    raw = ready_catalog().model_dump(mode="json")
    for item in raw["menu_items"]:
        item.update(
            name="Vegetarian mushroom pasta",
            description="Mushrooms and cream",
            variant="WITH BEEF MEATBALLS",
            dietary_claims=["vegetarian"],
            review_status="reviewed",
        )
    state = snapshot()
    for person in state["participants"]:
        person["profile"]["dietary_requirements"] = ["vegetarian"]
    result = Recommender(Catalog.model_validate(raw))(state)
    assert not result["options"]
    assert result["status"] == "needs_verification"


def test_unknown_source_rights_and_missing_location_explain_incomplete_coverage_privately():
    raw = ready_catalog().model_dump(mode="json")
    for source in raw["sources"]:
        source["rights"]["display"] = "unknown"
        source["evidence_text"] = "Restricted evidence sentinel"
    for outlet in raw["outlets"]:
        outlet["name"] = "Restricted outlet sentinel"
    raw["outlets"][0].update(latitude=None, longitude=None)
    result = Recommender(Catalog.model_validate(raw))(snapshot())
    assert result["status"] == "needs_verification"
    assert not result["options"] and not result["verification"]
    assert "Restricted" not in str(result)
    assert result["coverage"]


def test_uncertain_dish_does_not_hide_another_reviewed_main_meal():
    raw = ready_catalog().model_dump(mode="json")
    original = Recommender(Catalog.model_validate(raw))(snapshot())
    extra = deepcopy(raw["menu_items"][0])
    extra.update(
        item_id="unreviewed-extra",
        name="Unreviewed new dish",
        review_status="unreviewed",
    )
    raw["menu_items"].append(extra)
    result = Recommender(Catalog.model_validate(raw))(snapshot())
    assert [o["outlet_id"] for o in result["options"]] == [
        o["outlet_id"] for o in original["options"]
    ]


def test_unknown_price_channel_is_not_assumed_dine_in():
    raw = ready_catalog().model_dump(mode="json")
    for item in raw["menu_items"]:
        item["price"]["channel"] = "unknown"
    result = Recommender(Catalog.model_validate(raw))(snapshot())
    assert not result["options"]
    assert result["status"] == "needs_verification"


def test_low_individual_fit_blocks_majority_favourite_without_revealing_person():
    raw = ready_catalog().model_dump(mode="json")
    for item in raw["menu_items"]:
        item["attributes"] = ["rice", "mild", "hearty"]
        item["cuisine_tags"] = ["Thai"]
    state = snapshot()
    for person in state["participants"]:
        person["response"].update(
            craving="rice", cuisines=["Thai"], spice="mild", appetite="hearty"
        )
    state["participants"][0]["response"].update(
        craving="soup", cuisines=["Indian"], spice="hot", appetite="light"
    )
    result = Recommender(Catalog.model_validate(raw))(state)
    assert not result["options"]
    assert result["status"] == "needs_input"
    assert "preference" in result["explanation"].lower()
    assert "participants" not in result and "individual_fit" not in str(result)


def test_ranking_provenance_and_low_coverage_are_shared_without_private_features():
    state = snapshot()
    for person in state["participants"]:
        person["response"]["craving"] = "unrecognized private request sentinel"
    result = Recommender(ready_catalog())(state)
    assert result["policy_version"] == "prd-fit-v1"
    assert result["feature_version"] == "prd-features-v2"
    assert result["ontology_version"] == "dining-tags-v1"
    assert result["options"]
    assert all(option["fit_confidence"] == "limited" for option in result["options"])
    assert "unrecognized private request sentinel" not in str(result)
    assert not any(
        key in str(result)
        for key in ("individual_fit", "_score", "route_estimates", "taste_preferences")
    )
    assert all("features" not in option for option in result["options"])
