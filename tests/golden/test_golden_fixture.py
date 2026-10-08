"""Golden fixture suite: one test per case in docs/GOLDEN_RECOMMENDATION_TESTS.md (GT-001..GT-104).

Expectations come from the PRD and MAKAN-212, not from the implementation. A failing case
is reported for a product decision; expectations are never edited to make a case pass.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

import golden_catalog as gc
import pytest
from golden_catalog import confirmations, diner, meal_at, route, snapshot, visit

import dining.recommendation.engine as rec_mod
from dining.recommendation.engine import Recommender
from dining.recommendation.personal import score_personal_options
from dining.recommendation.ranking import (
    MINIMUM_INDIVIDUAL_FIT,
    base_score,
    group_fit,
    rank_diverse,
)

APPROX = 5e-4


class Run:
    def __init__(self, result, scores, cat, snap):
        self.result, self.scores, self.catalog, self.snap = result, scores, cat, snap

    @property
    def order(self):
        return [o["outlet_id"] for o in self.result["options"]]

    @property
    def status(self):
        return self.result["status"]

    @property
    def verification(self):
        return {v["outlet_id"] for v in self.result["verification"]}

    def items(self, oid):
        option = next(o for o in self.result["options"] if o["outlet_id"] == oid)
        return {m["id"] for m in option["menu_items"]}

    def all_items(self):
        return {m["id"] for o in self.result["options"] for m in o["menu_items"]}

    def personal(self, n):
        person = self.snap["participants"][n - 1]
        return score_personal_options(
            {"profile": person["profile"], "response": person["response"]},
            self.result["options"],
            self.catalog,
        )

    def personal_pairs(self, n):
        return [(r["outlet_id"], r["item_id"]) for r in self.personal(n)]

    def personal_item_at(self, n, oid):
        return next(r["item_id"] for r in self.personal(n) if r["outlet_id"] == oid)


@pytest.fixture
def run(monkeypatch):
    def _run(snap, raw=None):
        captured = {}
        original = rec_mod.rank_diverse

        def spy(candidates, **kwargs):
            captured.update({c["outlet_id"]: c["_score"] for c in candidates})
            return original(candidates, **kwargs)

        monkeypatch.setattr(rec_mod, "rank_diverse", spy)
        cat = gc.catalog(raw)
        result = Recommender(cat)(snap)
        _invariants(result)
        return Run(result, captured, cat, snap)

    return _run


def _invariants(result):
    """INV-1 and INV-3 from doc §3 (personal-item eligibility is asserted in section F)."""
    ids = [o["outlet_id"] for o in result["options"]]
    assert len(ids) <= 3 and len(ids) == len(set(ids)), f"INV-1 violated: {ids}"
    assert "participants" not in result and "profile" not in json.dumps(
        result, default=str
    )


def scores(r, expected):
    for oid, value in expected.items():
        assert r.scores[oid] == pytest.approx(value, abs=APPROX), (
            f"{oid} score {r.scores[oid]:.4f} != {value}"
        )


def D(profile=None, **response):
    return diner(profile, **response)


N = {"craving": "noodles"}


# ---------------------------------------------------------------- A. hard gates
def test_gt_001_baseline(run):
    r = run(snapshot(D(), D()))
    assert r.status == "shortlisted"
    assert r.order == ["G01", "G02", "G03"]
    assert [r.items(o) for o in r.order] == [{"I02"}, {"I03"}, {"I06"}]
    scores(r, {"G01": 0.5, "G02": 0.5, "G03": 0.5})
    assert all(o["fit_confidence"] == "limited" for o in r.result["options"])


def test_gt_002_radius(run):
    raw = gc.base_raw()
    gc.ov_g09(raw)
    r = run(snapshot(D(craving="soup"), D(craving="soup")), raw)
    assert r.order == ["G01", "G06", "G07"] and "G09" not in r.verification
    assert r.result["examined_outlets"] == 7


def test_gt_003_vegetarian(run):
    r = run(snapshot(D({"dietary_requirements": ["vegetarian"]}, **N), D(**N)))
    assert r.order == ["G07", "G03", "G01"]
    assert (
        r.items("G07") == {"I13", "I14"}
        and r.items("G03") == {"I06", "I05"}
        and r.items("G01") == {"I02", "I01"}
    )
    scores(r, {"G07": 0.5574, "G03": 0.4770, "G01": 0.4426})


def test_gt_004_vegan(run):
    assert run(snapshot(D({"dietary_requirements": ["vegan"]}), D())).order == [
        "G01",
        "G02",
    ]


def test_gt_005_same_dish_diet_and_budget(run):
    r = run(snapshot(D({"dietary_requirements": ["vegetarian"]}, budget=12), D()))
    assert r.order == ["G07"] and r.items("G07") == {"I13"}


def test_gt_006_firm_cap(run):
    r = run(snapshot(D(budget=12), D()))
    assert r.order == ["G07"] and r.items("G07") <= {"I13", "I14"}


def test_gt_007_cap_boundary(run):
    a = run(snapshot(D(budget=13), D()))
    assert a.order == ["G03", "G07"] and "I06" in a.items("G03")
    assert run(snapshot(D(budget=12.99), D())).order == ["G07"]


def test_gt_008_drink_is_not_a_meal(run):
    r = run(snapshot(D(budget=5), D()))
    assert r.status == "no_options" and r.order == []


def test_gt_009_certified_halal(run):
    r = run(snapshot(D({"halal_policy": "certified"}), D()))
    assert r.order == ["G01", "G07"]


def test_gt_010_expired_certificate(run):
    raw = gc.base_raw()
    gc.ov_halal_expired(raw)
    r = run(snapshot(D({"halal_policy": "certified"}), D()), raw)
    assert r.order == ["G07"] and "G01" in r.verification


@pytest.mark.parametrize("policy", ["unknown", "review"])
def test_gt_011_unknown_halal_policy(run, policy):
    r = run(snapshot(D({"halal_policy": policy}), D()))
    assert r.status == "needs_verification" and r.order == []


@pytest.mark.parametrize("state", ["unknown", "withheld"])
def test_gt_012_unknown_allergy_state(run, state):
    r = run(snapshot(D({"allergy_status": state}), D()))
    assert r.status == "needs_verification" and r.order == []
    assert "allerg" not in r.result["explanation"].casefold()


def test_gt_013_declared_allergy_without_confirmation(run):
    r = run(snapshot(D({"allergy_status": "declared", "allergens": ["peanut"]}), D()))
    assert r.status == "needs_verification" and r.order == []
    assert "peanut" not in json.dumps(r.result, default=str).casefold()


def test_gt_014_avoid_peanut(run):
    snap = snapshot(
        D(craving="rice", avoid=["peanut"]),
        D(craving="rice"),
        confirmations=confirmations("peanut"),
    )
    r = run(snap)
    assert r.order == ["G02", "G03", "G07"]
    scores(r, {"G02": 0.6913, "G03": 0.6913, "G07": 0.5})


def test_gt_015_avoid_pork(run):
    snap = snapshot(D(avoid=["pork"], **N), D(**N), confirmations=confirmations("pork"))
    r = run(snap)
    assert r.order == ["G07", "G01", "G06"]
    scores(r, {"G03": 0.4770})


def test_gt_016_expired_confirmation(run):
    conf = confirmations("peanut", overrides={"G07": meal_at() - timedelta(hours=1)})
    r = run(snapshot(D(avoid=["peanut"], **N), D(**N), confirmations=conf))
    assert r.order == ["G03", "G01", "G06"] and "G07" in r.verification


def test_gt_017_step_free_required(run):
    r = run(snapshot(D({"accessibility_requirements": ["step_free_entrance"]}), D()))
    assert r.order == ["G02"]
    assert "G05" not in r.verification, (
        "known-inaccessible outlet must be excluded, not 'needs confirmation'"
    )
    assert {"G01", "G03", "G04", "G06", "G07"} <= r.verification


def test_gt_018_known_closure(run):
    raw = gc.base_raw()
    gc.ov_closed(raw, "G03", meal_at().date())
    r = run(snapshot(D(**N), D(**N)), raw)
    assert r.order == ["G07", "G01", "G06"] and "G03" not in r.verification


def test_gt_019_unknown_hours(run):
    raw = gc.base_raw()
    gc.ov_hours_unknown(raw, "G04")
    r = run(snapshot(D(craving="grill"), D(craving="salad")), raw)
    assert "G04" not in r.order and "G04" in r.verification
    assert r.order == ["G07", "G01", "G02"]


def test_gt_020_late_meal(run):
    r = run(snapshot(D(), D(), at=meal_at(hh=22, mm=15), duration=45))
    assert r.order == ["G07"] and r.items("G07") == {"I13"}


def test_gt_021_overnight(run):
    raw = gc.base_raw()
    gc.ov_g07_night(raw)
    r = run(snapshot(D(), D(), at=meal_at(days=2, hh=1, mm=0)), raw)
    assert r.order == ["G07"]


def test_gt_022_expired_source(run):
    raw = gc.base_raw()
    gc.ov_sources_expired(raw, "G05")
    r = run(snapshot(D(craving="pasta"), D(craving="pizza")), raw)
    assert "G05" not in r.order and r.order == ["G07", "G01", "G02"]


def test_gt_023_quarantined_and_unavailable(run):
    raw = gc.base_raw()
    gc.item(raw, "I05")["review_status"] = "quarantined"
    gc.item(raw, "I14")["live_availability"] = "unavailable"
    r = run(snapshot(D(**N), D(**N)), raw)
    assert r.order == ["G01", "G06", "G07"] and r.items("G07") == {"I13"}
    assert not {"I05", "I14"} & r.all_items()


def test_gt_024_shared_platter(run):
    raw = gc.base_raw()
    gc.ov_platter(raw)
    r = run(snapshot(D(craving="soup"), D(craving="soup")), raw)
    assert r.order == ["G01", "G06", "G07"] and r.items("G01") == {"I02"}


# ---------------------------------------------------------------- B. status
def test_gt_025_one_diner(run):
    assert run(snapshot(D())).status == "needs_input"


def test_gt_026_no_meeting_point(run):
    r = run(snapshot(D(), D(), lat=None, lng=None))
    assert r.status == "needs_input" and r.order == []


@pytest.mark.parametrize("field", ["profile", "response"])
def test_gt_027_requirements_not_reviewed(run, field):
    p2 = (
        D({"requirements_reviewed": False})
        if field == "profile"
        else D(requirements_confirmed=False)
    )
    r = run(snapshot(D(), p2))
    assert r.status == "needs_verification" and r.order == []


def test_gt_028_no_overlap(run):
    snap = snapshot(
        D(budget=8),
        D({"dietary_requirements": ["vegetarian"]}, avoid=["wheat"]),
        confirmations=confirmations("wheat"),
    )
    r = run(snap)
    assert r.status == "no_options" and r.order == []
    text = r.result["explanation"].casefold()
    assert not any(word in text for word in ("p1", "p2", "wheat", "vegetarian", "rm8"))


def test_gt_029_unknown_all_in_price(run):
    raw = gc.base_raw()
    gc.ov_charges_unknown(raw, "I13", "I14")
    r = run(snapshot(D(budget=12), D()), raw)
    assert r.status == "needs_verification" and r.order == []


# ---------------------------------------------------------------- C. scoring
def test_gt_030_both_noodles(run):
    r = run(snapshot(D(**N), D(**N)))
    assert r.order == ["G03", "G07", "G01"]
    assert [r.items(o) for o in r.order] == [{"I05"}, {"I14"}, {"I01"}]
    scores(r, {"G03": 0.6913, "G07": 0.6913, "G01": 0.5765})


def test_gt_031_exact_beats_broader(run):
    r = run(snapshot(D(craving="soup"), D(craving="soup")))
    assert r.order == ["G01", "G06", "G07"] and r.items("G01") == {"I02"}
    scores(r, {"G01": 0.6913, "G06": 0.5765, "G07": 0.5})


def test_gt_032_both_rice(run):
    r = run(snapshot(D(craving="rice"), D(craving="rice")))
    assert r.order == ["G02", "G03", "G06"]
    assert [r.items(o) for o in r.order] == [{"I03"}, {"I06"}, {"I12"}]


def test_gt_033_both_spicy(run):
    r = run(snapshot(D(craving="spicy"), D(craving="spicy")))
    assert r.order == ["G02", "G06", "G07"]
    assert [r.items(o) for o in r.order] == [{"I04"}, {"I11"}, {"I14"}]


def test_gt_034_same_outlet_different_dishes(run):
    r = run(snapshot(D(craving="pasta"), D(craving="pizza")))
    assert r.order == ["G05", "G07", "G01"] and r.items("G05") == {"I09", "I10"}


def test_gt_035_noodles_vs_rice(run):
    r = run(snapshot(D(**N), D(craving="rice")))
    assert r.order == ["G03", "G06", "G07"] and r.items("G03") == {"I05", "I06"}
    scores(r, {"G03": 0.6913, "G06": 0.6109, "G07": 0.5574})


def test_gt_036_grill_vs_salad(run):
    r = run(snapshot(D(craving="grill"), D(craving="salad")))
    assert r.order[0] == "G04" and r.items("G04") == {"I07", "I08"}
    scores(r, {"G04": 0.6913})


def test_gt_037_anything(run):
    r = run(snapshot(D(craving="anything"), D(craving="anything")))
    assert r.status == "shortlisted" and r.order == ["G01", "G02", "G03"]


def test_gt_038_negated_craving(run):
    assert run(snapshot(D(craving="no noodles"), D(craving="no noodles"))).order == [
        "G01",
        "G02",
        "G03",
    ]


def test_gt_039_cuisine_today(run):
    r = run(snapshot(D(cuisines=["Thai"]), D(cuisines=["Thai"])))
    assert r.order[0] == "G06" and r.items("G06") == {"I12"}
    scores(r, {"G06": 0.6913})


def test_gt_040_thai_vs_indian(run):
    r = run(snapshot(D(cuisines=["Thai"]), D(cuisines=["Indian"])))
    assert r.order[:2] == ["G02", "G06"]
    scores(r, {"G02": 0.4770, "G06": 0.4770})


def test_gt_041_spice_hot(run):
    r = run(snapshot(D(spice="hot"), D(spice="hot")))
    assert (
        r.order == ["G02", "G06", "G01"]
        and r.items("G02") == {"I04"}
        and r.items("G06") == {"I11"}
    )


def test_gt_042_spice_none(run):
    r = run(snapshot(D(spice="none"), D(spice="none")))
    assert r.order == ["G01", "G03", "G04"]
    assert [r.items(o) for o in r.order] == [{"I02"}, {"I06"}, {"I08"}]


def test_gt_043_noodle_soup_phrase(run):
    r = run(snapshot(D(craving="noodle soup"), D(craving="noodle soup")))
    assert r.order == ["G01", "G06", "G03"]
    assert r.items("G01") == {"I01"} and r.items("G06") == {"I11"}


def test_gt_044_malay_terms(run):
    r = run(snapshot(D(craving="mee pedas"), D(craving="mee pedas")))
    assert r.order == ["G07", "G06", "G02"] and r.items("G07") == {"I14"}


def test_gt_045_light_appetite(run):
    r = run(snapshot(D(appetite="light"), D(appetite="light")))
    assert r.order == ["G01", "G04", "G07"]
    assert [r.items(o) for o in r.order] == [{"I02"}, {"I08"}, {"I13"}]
    scores(r, {"G01": 0.5425, "G04": 0.5425, "G07": 0.5425})


def test_gt_046_hearty_appetite(run):
    r = run(snapshot(D(appetite="hearty"), D(appetite="hearty")))
    assert (
        r.order == ["G02", "G04", "G01"]
        and r.items("G02") == {"I03"}
        and r.items("G04") == {"I07"}
    )


def test_gt_047_soft_target_and_cap(run):
    r = run(
        snapshot(D(craving="rice", soft_budget_target=15, budget=30), D(craving="rice"))
    )
    assert r.order == ["G03", "G02", "G06"]
    scores(r, {"G03": 0.7040, "G02": 0.7023, "G06": 0.6955})
    assert "G05" not in r.scores


def test_gt_048_flexible_budget(run):
    r = run(snapshot(D(craving="noodles", soft_budget_target=15, budget=None), D(**N)))
    assert r.status == "shortlisted"
    assert r.order == ["G03", "G07", "G01"]
    scores(r, {"G01": 0.5850})


def _eta_diner(etas, expired=()):
    est = {oid: route(eta, expired=oid in expired) for oid, eta in etas.items()}
    return D({"route_estimates": est}, craving="rice", comfortable_travel_minutes=20)


def test_gt_049_travel_time(run):
    etas = {"G01": 5, "G02": 5, "G03": 30, "G06": 10, "G07": 18}
    r = run(snapshot(_eta_diner(etas), _eta_diner(etas)))
    assert r.order == ["G02", "G06", "G03"]
    scores(r, {"G02": 0.7231, "G06": 0.6913, "G03": 0.6275})


def test_gt_050_expired_route(run):
    etas = {"G02": 18, "G03": 2, "G06": 2}
    r = run(snapshot(_eta_diner(etas, {"G03"}), _eta_diner(etas, {"G03"})))
    assert r.order == ["G06", "G03", "G02"]
    scores(r, {"G06": 0.7423, "G03": 0.6913, "G02": 0.6403})


def test_gt_051_lasting_taste(run):
    r = run(snapshot(D({"cuisines": ["Thai"]}), D()))
    assert r.order == ["G06", "G01", "G02"]
    scores(r, {"G06": 0.5255})


def test_gt_052_today_overrides_history(run):
    r = run(snapshot(D({"cuisines": ["Thai"]}, cuisines=["Indian"]), D()))
    assert r.order[0] == "G02"
    scores(r, {"G02": 0.5574, "G06": 0.4197})


def test_gt_053_three_diners(run):
    r = run(snapshot(D(**N), D(craving="soup"), D(craving="rice")))
    assert r.order == ["G06", "G07", "G03"] and r.items("G06") == {"I11", "I12"}
    scores(r, {"G06": 0.5995, "G07": 0.5383, "G03": 0.5076})


def test_gt_054_eight_diners(run):
    r = run(snapshot(*[D(**N) for _ in range(8)]))
    assert r.order == ["G03", "G07", "G01"]
    scores(r, {"G03": 0.6913, "G07": 0.6913, "G01": 0.5765})


def test_gt_055_order_has_no_effect(run):
    a = run(snapshot(D(**N), D(craving="rice")))
    b = run(snapshot(D(craving="rice"), D(**N)))
    assert a.order == b.order == ["G03", "G06", "G07"]
    for oid in a.order:
        assert a.scores[oid] == pytest.approx(b.scores[oid], abs=1e-9)


def test_gt_056_duplicates_and_noise(run):
    raw = gc.base_raw()
    gc.ov_dup(raw)
    a = run(snapshot(D(**N), D(**N)), raw)
    assert a.order == ["G03", "G07", "G01"]
    scores(a, {"G03": 0.6913, "G07": 0.6913, "G01": 0.5765})
    raw = gc.base_raw()
    gc.ov_noise(raw)
    b = run(snapshot(D(craving="soup"), D(craving="soup")), raw)
    assert b.order == ["G01", "G06", "G07"]
    assert not any(i.startswith("noise-") for i in b.all_items())


def test_gt_057_prd_worked_example():
    assert group_fit([0.95, 0.85, 0.20]) == pytest.approx(0.48, abs=1e-4)
    assert min([0.95, 0.85, 0.20]) < MINIMUM_INDIVIDUAL_FIT
    b, c = base_score([0.75, 0.75, 0.70]), base_score([0.80, 0.60, 0.65])
    assert (
        b == pytest.approx(0.687, abs=1e-4)
        and c == pytest.approx(0.6275, abs=1e-4)
        and b > c
    )


def test_gt_058_learned_venue_and_memory_off(run):
    visits = [visit("G06"), visit("G06")]
    a = run(snapshot(D({"memory_enabled": True, "observations": visits}), D()))
    assert a.order == ["G06", "G01", "G02"]
    scores(a, {"G06": 0.5127})
    b = run(snapshot(D({"memory_enabled": False, "observations": visits}), D()))
    assert b.order == ["G01", "G02", "G03"]


def test_gt_059_would_not_repeat(run):
    p1 = D(
        {
            "memory_enabled": True,
            "observations": [visit("G06"), visit("G06")],
            "venue_preferences": [{"outlet_id": "G06", "would_repeat": False}],
        },
        craving="spicy",
    )
    r = run(snapshot(p1, D(craving="spicy")))
    assert r.order == ["G02", "G07", "G06"]
    scores(r, {"G06": 0.6318})


def _novelty(intent, oid=None, enjoyment="enjoyed", complete=True):
    prof = {
        "memory_enabled": True,
        "observations": [visit(oid, enjoyment)] if oid else [],
        "visit_history_complete": complete,
    }
    return D(prof, novelty=intent)


def test_gt_060_explore(run):
    r = run(snapshot(_novelty("explore", "G01"), _novelty("explore", "G01")))
    # Corrected 7 Oct 2026: an enjoyed, consented visit also lifts lasting venue taste H
    # (PRD §9.2 learned affinity) to .6658, so G01 = .5132 (oracle omitted H; not a product change).
    assert r.order == ["G02", "G03", "G04"]
    scores(r, {"G01": 0.5132, "G02": 0.5250})
    v = run(
        snapshot(
            _novelty("explore", "G01", complete=False),
            _novelty("explore", "G01", complete=False),
        )
    )
    assert v.order == ["G01", "G02", "G03"]
    scores(v, {"G01": 0.5132, "G02": 0.5})


def test_gt_061_familiar(run):
    a = run(
        snapshot(
            _novelty("familiar", "G05", complete=False),
            _novelty("familiar", "G05", complete=False),
        )
    )
    # Corrected 7 Oct 2026: venue H from the visit is included (enjoyed .6658, disliked .3342).
    assert a.order == ["G05", "G01", "G02"]
    scores(a, {"G05": 0.5532})
    b = run(
        snapshot(
            *[
                _novelty("familiar", "G05", "did_not_enjoy", complete=False)
                for _ in range(2)
            ]
        )
    )
    assert b.order == ["G01", "G02", "G03"]
    scores(b, {"G05": 0.4568})


def test_gt_062_feedback_half_life(run):
    p1 = D(
        {
            "memory_enabled": True,
            "observations": [visit("G04", days_ago=1), visit("G06", days_ago=360)],
        }
    )
    r = run(snapshot(p1, D()))
    assert r.order == ["G04", "G06", "G01"]
    scores(r, {"G04": 0.5085, "G06": 0.5008})


def test_gt_063_learning_never_relaxes_halal(run):
    p1 = D(
        {
            "halal_policy": "certified",
            "memory_enabled": True,
            "observations": [visit("G03") for _ in range(5)],
        }
    )
    assert run(snapshot(p1, D())).order == ["G01", "G07"]


# ---------------------------------------------------------------- D. fairness floor
DISLIKES = {
    "taste_preferences": {
        "cuisine:thai": "dislike",
        "flavour:sour": "dislike",
        "spice:hot": "dislike",
    }
}


def test_gt_064_poor_fit_blocks_favourite(run):
    r = run(snapshot(D(DISLIKES, craving="pasta"), D(craving="spicy sour")))
    assert "G06" not in r.order and r.order == ["G07", "G02", "G05"]


def test_gt_065_majority_cannot_override_floor(run):
    r = run(
        snapshot(
            D(craving="spicy sour"),
            D(craving="spicy sour"),
            D(DISLIKES, craving="pasta"),
        )
    )
    assert "G06" not in r.order and r.order == ["G07", "G02", "G05"]
    scores(r, {"G07": 0.5765, "G02": 0.5076, "G05": 0.4464})


def test_gt_066_all_below_floor(run):
    prefs = {
        "taste_preferences": {
            f"cuisine:{c}": "dislike" for c in ("malaysian", "indian", "chinese")
        }
    }
    r = run(snapshot(D(prefs, craving="pizza"), D(craving="anything"), radius=1.0))
    assert r.status == "needs_input" and r.order == []
    assert "p1" not in r.result["explanation"].casefold()


def test_gt_067_compromise(run):
    r = run(snapshot(D(craving="soup"), D(craving="grill")))
    assert r.order == ["G07", "G01", "G04"]
    scores(r, {"G07": 0.5, "G01": 0.4770, "G04": 0.4770})


def test_gt_068_floor_boundary():
    assert not 0.35 < MINIMUM_INDIVIDUAL_FIT
    assert 0.3499 < MINIMUM_INDIVIDUAL_FIT


# ---------------------------------------------------------------- E. diversity
def _row(oid, score, cuisine, brand=None, dist=1.0):
    return {
        "outlet_id": oid,
        "brand_id": brand or oid,
        "cuisines": [cuisine],
        "distance_km": dist,
        "_score": score,
    }


def test_gt_069_same_brand_branch(run):
    raw = gc.base_raw()
    gc.ov_g08(raw)
    r = run(snapshot(D(craving="soup"), D(craving="soup")), raw)
    assert r.order == ["G01", "G06", "G07"] and "G08" not in r.order
    scores(r, {"G08": 0.6913})


def test_gt_070_closer_branch_kept():
    rows = [
        _row("X1", 0.80, "A", "X", 3.0),
        _row("X2", 0.75, "B", "X", 1.5),
        _row("Y", 0.50, "C", "Y", 1.0),
    ]
    assert [r["outlet_id"] for r in rank_diverse(rows)] == ["X1", "X2", "Y"]


def test_gt_071_diversity_within_point_one():
    rows = [
        _row("A", 0.80, "Malaysian"),
        _row("B", 0.78, "Malaysian"),
        _row("C", 0.72, "Thai"),
    ]
    assert [r["outlet_id"] for r in rank_diverse(rows)] == ["A", "C", "B"]


def test_gt_072_diversity_not_beyond_point_one():
    rows = [
        _row("A", 0.80, "Malaysian"),
        _row("B", 0.78, "Malaysian"),
        _row("C", 0.60, "Thai"),
    ]
    assert [r["outlet_id"] for r in rank_diverse(rows)] == ["A", "B", "C"]


def test_gt_073_tie_break():
    rows = [
        _row("c", 0.5, "X", dist=2.0),
        _row("b", 0.5, "X", dist=1.0),
        _row("a", 0.5, "X", dist=1.0),
    ]
    assert [r["outlet_id"] for r in rank_diverse(rows)] == ["a", "b", "c"]


def test_gt_074_max_three_unique(run):
    r = run(snapshot(D(), D()))
    brands = [o["brand_id"] for o in r.result["options"]]
    assert len(r.order) == 3 and len(set(brands)) == 3


# ---------------------------------------------------------------- F. personal best fit
def _eligible_for(person, cat, item_id):
    item = next(i for i in cat.menu_items if i.item_id == item_id)
    p, resp = person["profile"], person["response"]
    budget = resp.get("budget", p.get("max_budget"))
    return (
        set(p.get("dietary_requirements", [])) <= set(item.dietary_claims)
        and not set(resp.get("avoid", [])) & set(item.ingredients)
        and (budget is None or item.price.payable_amount_minor <= round(budget * 100))
        and item.meal_role in {"main", "set"}
        and item.review_status == "reviewed"
        and item.live_availability != "unavailable"
    )


def assert_inv4(r):
    pool = set(r.order)
    for n, person in enumerate(r.snap["participants"], 1):
        recs = r.personal(n)
        assert 1 <= len(recs) <= 3, f"P{n} has {len(recs)} personal results"
        assert [x["rank"] for x in recs] == list(range(1, len(recs) + 1))
        for rec in recs:
            assert rec["outlet_id"] in pool, (
                f"P{n} personal outlet {rec['outlet_id']} not in shortlist"
            )
            assert 0 <= rec["score"] <= 1
            assert _eligible_for(person, r.catalog, rec["item_id"]), (
                f"P{n} personal item {rec['item_id']} fails P{n}'s own checks"
            )


def test_gt_075_personal_from_group_pool(run):
    r = run(snapshot(D(**N), D(craving="rice")))
    assert r.order == ["G03", "G06", "G07"]
    for n in (1, 2):
        recs = r.personal(n)
        assert {x["outlet_id"] for x in recs} == {"G03", "G06", "G07"} and [
            x["rank"] for x in recs
        ] == [1, 2, 3]
    assert_inv4(r)


def _r076(run):
    return run(snapshot(D(craving="noodles", cuisines=["Chinese"]), D(craving="rice")))


def test_gt_076_clear_personal_best(run):
    r = _r076(run)
    assert r.order == ["G03", "G06", "G07"]
    assert r.personal_pairs(1) == [("G03", "I05"), ("G07", "I14"), ("G06", "I11")]
    p2 = r.personal_pairs(2)
    assert p2[0] in {("G03", "I06"), ("G06", "I12")} and p2[2] == ("G07", "I13")


def _r077(run):
    return run(snapshot(D({"halal_policy": "certified"}, craving="soup"), D(**N)))


def test_gt_077_different_best_fit(run):
    r = _r077(run)
    assert r.order == ["G01", "G07"]
    assert r.personal_pairs(1)[0] == ("G01", "I02") and r.personal_pairs(2)[0] == (
        "G07",
        "I14",
    )


def test_gt_078_personal_matches_group_item(run):
    r = _r076(run)
    expected = {
        1: {"G03": "I05", "G06": "I11", "G07": "I14"},
        2: {"G03": "I06", "G06": "I12", "G07": "I13"},
    }
    for n, by_outlet in expected.items():
        assert {oid: r.personal_item_at(n, oid) for oid in by_outlet} == by_outlet


def test_gt_079_vegetarian_never_gets_meat(run):
    r = run(snapshot(D({"dietary_requirements": ["vegetarian"]}, **N), D(**N)))
    assert {oid: r.personal_item_at(1, oid) for oid in r.order} == {
        "G07": "I13",
        "G03": "I06",
        "G01": "I02",
    }
    assert_inv4(r)


def test_gt_080_vegan_personal(run):
    r = run(
        snapshot(
            D({"dietary_requirements": ["vegan"]}, craving="rice"), D(craving="rice")
        )
    )
    assert r.order == ["G02", "G01"]
    assert r.personal_pairs(1) == [("G02", "I03"), ("G01", "I02")]


def test_gt_081_avoid_list_personal(run):
    snap = snapshot(
        D(craving="rice", cuisines=["Thai"], avoid=["peanut"]),
        D(cuisines=["Thai"]),
        confirmations=confirmations("peanut"),
    )
    r = run(snap)
    assert r.order == ["G06", "G02", "G03"]
    assert r.personal_item_at(1, "G06") == "I11"
    assert "I12" not in {i for _, i in r.personal_pairs(1)}


def test_gt_082_avoid_pork_personal(run):
    snap = snapshot(
        D(avoid=["pork"], **N),
        D(craving="rice", cuisines=["Chinese"]),
        confirmations=confirmations("pork"),
    )
    r = run(snap)
    assert r.order == ["G06", "G07", "G03"]
    assert r.personal_pairs(1) == [("G07", "I14"), ("G06", "I11"), ("G03", "I06")]


def test_gt_083_firm_cap_personal(run):
    r = run(snapshot(D(budget=15, **N), D(**N)))
    assert r.order == ["G03", "G07", "G01"]
    assert r.personal_item_at(1, "G01") == "I02"


def test_gt_084_halal_personal(run):
    r = _r077(run)
    assert {oid for oid, _ in r.personal_pairs(1)} <= {"G01", "G07"}


def test_gt_085_drink_never_personal(run):
    r = run(snapshot(D(craving="teh tarik"), D(**N)))
    assert "G07" in r.order
    assert r.personal_item_at(1, "G07") in {"I13", "I14"}


@pytest.mark.parametrize("change", ["quarantined", "unavailable"])
def test_gt_086_quarantined_or_unavailable_personal(run, change):
    raw = gc.base_raw()
    if change == "quarantined":
        gc.item(raw, "I02")["review_status"] = "quarantined"
    else:
        gc.item(raw, "I02")["live_availability"] = "unavailable"
    r = run(snapshot(D(craving="soup"), D(craving="soup")), raw)
    assert "G01" in r.order and r.items("G01") == {"I01"}
    for n in (1, 2):
        assert "I02" not in {i for _, i in r.personal_pairs(n)}


def test_gt_087_light_appetite_personal(run):
    r = run(snapshot(D(appetite="light"), D(craving="grill")))
    assert r.order == ["G04", "G07", "G01"]
    assert r.personal_item_at(1, "G04") == "I08"
    light = {i.item_id for i in r.catalog.menu_items if "light" in i.attributes}
    for rec in r.personal(1):
        if "LIGHT_MEAL_MATCH" in rec["reason_codes"]:
            assert rec["item_id"] in light


def test_gt_088_chilli_level_personal(run):
    r = run(snapshot(D(spice="hot"), D(craving="rice")))
    assert r.order == ["G02", "G06", "G03"]
    assert (
        r.personal_item_at(1, "G02") == "I04" and r.personal_item_at(1, "G06") == "I11"
    )


def test_gt_089_today_beats_lasting_personal(run):
    r = run(snapshot(D({"cuisines": ["Thai"]}, craving="pasta"), D(craving="spicy")))
    assert r.order == ["G07", "G06", "G02"]
    assert r.personal_pairs(1) == [("G07", "I13"), ("G06", "I12"), ("G02", "I03")]


def test_gt_090_negated_craving_personal(run):
    r = run(snapshot(D(craving="no noodles"), D(**N)))
    assert r.order == ["G03", "G07", "G01"]
    assert {oid: r.personal_item_at(1, oid) for oid in r.order} == {
        "G03": "I06",
        "G07": "I13",
        "G01": "I02",
    }
    assert not any("CRAVING_MATCH" in rec["reason_codes"] for rec in r.personal(1))


def test_gt_091_no_fuzzy_false_positive(run):
    r = run(snapshot(D(craving="sour"), D(craving="sour")))
    assert r.order == ["G06", "G01", "G02"]
    recs = r.personal(1)
    assert (recs[0]["outlet_id"], recs[0]["item_id"]) == ("G06", "I11")
    assert not any(
        "CRAVING_MATCH" in x["reason_codes"]
        for x in recs
        if x["outlet_id"] in {"G01", "G02"}
    )


ALLOWED_CODES = {
    "GROUP_ELIGIBLE_POOL",
    "CRAVING_MATCH",
    "DISH_FAMILY_MATCH",
    "FAVOURITE_CUISINE_MATCH",
    "WITHIN_COMFORT_BUDGET",
    "LIGHT_MEAL_MATCH",
}


def test_gt_092_reason_codes(run):
    r = _r076(run)
    expected_craving = {1: {"G03", "G07"}, 2: {"G03", "G06"}}
    for n in (1, 2):
        person = r.snap["participants"][n - 1]
        for rec in r.personal(n):
            codes = set(rec["reason_codes"])
            assert codes <= ALLOWED_CODES
            item = next(i for i in r.catalog.menu_items if i.item_id == rec["item_id"])
            within = (
                item.price.payable_amount_minor <= person["response"]["budget"] * 100
            )
            assert ("WITHIN_COMFORT_BUDGET" in codes) == within
            assert ("CRAVING_MATCH" in codes) == (
                rec["outlet_id"] in expected_craving[n]
            ), (n, rec["outlet_id"], codes)
            assert ("FAVOURITE_CUISINE_MATCH" in codes) == (
                n == 1 and rec["outlet_id"] == "G03"
            )


def test_gt_093_neutral_personal(run):
    r = run(snapshot(D(), D()))
    assert r.personal_pairs(1) == [("G01", "I02"), ("G02", "I03"), ("G03", "I06")]
    assert len({x["score"] for x in r.personal(1)}) == 1


def test_gt_094_personal_deterministic(run):
    a, b = _r076(run), _r076(run)
    c = run(
        snapshot(
            D(craving="rice", uid="P2"),
            D(craving="noodles", cuisines=["Chinese"], uid="P1"),
        )
    )
    strip = lambda recs: [
        {k: v for k, v in x.items() if k not in {"created_at", "updated_at"}}
        for x in recs
    ]
    assert strip(a.personal(1)) == strip(b.personal(1)) == strip(c.personal(2))
    assert strip(a.personal(2)) == strip(b.personal(2)) == strip(c.personal(1))


def test_gt_095_same_outlet_different_dish(run):
    r = run(snapshot(D(craving="pasta"), D(craving="pizza")))
    assert r.personal_pairs(1)[0] == ("G05", "I09") and r.personal_pairs(2)[0] == (
        "G05",
        "I10",
    )


# ---------------------------------------------------------------- G. lifecycle via the real API
@pytest.fixture
def app_flow(tmp_path):
    from test_api import checkin, create_meal, make_client, setup_room

    from webapp import create_app

    path = tmp_path / "gc1.json"
    path.write_text(gc.catalog().model_dump_json(), encoding="utf-8")
    db = tmp_path / "golden.sqlite3"
    app = create_app(db, path, demo_mode=True)
    p1, p2 = make_client(app, "GoldenOne"), make_client(app, "GoldenTwo")
    room, _ = setup_room([p1, p2])
    meal, _ = create_meal(
        p1,
        room,
        meal_at=meal_at().isoformat(),
        latitude=gc.ORIGIN[0],
        longitude=gc.ORIGIN[1],
    )
    meal = checkin(p1, meal["id"], craving="noodles", cuisines=["Chinese"], budget=50)
    meal = checkin(p2, meal["id"], craving="rice", cuisines=[], budget=50)
    p1.post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    app.state.generation_worker.tick()

    class Flow:
        pass

    flow = Flow()
    flow.app, flow.p1, flow.p2, flow.meal_id, flow.db = app, p1, p2, meal["id"], db
    flow.view = lambda client: client.get(f"/api/meals/{meal['id']}").json()
    flow.checkin = checkin
    return flow


def _count(db, table, where="", args=()):
    with sqlite3.connect(db) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table} {where}", args).fetchone()[0]


def test_gt_096_personal_privacy(app_flow):
    v1, v2 = app_flow.view(app_flow.p1), app_flow.view(app_flow.p2)
    mine, theirs = v1["my_personal_recommendations"], v2["my_personal_recommendations"]
    assert mine and theirs
    assert json.dumps(theirs) not in json.dumps(v1) and json.dumps(
        mine
    ) not in json.dumps(v2)
    shared = json.dumps(v1["result"])
    assert not any(key in shared for key in ('"scores"', '"personal', '"reason_codes"'))


def test_gt_097_backup_changes_nothing_shared(app_flow):
    before = app_flow.view(app_flow.p2)
    observations = _count(app_flow.db, "observations")
    res = app_flow.p2.post(
        f"/api/meals/{app_flow.meal_id}/personal-recommendations/1/action",
        json={"expected_revision": before["revision"], "action": "save_backup"},
    )
    assert res.status_code == 200, res.text
    after = app_flow.view(app_flow.p2)
    assert (
        after["revision"] == before["revision"] and after["status"] == before["status"]
    )
    assert after["result"]["options"] == before["result"]["options"]
    assert _count(app_flow.db, "observations") == observations


def test_gt_098_stale_personal_rejected(app_flow):
    old = app_flow.view(app_flow.p1)
    app_flow.checkin(
        app_flow.p1, app_flow.meal_id, craving="rice", cuisines=[], budget=50
    )
    res = app_flow.p1.post(
        f"/api/meals/{app_flow.meal_id}/personal-recommendations/1/action",
        json={"expected_revision": old["revision"], "action": "save_backup"},
    )
    assert res.status_code == 409, res.text
    assert app_flow.view(app_flow.p1).get("my_personal_recommendations") in ([], None)


def test_gt_099_choose_separately_before_decision(app_flow):
    before = app_flow.view(app_flow.p2)
    res = app_flow.p2.post(
        f"/api/meals/{app_flow.meal_id}/personal-recommendations/1/action",
        json={"expected_revision": before["revision"], "action": "choose_separately"},
    )
    assert res.status_code == 200, res.text
    after = app_flow.view(app_flow.p1)
    assert after["revision"] > before["revision"], (
        "choosing separately must increment the meal revision"
    )
    assert not (after.get("result") or {}).get("options"), (
        "the shortlist must be invalidated"
    )


def test_gt_100_dismiss_teaches_nothing(app_flow):
    view = app_flow.view(app_flow.p1)
    observations = _count(app_flow.db, "observations")
    res = app_flow.p1.post(
        f"/api/meals/{app_flow.meal_id}/personal-recommendations/2/action",
        json={"expected_revision": view["revision"], "action": "dismiss"},
    )
    assert res.status_code == 200, res.text
    assert _count(app_flow.db, "observations") == observations


# ---------------------------------------------------------------- H. majority preference
# Added 7 Oct 2026 (product owner). Expectations from the PRD §9.3 formula:
# group = .60 × mean + .40 × min, base = .85 × group + .075.
def test_gt_101_majority_cuisine(run):
    r = run(
        snapshot(D(cuisines=["Thai"]), D(cuisines=["Thai"]), D(cuisines=["Indian"]))
    )
    assert r.order == ["G06", "G02", "G01"]
    scores(r, {"G06": 0.5076, "G02": 0.4464})


def test_gt_102_majority_flips_the_compromise(run):
    # Contrast with GT-067 (one soup vs one grill → neutral compromise first).
    r = run(snapshot(D(craving="soup"), D(craving="soup"), D(craving="grill")))
    assert r.order == ["G01", "G07", "G06"]
    scores(r, {"G01": 0.5076, "G07": 0.5, "G06": 0.4617})


def test_gt_103_three_of_four(run):
    r = run(
        snapshot(
            D(craving="pasta"),
            D(craving="pasta"),
            D(craving="pasta"),
            D(craving="rice"),
        )
    )
    assert r.order[0] == "G05"
    scores(r, {"G05": 0.5230, "G07": 0.5})


def test_gt_104_majority_cannot_override_floor(run):
    dislikes = {
        "taste_preferences": {
            "cuisine:thai": "dislike",
            "flavour:sour": "dislike",
            "spice:hot": "dislike",
            "flavour:spicy": "dislike",
        }
    }
    r = run(
        snapshot(D(craving="spicy"), D(craving="spicy"), D(dislikes, craving="pasta"))
    )
    assert "G06" not in r.order
    assert r.order == ["G07", "G02", "G05"]
    scores(r, {"G07": 0.5765, "G02": 0.5076, "G05": 0.4464})
