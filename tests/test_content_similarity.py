import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dining.catalog.models import load_catalog
from dining.recommendation.content_similarity import (
    POLICY,
    FeatureSpace,
    cosine,
    lasting_vector,
    score_item_content,
    today_vector,
)
from dining.recommendation.engine import Recommender

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(ROOT / "data/catalog.example.json", allow_synthetic=True)


@pytest.fixture(scope="module")
def space(catalog):
    return FeatureSpace(catalog)


def item(catalog, item_id):
    return next(i for i in catalog.menu_items if i.item_id == item_id)


def test_cosine_basics():
    assert cosine({"a": 1.0}, {"a": 3.0}) == pytest.approx(1.0)
    assert cosine({"a": 1.0}, {"b": 1.0}) == 0.0
    assert cosine({}, {"a": 1.0}) == 0.0
    assert cosine({"a": 1.0, "b": 1.0}, {"a": 1.0}) == pytest.approx(1 / math.sqrt(2))


def test_idf_makes_rare_features_weigh_more(space):
    common = max(space.idf, key=lambda f: -space.idf[f])
    rare = max(space.idf, key=lambda f: space.idf[f])
    assert space.idf[rare] >= space.idf[common]


def test_matching_dish_scores_above_mismatch(catalog, space):
    response = {"craving": "soup"}
    soup = score_item_content(
        item(catalog, "demo-item-soup-1"),
        {},
        response,
        space=space,
        catalog_items={},
        outlet_id="x",
        at=datetime.now(timezone.utc),
    )
    rice = score_item_content(
        item(catalog, "demo-item-spice-2"),
        {},
        response,
        space=space,
        catalog_items={},
        outlet_id="x",
        at=datetime.now(timezone.utc),
    )
    assert soup.features["C"].value > rice.features["C"].value >= 0.2
    assert soup.features["C"].coverage == 1


def test_anything_is_neutral_and_answered(catalog, space):
    fit = score_item_content(
        item(catalog, "demo-item-soup-1"),
        {},
        {"craving": "anything"},
        space=space,
        catalog_items={},
        outlet_id="x",
        at=datetime.now(timezone.utc),
    )
    assert fit.features["C"].value == 0.5 and fit.features["C"].coverage == 1
    assert today_vector(space, {"craving": "anything"}) == {}


def test_negated_craving_is_not_a_wish(space):
    assert not any(
        k.endswith(("noodle", "noodles"))
        for k in today_vector(space, {"craving": "no noodles"})
    )


def test_dislike_pulls_usual_taste_below_neutral(catalog, space):
    profile = {"taste_preferences": {"cuisine:indian": "dislike"}}
    fit = score_item_content(
        item(catalog, "demo-item-spice-2"),
        profile,
        {},
        space=space,
        catalog_items={},
        outlet_id="x",
        at=datetime.now(timezone.utc),
    )
    assert fit.features["H"].value < 0.5


def test_history_needs_consent_and_a_specific_dish(catalog, space):
    items = {i.item_id: i for i in catalog.menu_items}
    now = datetime.now(timezone.utc)
    visit = {
        "item_id": "demo-item-noodle-1",
        "source": "confirmed_visit",
        "enjoyment": "enjoyed",
        "created_at": (now - timedelta(days=1)).isoformat(),
    }
    assert lasting_vector(space, items, {"memory_enabled": False}, [visit], now) == {}
    learned = lasting_vector(space, items, {"memory_enabled": True}, [visit], now)
    assert learned and all(v > 0 for v in learned.values())
    disliked = dict(visit, enjoyment="did_not_enjoy")
    assert all(
        v < 0
        for v in lasting_vector(
            space, items, {"memory_enabled": True}, [disliked], now
        ).values()
    )


def test_default_policy_is_unchanged_and_content_policy_is_opt_in(catalog):
    assert Recommender(catalog).scoring_policy == "prd-fit-v1"
    assert Recommender(catalog, scoring_policy=POLICY).scoring_policy == POLICY
    with pytest.raises(ValueError):
        Recommender(catalog, scoring_policy="unknown")
