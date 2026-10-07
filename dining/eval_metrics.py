"""Offline ranking metrics for labelled recommendation scenarios (PRD §15.3).

Relevance labels are graded: 2 = ideal for the group, 1 = acceptable, 0/absent = not
relevant. "Not relevant" means not judged relevant for that scenario; an unchosen or
unlabelled outlet is never treated as a negative preference signal.
"""

from __future__ import annotations

import math
from statistics import mean


def precision_at_k(ranked: list[str], relevance: dict[str, int], k: int = 3) -> float:
    top = ranked[:k]
    return sum(1 for o in top if relevance.get(o, 0) > 0) / k


def recall_at_k(
    ranked: list[str], relevance: dict[str, int], k: int = 3
) -> float | None:
    relevant = [o for o, grade in relevance.items() if grade > 0]
    if not relevant:
        return None
    # A shortlist holds at most k outlets, so recall is measured against what can fit.
    return sum(1 for o in ranked[:k] if relevance.get(o, 0) > 0) / min(k, len(relevant))


def dcg(gains: list[int]) -> float:
    return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg_at_k(ranked: list[str], relevance: dict[str, int], k: int = 3) -> float | None:
    """None when the ideal gain is zero (PRD: report those scenarios separately)."""
    ideal = dcg(sorted((g for g in relevance.values() if g > 0), reverse=True)[:k])
    if ideal == 0:
        return None
    return dcg([relevance.get(o, 0) for o in ranked[:k]]) / ideal


def reciprocal_rank(ranked: list[str], relevance: dict[str, int]) -> float:
    for index, outlet in enumerate(ranked, 1):
        if relevance.get(outlet, 0) > 0:
            return 1 / index
    return 0.0


def top1_hit(ranked: list[str], relevance: dict[str, int]) -> float:
    """1 when #1 has the best grade in the labels (e.g. an ideal outlet), else 0."""
    if not ranked or not relevance:
        return 0.0
    return float(relevance.get(ranked[0], 0) == max(relevance.values()) > 0)


def evaluate(ranked: list[str], relevance: dict[str, int], k: int = 3) -> dict:
    return {
        "precision": precision_at_k(ranked, relevance, k),
        "recall": recall_at_k(ranked, relevance, k),
        "ndcg": ndcg_at_k(ranked, relevance, k),
        "mrr": reciprocal_rank(ranked, relevance),
        "top1": top1_hit(ranked, relevance),
    }


def summarise(rows: list[dict]) -> dict:
    """Mean of each metric over the scenarios where it is defined."""
    keys = ("precision", "recall", "ndcg", "mrr", "top1")
    out = {}
    for key in keys:
        values = [r[key] for r in rows if r.get(key) is not None]
        out[key] = round(mean(values), 4) if values else None
        out[f"{key}_n"] = len(values)
    return out
