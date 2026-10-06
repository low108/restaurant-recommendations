"""Rights-aware catalog embedding index and hybrid candidate search."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .catalog import Catalog

RETRIEVAL_POLICY_VERSION = "retrieval-policy-v1"
EMBEDDING_MODEL_NAME = "deterministic-hash-v1"
VECTOR_DIM = 64

SENSITIVE_QUERY_PATTERNS = re.compile(
    r"\b(?:allerg\w*|alahan|anaphyla\w*|halal|haram|pork|babi|kacang|peanuts?|shellfish|gluten|dairy|gps|latitude|longitude|sk-[\w-]+|password|secret|token)\b|\d{1,3}\.\d{3,}",
    re.IGNORECASE,
)


def sanitize_retrieval_text(text: str) -> str:
    """Strip private sentinels, credentials, coordinates and medical terms."""
    if not text:
        return ""
    cleaned = SENSITIVE_QUERY_PATTERNS.sub(" ", text)
    return " ".join(cleaned.split())


@dataclass(frozen=True)
class RetrievalQuery:
    query_id: str
    text: str
    top_k: int = 20

    @property
    def sanitized_text(self) -> str:
        return sanitize_retrieval_text(self.text)


class DeterministicEmbedder:
    """Lightweight, deterministic feature hashing embedder with zero external dependencies."""

    def __init__(self, dim: int = VECTOR_DIM):
        self.dim = dim

    def embed_text(self, text: str) -> list[float]:
        tokens = re.findall(r"\w+", text.casefold())
        if not tokens:
            return [0.0] * self.dim

        vec = [0.0] * self.dim
        # Subword and word n-grams for multilingual resilience (English, BM, Manglish)
        features = list(tokens)
        for t in tokens:
            if len(t) >= 4:
                features.append(t[:4])
                features.append(t[-4:])

        for feat in features:
            h = int(hashlib.md5(feat.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dim
            val = 1.0 if ((h >> 8) & 1) else -1.0
            vec[idx] += val

        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            return [x / norm for x in vec]
        return vec

    def similarity(self, vec_a: list[float], vec_b: list[float]) -> float:
        dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
        return max(0.0, min(1.0, (dot + 1.0) / 2.0))


@dataclass(frozen=True)
class IndexedItem:
    item_id: str
    outlet_id: str
    name: str
    text: str
    vector: list[float]
    attributes: list[str]
    cuisine_tags: list[str]


@dataclass
class CatalogEmbeddingIndex:
    catalog_id: str
    catalog_version: str
    model_name: str
    index_policy_version: str
    created_at: str
    indexed_items: list[IndexedItem] = field(default_factory=list)
    _embedder: DeterministicEmbedder = field(default_factory=DeterministicEmbedder)

    @classmethod
    def build(
        cls,
        catalog: Catalog,
        embedder: DeterministicEmbedder | None = None,
        at: datetime | None = None,
    ) -> CatalogEmbeddingIndex:
        at_time = at or datetime.now(timezone.utc)
        emb = embedder or DeterministicEmbedder()

        # Step 1: Identify outlets whose sources have rights.embed == "allowed" and are not expired
        usable_outlet_ids: set[str] = set()
        for outlet in catalog.outlets:
            if catalog.evidence_usable(outlet.source_ids, purpose="embed", at=at_time):
                usable_outlet_ids.add(outlet.outlet_id)

        # Step 2: Index menu items for usable outlets where item sources also permit embedding
        indexed: list[IndexedItem] = []
        for item in catalog.menu_items:
            if item.outlet_id not in usable_outlet_ids:
                continue
            if not catalog.evidence_usable(
                item.source_ids, purpose="embed", at=at_time
            ):
                continue

            text_rep = (
                f"{item.name} {' '.join(item.attributes)} {' '.join(item.cuisine_tags)}"
            )
            vec = emb.embed_text(text_rep)
            indexed.append(
                IndexedItem(
                    item_id=item.item_id,
                    outlet_id=item.outlet_id,
                    name=item.name,
                    text=text_rep,
                    vector=vec,
                    attributes=list(item.attributes),
                    cuisine_tags=list(item.cuisine_tags),
                )
            )

        return cls(
            catalog_id=catalog.catalog_id,
            catalog_version=catalog.version,
            model_name=EMBEDDING_MODEL_NAME,
            index_policy_version=RETRIEVAL_POLICY_VERSION,
            created_at=at_time.isoformat(),
            indexed_items=indexed,
            _embedder=emb,
        )

    def is_usable_for_catalog(self, catalog: Catalog) -> bool:
        return (
            self.catalog_id == catalog.catalog_id
            and self.catalog_version == catalog.version
        )

    def indexed_outlet_ids(self) -> set[str]:
        return {item.outlet_id for item in self.indexed_items}


def _fallback_structured_search(
    catalog: Catalog,
    queries: list[RetrievalQuery],
    maximum_outlets: int = 30,
    maximum_items_per_outlet: int = 2,
) -> dict[str, Any]:
    """Fallback to transparent structured candidate retrieval when embeddings unavailable."""
    outlet_candidates = []
    seen: set[str] = set()

    all_query_terms = set()
    for q in queries:
        all_query_terms.update(q.sanitized_text.casefold().split())

    for outlet in catalog.outlets:
        if outlet.outlet_id in seen:
            continue
        items = [i for i in catalog.menu_items if i.outlet_id == outlet.outlet_id]
        if not items:
            continue

        rep_items = [i.item_id for i in items[:maximum_items_per_outlet]]
        outlet_candidates.append(
            {
                "outlet_id": outlet.outlet_id,
                "semantic_score": 0.5,
                "recall_score": 0.5,
                "representative_item_ids": rep_items,
            }
        )
        seen.add(outlet.outlet_id)
        if len(outlet_candidates) >= maximum_outlets:
            break

    return {
        "status": "fallback",
        "fallback_reason": "index_unavailable",
        "policy_version": RETRIEVAL_POLICY_VERSION,
        "candidates": outlet_candidates,
    }


def semantic_candidate_search(
    index: CatalogEmbeddingIndex | None,
    catalog: Catalog,
    queries: list[RetrievalQuery],
    maximum_outlets: int = 30,
    maximum_items_per_outlet: int = 2,
) -> dict[str, Any]:
    """Retrieve unique outlet candidates through hybrid semantic and structured matching."""
    if index is None or not index.is_usable_for_catalog(catalog):
        return _fallback_structured_search(
            catalog, queries, maximum_outlets, maximum_items_per_outlet
        )

    embedder = index._embedder
    outlet_similarities: dict[str, list[tuple[float, str, set[str], set[str]]]] = {}

    for q in queries:
        query_text = q.sanitized_text
        if not query_text:
            continue
        q_vec = embedder.embed_text(query_text)
        q_terms = set(query_text.casefold().split())

        for item in index.indexed_items:
            sim = embedder.similarity(q_vec, item.vector)
            if item.outlet_id not in outlet_similarities:
                outlet_similarities[item.outlet_id] = []
            outlet_similarities[item.outlet_id].append(
                (
                    sim,
                    item.item_id,
                    item.name.casefold().strip(),
                    set(item.attributes),
                    q_terms,
                )
            )

    if not outlet_similarities:
        return _fallback_structured_search(
            catalog, queries, maximum_outlets, maximum_items_per_outlet
        )

    outlet_scores: list[dict[str, Any]] = []

    for outlet_id, item_records in outlet_similarities.items():
        # Deduplicate identical menu items per outlet so duplicate records cannot inflate score
        distinct_by_name: dict[str, tuple[float, str, str, set[str], set[str]]] = {}
        for rec in item_records:
            name_key = rec[2]
            if (
                name_key not in distinct_by_name
                or rec[0] > distinct_by_name[name_key][0]
            ):
                distinct_by_name[name_key] = rec

        distinct_records = list(distinct_by_name.values())
        distinct_records.sort(key=lambda x: -x[0])

        # Enforce per-outlet aggregation rule: mean of top 2 distinct menu-item similarities
        top_sims = [rec[0] for rec in distinct_records[:2]]
        semantic_score = sum(top_sims) / len(top_sims) if top_sims else 0.0

        # Structured tag match and keyword match
        best_tags = distinct_records[0][3]
        query_terms = distinct_records[0][4]
        tag_match = (
            len(best_tags & query_terms) / max(1, len(query_terms))
            if query_terms
            else 0.0
        )
        tag_match = min(1.0, tag_match)

        # Hybrid fusion formula
        recall_score = (
            0.55 * semantic_score + 0.30 * tag_match + 0.15 * min(1.0, semantic_score)
        )

        # Select unique representative item IDs (capped at maximum_items_per_outlet)
        rep_ids: list[str] = []
        for rec in distinct_records:
            if rec[1] not in rep_ids:
                rep_ids.append(rec[1])
            if len(rep_ids) >= maximum_items_per_outlet:
                break

        outlet_scores.append(
            {
                "outlet_id": outlet_id,
                "semantic_score": round(semantic_score, 4),
                "recall_score": round(recall_score, 4),
                "representative_item_ids": rep_ids,
            }
        )

    # Sort candidates by recall score descending, limit to maximum_outlets
    outlet_scores.sort(key=lambda c: (-c["recall_score"], c["outlet_id"]))
    selected_candidates = outlet_scores[:maximum_outlets]

    return {
        "status": "ok",
        "policy_version": RETRIEVAL_POLICY_VERSION,
        "model_name": index.model_name,
        "candidates": selected_candidates,
    }
