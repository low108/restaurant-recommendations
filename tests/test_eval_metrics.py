import math

import pytest

from dining.eval_metrics import (
    evaluate,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    summarise,
    top1_hit,
)


def test_perfect_ranking_scores_one():
    rel = {"a": 2, "b": 1}
    assert ndcg_at_k(["a", "b", "c"], rel) == pytest.approx(1.0)
    assert recall_at_k(["a", "b", "c"], rel) == 1.0
    assert reciprocal_rank(["a", "b"], rel) == 1.0
    assert top1_hit(["a", "b"], rel) == 1.0


def test_swapped_grades_lower_ndcg_but_not_recall():
    rel = {"a": 2, "b": 1}
    swapped = ndcg_at_k(["b", "a"], rel)
    expected = ((2**1 - 1) + (2**2 - 1) / math.log2(3)) / (
        (2**2 - 1) + (2**1 - 1) / math.log2(3)
    )
    assert swapped == pytest.approx(expected)
    assert recall_at_k(["b", "a"], rel) == 1.0
    assert top1_hit(["b", "a"], rel) == 0.0


def test_precision_counts_all_k_slots_and_recall_caps_at_k():
    rel = {"a": 1, "b": 1, "c": 1, "d": 1}
    assert precision_at_k(["a", "x", "y"], rel, k=3) == pytest.approx(1 / 3)
    assert (
        recall_at_k(["a", "b", "c"], rel, k=3) == 1.0
    )  # only 3 can fit in a shortlist


def test_no_relevant_outlets_is_undefined_not_zero():
    assert ndcg_at_k(["a"], {}) is None
    assert recall_at_k(["a"], {}) is None
    row = evaluate(["a"], {})
    assert row["ndcg"] is None and row["recall"] is None and row["mrr"] == 0.0


def test_summary_averages_only_defined_values():
    rows = [evaluate(["a"], {"a": 2}), evaluate(["x"], {})]
    summary = summarise(rows)
    assert summary["ndcg"] == 1.0 and summary["ndcg_n"] == 1
    assert summary["mrr"] == 0.5 and summary["mrr_n"] == 2
