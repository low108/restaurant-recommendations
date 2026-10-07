"""PRD scoring examples and missingness rules, independent of a language model."""

from datetime import datetime, timedelta, timezone

import pytest
from test_recommendation import ready_catalog

from dining.ranking import (
    Feature,
    base_score,
    group_fit,
    individual_fit,
    novelty_feature,
    rank_diverse,
    score_item,
    venue_affinity,
)

NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)


def test_a23_exact_prd_worked_examples():
    assert group_fit([0.75, 0.75, 0.70]) == pytest.approx(0.7200)
    assert group_fit([0.80, 0.60, 0.65]) == pytest.approx(0.6500)
    assert base_score([0.75, 0.75, 0.70], quality=0.5, novelty=0.5) == pytest.approx(
        0.687
    )
    assert base_score([0.80, 0.60, 0.65], quality=0.5, novelty=0.5) == pytest.approx(
        0.6275
    )


def test_individual_weights_match_prd():
    features = {
        "C": Feature(1, 1),
        "H": Feature(0, 1),
        "T": Feature(0.5, 1),
        "B": Feature(1, 1),
        "O": Feature(0, 1),
    }
    assert individual_fit(features) == pytest.approx(0.625)


def test_missing_features_are_neutral_and_not_observed():
    item = ready_catalog().menu_items[0]
    score = score_item(item, {}, {}, outlet_id=item.outlet_id, at=NOW)
    assert score.fit == pytest.approx(0.5)
    assert score.coverage == 0
    assert all(
        feature.value == 0.5 and feature.coverage == 0
        for feature in score.features.values()
    )


def test_anything_is_explicit_neutral_not_absent_data():
    item = ready_catalog().menu_items[0]
    score = score_item(
        item, {}, {"craving": "anything"}, outlet_id=item.outlet_id, at=NOW
    )
    assert score.features["C"] == Feature(0.5, 1)
    assert score.fit == pytest.approx(0.5)


def test_synonyms_do_not_add_votes_and_unknown_prose_is_not_a_match():
    item = ready_catalog().menu_items[0]
    a = score_item(item, {}, {"craving": "soup"}, outlet_id=item.outlet_id, at=NOW)
    b = score_item(
        item, {}, {"craving": "soup sup soup soupy"}, outlet_id=item.outlet_id, at=NOW
    )
    unknown = score_item(
        item,
        {},
        {"craving": "surprise me with something magical"},
        outlet_id=item.outlet_id,
        at=NOW,
    )
    assert a.features["C"] == b.features["C"] == Feature(1, 1)
    assert unknown.features["C"] == Feature(0.5, 0)


def test_raw_dish_name_does_not_manufacture_curated_attribute_match():
    item = (
        ready_catalog()
        .menu_items[0]
        .model_copy(update={"name": "Smoky spicy special", "attributes": ()})
    )
    score = score_item(item, {}, {"craving": "spicy"}, outlet_id=item.outlet_id, at=NOW)
    assert score.features["C"] == Feature(0.5, 0)


def test_current_cuisine_answer_overrides_historical_cuisine():
    item = ready_catalog().menu_items[0]
    a = score_item(
        item,
        {"cuisines": ["Malaysian"]},
        {"cuisines": ["Thai"]},
        outlet_id=item.outlet_id,
        at=NOW,
    )
    b = score_item(
        item,
        {"cuisines": ["Thai"]},
        {"cuisines": ["Thai"]},
        outlet_id=item.outlet_id,
        at=NOW,
    )
    assert a.features["H"] == b.features["H"] == Feature(0.5, 0)
    assert a.fit == b.fit


def test_spending_target_and_firm_cap_are_separate():
    item = ready_catalog().menu_items[0]
    price = item.price.payable_amount_minor / 100
    exact = score_item(
        item,
        {},
        {"soft_budget_target": price, "budget": price},
        outlet_id=item.outlet_id,
        at=NOW,
    )
    between = score_item(
        item,
        {},
        {"soft_budget_target": price - 5, "budget": price + 5},
        outlet_id=item.outlet_id,
        at=NOW,
    )
    uncapped = score_item(
        item, {}, {"soft_budget_target": price / 2}, outlet_id=item.outlet_id, at=NOW
    )
    assert exact.features["B"] == Feature(1, 1)
    assert between.features["B"] == Feature(0.5, 1)
    assert uncapped.features["B"] == Feature(0.5, 1)


def test_route_requires_current_attributable_evidence():
    item = ready_catalog().menu_items[0]
    answer = {"comfortable_travel_minutes": 30}
    route = {
        "eta_minutes": 10,
        "source_id": "route-provider",
        "observed_at": (NOW - timedelta(minutes=5)).isoformat(),
        "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
    }
    valid = score_item(item, {}, answer, outlet_id=item.outlet_id, at=NOW, route=route)
    expired = score_item(
        item,
        {},
        answer,
        outlet_id=item.outlet_id,
        at=NOW,
        route={**route, "expires_at": (NOW - timedelta(seconds=1)).isoformat()},
    )
    unproven = score_item(
        item, {}, answer, outlet_id=item.outlet_id, at=NOW, route={"eta_minutes": 10}
    )
    assert valid.features["T"].value == pytest.approx(2 / 3)
    assert valid.features["T"].coverage == 1
    assert expired.features["T"] == unproven.features["T"] == Feature(0.5, 0)


