"""Multilingual retrieval evaluation judgment set and benchmarking metrics."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from .catalog import Catalog
from .retrieval import (
    CatalogEmbeddingIndex,
    RetrievalQuery,
    semantic_candidate_search,
)


@dataclass(frozen=True)
class RetrievalJudgmentCase:
    query_id: str
    language: str  # "english", "bahasa_melayu", "mixed"
    query_text: str
    expected_outlets: list[str]
    relevance_grades: dict[str, int]  # outlet_id -> grade (0 to 3)


RETRIEVAL_JUDGMENT_SET: list[RetrievalJudgmentCase] = [
    # English queries
    RetrievalJudgmentCase(
        query_id="en_noodle_soup",
        language="english",
        query_text="comforting warm noodle soup with chicken",
        expected_outlets=["demo-outlet-soup", "demo-outlet-noodle"],
        relevance_grades={"demo-outlet-soup": 3, "demo-outlet-noodle": 2},
    ),
    RetrievalJudgmentCase(
        query_id="en_spicy_rice",
        language="english",
        query_text="spicy fragrant fried rice with sambal",
        expected_outlets=["demo-outlet-spice"],
        relevance_grades={"demo-outlet-spice": 3},
    ),
    # Bahasa Melayu queries
    RetrievalJudgmentCase(
        query_id="bm_nasi_lemak",
        language="bahasa_melayu",
        query_text="teringin nasi lemak panas sambal pedas",
        expected_outlets=["demo-outlet-spice"],
        relevance_grades={"demo-outlet-spice": 3},
    ),
    RetrievalJudgmentCase(
        query_id="bm_sup_mee",
        language="bahasa_melayu",
        query_text="mee sup ayam panas kuah sedap",
        expected_outlets=["demo-outlet-soup", "demo-outlet-noodle"],
        relevance_grades={"demo-outlet-soup": 3, "demo-outlet-noodle": 2},
    ),
    # Mixed Manglish queries
    RetrievalJudgmentCase(
        query_id="manglish_tapau_noodle",
        language="mixed",
        query_text="jom tapau noodle soup not so spicy",
        expected_outlets=["demo-outlet-soup", "demo-outlet-noodle"],
        relevance_grades={"demo-outlet-soup": 3, "demo-outlet-noodle": 2},
    ),
    RetrievalJudgmentCase(
        query_id="manglish_pedas_rice",
        language="mixed",
        query_text="craving nasi goreng pedas gao gao",
        expected_outlets=["demo-outlet-spice"],
        relevance_grades={"demo-outlet-spice": 3},
    ),
    # Specific flavour and dish family
    RetrievalJudgmentCase(
        query_id="flavour_light",
        language="english",
        query_text="something light and savoury not oily",
        expected_outlets=["demo-outlet-soup"],
        relevance_grades={"demo-outlet-soup": 3},
    ),
    # Unsupported query (no match expected)
    RetrievalJudgmentCase(
        query_id="unsupported_exotic",
        language="english",
        query_text="deep dish Chicago pizza and tacos",
        expected_outlets=[],
        relevance_grades={},
    ),
]


def _dcg_at_k(relevances: list[int], k: int) -> float:
    score = 0.0
    for idx, rel in enumerate(relevances[:k]):
        score += (2**rel - 1) / math.log2(idx + 2)
    return score


def _ndcg_at_k(relevances: list[int], ideal_relevances: list[int], k: int) -> float:
    idcg = _dcg_at_k(ideal_relevances, k)
    if idcg <= 0:
        return 1.0 if not relevances or max(relevances) == 0 else 0.0
    dcg = _dcg_at_k(relevances, k)
    return dcg / idcg


def evaluate_retrieval(
    catalog: Catalog,
    index: CatalogEmbeddingIndex | None = None,
    cases: list[RetrievalJudgmentCase] | None = None,
) -> dict[str, Any]:
    """Run retrieval benchmarking on the multilingual judgment set."""
    judgment_cases = cases or RETRIEVAL_JUDGMENT_SET
    idx = index or CatalogEmbeddingIndex.build(catalog)

    total_cases = len(judgment_cases)
    recall_10_list = []
    ndcg_10_list = []
    unique_outlets_retrieved = set()
    total_latency_ms = 0.0
    languages = {"english": [], "bahasa_melayu": [], "mixed": []}

    for case in judgment_cases:
        query = RetrievalQuery(query_id=case.query_id, text=case.query_text, top_k=10)
        start = time.monotonic()
        res = semantic_candidate_search(index=idx, catalog=catalog, queries=[query])
        elapsed = (time.monotonic() - start) * 1000
        total_latency_ms += elapsed

        retrieved_ids = [c["outlet_id"] for c in res["candidates"]]
        unique_outlets_retrieved.update(retrieved_ids)

        expected = set(case.expected_outlets)
        if expected:
            hits = len(set(retrieved_ids[:10]) & expected)
            r10 = hits / len(expected)
        else:
            r10 = 1.0 if not retrieved_ids else 0.5
        recall_10_list.append(r10)

        # NDCG calculation
        relevances = [case.relevance_grades.get(oid, 0) for oid in retrieved_ids]
        ideal = sorted(case.relevance_grades.values(), reverse=True)
        ndcg_val = _ndcg_at_k(relevances, ideal, k=10)
        ndcg_10_list.append(ndcg_val)

        if case.language in languages:
            languages[case.language].append(r10)

    mean_recall_10 = sum(recall_10_list) / total_cases if total_cases > 0 else 0.0
    mean_ndcg_10 = sum(ndcg_10_list) / total_cases if total_cases > 0 else 0.0
    avg_latency = total_latency_ms / total_cases if total_cases > 0 else 0.0

    return {
        "catalog_id": catalog.catalog_id,
        "total_cases": total_cases,
        "mean_recall_at_10": round(mean_recall_10, 4),
        "mean_ndcg_at_10": round(mean_ndcg_10, 4),
        "unique_outlets_retrieved": len(unique_outlets_retrieved),
        "avg_latency_ms": round(avg_latency, 2),
        "language_recall": {
            lang: round(sum(scores) / len(scores), 4) if scores else 0.0
            for lang, scores in languages.items()
        },
    }
