#!/usr/bin/env python3
"""Offline ranking evaluation on the labelled production golden scenarios (PRD §15.3).

Labels and scenarios come from tests/golden/test_golden_production.py: each case's expected
outlets become graded relevance (★ must-be-#1 → 2, required/allowed → 1; for mixed-group
"must include" sets every outlet → 2). Four policies are compared on the *same* eligible pool:

  nearest        eligible outlets by distance (PRD baseline "nearest eligible")
  mean_fit       eligible outlets by average individual fit only (no least-misery term)
  prd_blend      the production ranker, prd-fit-v1 (.6 mean + .4 min, floor, diversity, coverage)
  content_sim    the same ranker with content-sim-v1 (TF-IDF cosine for craving/usual taste)

Usage:
    HF_HUB_OFFLINE=1 .venv/bin/python scripts/evaluate_ranking.py \
        --out var/eval/ranking-eval.json --labels-out data/eval/production-relevance.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "golden"))

import dining.recommendation as rec_mod
from dining.catalog import load_catalog
from dining.content_similarity import POLICY as CONTENT
from dining.eval_metrics import evaluate, summarise
from dining.ranking import POLICY_VERSION
from dining.retrieval import load_persistent_index

# Ranking-quality cases only (hard-gate, privacy and lifecycle cases are pass/fail elsewhere).
CASES = [f"{n:03d}" for n in [*range(1, 43), 71, 72, 73, 76, 80, *range(101, 107)]]
EXTRA_LABELS = {  # outlets a case's free-form assertion accepts, which the helpers can't capture
    "035": {"BARBER": 1, "BT": 1},
    "042": {"WMK": 1, "TCH": 1},
}


class _Dummy:
    """Stand-in result: free-form assertions in a test may fail; labels are recorded first."""

    def __init__(self):
        self.order: list = []
        self.verification: set = set()
        self.status = "shortlisted"
        self.result = {
            "options": [],
            "verification": [],
            "retrieval_status": "semantic",
        }

    def personal(self, n):
        return []


def capture_scenarios(golden):
    """Run each golden test with assertion helpers swapped for label recorders."""
    captured = {}
    state = {}

    def go(
        env, mp, radius, at="Thu 12:30", *people, dur=60, index=True, confirmations=None
    ):
        lat, lng = golden.MP[mp]
        snap = {
            "revision": 1,
            "meal": {
                "id": "eval",
                "meal_at": golden.when(at).isoformat(),
                "duration_minutes": dur,
                "latitude": lat,
                "longitude": lng,
                "radius_km": radius,
            },
            "participants": [dict(p, user_id=f"P{n}") for n, p in enumerate(people, 1)],
        }
        if confirmations is not None:
            snap["preparation_confirmations"] = confirmations
        state["snaps"].append(snap)
        return _Dummy()

    def grade(keys, value):
        for key in keys:
            state["labels"][key] = max(state["labels"].get(key, 0), value)

    def top1(r, *keys):
        grade(keys, 2)

    def include(r, *keys):
        state["include"].extend(keys)
        grade(keys, 1)

    def subset(r, *keys):
        grade(keys, 1)

    patches = {
        "go": go,
        "top1": top1,
        "include": include,
        "subset": subset,
        "exclude": lambda r, *k: None,
        "personal_ok": lambda *a, **k: None,
        "_blocked": lambda *a, **k: None,
    }
    saved = {name: getattr(golden, name) for name in patches}
    for name, fn in patches.items():
        setattr(golden, name, fn)
    try:
        for case in CASES:
            state.update(snaps=[], labels={}, include=[])
            try:
                getattr(golden, f"test_p_{case}")(None)
            except (AssertionError, IndexError, KeyError):
                pass  # free-form assertions on the dummy result; labels are already recorded
            labels = dict(state["labels"])
            if state["include"] and not any(v == 2 for v in labels.values()):
                labels = {
                    k: 2 for k in labels
                }  # mixed-group "must include" sets: each serves someone
            for key, value in EXTRA_LABELS.get(case, {}).items():
                labels[key] = max(labels.get(key, 0), value)
            if len(state["snaps"]) == 1 and labels:
                captured[f"P-{case}"] = {
                    "snapshot": state["snaps"][0],
                    "relevance": {golden.O[k]: v for k, v in labels.items()},
                }
    finally:
        for name, fn in saved.items():
            setattr(golden, name, fn)
    return captured


def rank_all(catalog, index, snapshot):
    """Return the four rankings for one scenario, on the same eligible pool."""
    pool = []
    original = rec_mod.rank_diverse

    def spy(candidates, **kwargs):
        pool[:] = [dict(c) for c in candidates]
        return original(candidates, **kwargs)

    rec_mod.rank_diverse = spy
    try:
        prd = rec_mod.Recommender(catalog, embedding_index=index)(
            json.loads(json.dumps(snapshot))
        )
        prd_pool = list(pool)
        content = rec_mod.Recommender(
            catalog, embedding_index=index, scoring_policy=CONTENT
        )(json.loads(json.dumps(snapshot)))
    finally:
        rec_mod.rank_diverse = original
    nearest = [
        c["outlet_id"]
        for c in sorted(prd_pool, key=lambda c: (c["distance_km"], c["outlet_id"]))
    ]
    mean_fit = [
        c["outlet_id"]
        for c in sorted(
            prd_pool,
            key=lambda c: (-mean(c["_fits"]), c["distance_km"], c["outlet_id"]),
        )
    ]
    return {
        "nearest": nearest[:3],
        "mean_fit": mean_fit[:3],
        "prd_blend": [o["outlet_id"] for o in prd["options"]],
        "content_sim": [o["outlet_id"] for o in content["options"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", type=Path, default=ROOT / "var/eval/ranking-eval.json")
    parser.add_argument(
        "--labels-out", type=Path, default=ROOT / "data/eval/production-relevance.json"
    )
    args = parser.parse_args()

    import test_golden_production as golden

    catalog = load_catalog(golden.CATALOG_DIR / "catalog.validated.json")
    index = load_persistent_index(golden.INDEX_DIR, catalog=catalog)
    if index is None:
        print("Active index does not match the production catalog.", file=sys.stderr)
        return 1
    scenarios = capture_scenarios(golden)

    per_case, by_policy = {}, {}
    for case, spec in scenarios.items():
        rankings = rank_all(catalog, index, spec["snapshot"])
        per_case[case] = {
            "relevance": spec["relevance"],
            "rankings": rankings,
            "metrics": {},
        }
        for policy, ranked in rankings.items():
            metrics = evaluate(ranked, spec["relevance"], k=3)
            per_case[case]["metrics"][policy] = metrics
            by_policy.setdefault(policy, []).append(metrics)
    summary = {policy: summarise(rows) for policy, rows in by_policy.items()}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "catalog_version": catalog.version,
                "policies": {"prd_blend": POLICY_VERSION, "content_sim": CONTENT},
                "scenarios": len(per_case),
                "summary": summary,
                "cases": per_case,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    args.labels_out.parent.mkdir(parents=True, exist_ok=True)
    args.labels_out.write_text(
        json.dumps(
            {
                "label_version": "production-relevance-v1",
                "source": "tests/golden/test_golden_production.py expectations (★ → 2, required/allowed → 1)",
                "catalog_version": catalog.version,
                "cases": {
                    c: {"snapshot": s["snapshot"], "relevance": s["relevance"]}
                    for c, s in scenarios.items()
                },
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"{len(per_case)} labelled scenarios · catalog {catalog.version}")
    print(f"{'policy':<12} {'P@3':>6} {'R@3':>6} {'NDCG@3':>7} {'MRR':>6} {'Top-1':>6}")
    for policy in ("nearest", "mean_fit", "prd_blend", "content_sim"):
        s = summary[policy]
        print(
            f"{policy:<12} {s['precision']:>6.3f} {s['recall']:>6.3f} {s['ndcg']:>7.3f} {s['mrr']:>6.3f} {s['top1']:>6.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