def test_occasion_averages_unknown_requested_dimensions_neutrally():
    item = ready_catalog().menu_items[0].model_copy(update={"attributes": ("quiet",)})
    score = score_item(
        item,
        {},
        {"occasion_features": ["quiet", "indoor", "quiet"]},
        outlet_id=item.outlet_id,
        at=NOW,
    )
    assert score.features["O"] == Feature(0.75, 0.5)


def test_venue_affinity_uses_actual_consented_visits_and_90_day_decay():
    event = {
        "outlet_id": "venue",
        "source": "confirmed_visit",
        "enjoyment": "enjoyed",
        "created_at": NOW.isoformat(),
    }
    assert venue_affinity([event], "venue", at=NOW) == Feature(2 / 3, 1)
    old = {**event, "created_at": (NOW - timedelta(days=90)).isoformat()}
    assert venue_affinity([old], "venue", at=NOW) == Feature(0.6, 1)
    assert venue_affinity(
        [{**event, "source": "selection"}], "venue", at=NOW
    ) == Feature(0.5, 0)
    assert venue_affinity(
        [{**event, "created_at": (NOW + timedelta(days=1)).isoformat()}],
        "venue",
        at=NOW,
    ) == Feature(0.5, 0)
    item = ready_catalog().menu_items[0]
    event["outlet_id"] = item.outlet_id
    disabled = score_item(
        item,
        {"memory_enabled": False},
        {},
        outlet_id=item.outlet_id,
        at=NOW,
        observations=[event],
    )
    assert disabled.features["H"] == Feature(0.5, 0)


def candidate(identity, score, cuisine, brand=None):
    return {
        "id": identity,
        "outlet_id": identity,
        "brand_id": brand or identity,
        "_score": score,
        "cuisines": [cuisine],
        "distance_km": 1,
    }


def test_cuisine_diversity_only_within_point_one_of_next_best():
    rows = [
        candidate("a", 0.90, "Thai"),
        candidate("b", 0.85, "Thai"),
        candidate("c", 0.77, "Malay"),
        candidate("d", 0.60, "Indian"),
    ]
    assert [row["id"] for row in rank_diverse(rows)] == ["a", "c", "b"]


def test_diversity_does_not_promote_a_far_worse_option():
    rows = [
        candidate("a", 0.90, "Thai"),
        candidate("b", 0.85, "Thai"),
        candidate("c", 0.74, "Malay"),
    ]
    assert [row["id"] for row in rank_diverse(rows)] == ["a", "b", "c"]


def test_same_brand_without_meaningful_distance_benefit_is_deduplicated():
    rows = [
        candidate("a", 0.90, "Thai", "one-brand"),
        candidate("b", 0.89, "Thai", "one-brand"),
        candidate("c", 0.80, "Malay"),
    ]
    assert [row["id"] for row in rank_diverse(rows)] == ["a", "c"]


def test_negative_free_text_is_not_inverted_into_a_positive_craving():
    item = ready_catalog().menu_items[0]
    score = score_item(
        item, {}, {"craving": "I do not want soup"}, outlet_id=item.outlet_id, at=NOW
    )
    assert score.features["C"] == Feature(0.5, 0)


def test_removed_observation_recomputes_affinity_without_cached_influence():
    event = {
        "outlet_id": "venue",
        "source": "confirmed_visit",
        "enjoyment": "did_not_enjoy",
        "created_at": NOW.isoformat(),
    }
    assert venue_affinity([event], "venue", at=NOW).value < 0.5
    assert venue_affinity([], "venue", at=NOW) == Feature(0.5, 0)


def test_confirmed_venue_preference_overrides_learned_venue_inference():
    item = ready_catalog().menu_items[0]
    event = {
        "outlet_id": item.outlet_id,
        "source": "confirmed_visit",
        "enjoyment": "did_not_enjoy",
        "created_at": NOW.isoformat(),
    }
    score = score_item(
        item,
        {"memory_enabled": True},
        {},
        outlet_id=item.outlet_id,
        at=NOW,
        observations=[event],
        venue_preferences=[{"outlet_id": item.outlet_id, "would_repeat": True}],
    )
    assert score.features["H"] == Feature(1, 1)


def test_familiar_novelty_does_not_reward_a_disliked_visit_or_unconfirmed_selection():
    person = {
        "profile": {"memory_enabled": True},
        "response": {"novelty": "familiar"},
        "observations": [
            {
                "outlet_id": "venue",
                "source": "confirmed_visit",
                "enjoyment": "did_not_enjoy",
                "created_at": NOW.isoformat(),
            }
        ],
    }
    assert novelty_feature(person, "venue", at=NOW) == Feature(0.2, 1)
    person["observations"][0]["source"] = "selection"
    assert novelty_feature(person, "venue", at=NOW) == Feature(0.5, 0)


def test_explore_requires_actual_visits_or_explicitly_complete_history():
    person = {
        "profile": {"memory_enabled": True},
        "response": {"novelty": "explore"},
        "observations": [],
    }
    assert novelty_feature(person, "venue", at=NOW) == Feature(0.5, 0)
    person["visit_history_complete"] = True
    assert novelty_feature(person, "venue", at=NOW) == Feature(1, 1)
    person["observations"] = [
        {
            "outlet_id": "venue",
            "source": "confirmed_visit",
            "created_at": NOW.isoformat(),
        }
    ]
    assert novelty_feature(person, "venue", at=NOW) == Feature(0.2, 1)
