"""Scoring policies: how one diner's fit with one dish is calculated (Strategy pattern).

The recommender only needs "give me this diner's fit for this dish". Each policy answers
that differently, so the pipeline never branches on which policy is active:

* :class:`PrdFitPolicy` (``prd-fit-v1``, default) – the PRD §9 baseline with rule-based
  match levels for craving and usual taste.
* :class:`ContentSimilarityPolicy` (``content-sim-v1``, opt-in) – TF-IDF feature vectors
  and cosine similarity for craving and usual taste (see ``dining.recommendation.content_similarity``).

Both return a :class:`dining.recommendation.ranking.ItemFit`, so group aggregation, the fairness floor and
everything downstream stay identical.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from dining.catalog.models import Catalog, MenuItem
from dining.recommendation.content_similarity import POLICY as CONTENT_POLICY
from dining.recommendation.content_similarity import FeatureSpace, score_item_content
from dining.recommendation.ranking import POLICY_VERSION, ItemFit, score_item


class ScoringPolicy(Protocol):
    """Interface every scoring policy implements."""

    name: str

    def score(
        self,
        item: MenuItem,
        person: dict,
        *,
        outlet_id: str,
        at: datetime,
        route: dict | None,
    ) -> ItemFit: ...


class PrdFitPolicy:
    """PRD §9 baseline: fixed match levels (exact 1.0, related 0.7, neutral 0.5, mismatch 0.2)."""

    name = POLICY_VERSION

    def score(self, item, person, *, outlet_id, at, route):
        return score_item(
            item,
            person["profile"],
            person["response"],
            outlet_id=outlet_id,
            at=at,
            observations=person.get("observations", []),
            venue_preferences=person.get("venue_preferences", []),
            attribute_preferences=person.get("attribute_preferences", []),
            route=route,
        )


class ContentSimilarityPolicy:
    """TF-IDF + cosine similarity for craving and usual taste.

    The feature space (IDF over the whole catalog) is built lazily on first use and then
    reused, because it only depends on the catalog.
    """

    name = CONTENT_POLICY

    def __init__(self, catalog: Catalog):
        self._catalog = catalog
        self._space: FeatureSpace | None = None
        self._items: dict[str, MenuItem] | None = None

    @property
    def space(self) -> FeatureSpace:
        if self._space is None:
            self._space = FeatureSpace(self._catalog)
        return self._space

    @property
    def items_by_id(self) -> dict[str, MenuItem]:
        if self._items is None:
            self._items = {i.item_id: i for i in self._catalog.menu_items}
        return self._items

    def score(self, item, person, *, outlet_id, at, route):
        return score_item_content(
            item,
            person["profile"],
            person["response"],
            space=self.space,
            catalog_items=self.items_by_id,
            outlet_id=outlet_id,
            at=at,
            observations=person.get("observations", []),
            attribute_preferences=person.get("attribute_preferences", []),
            route=route,
        )


AVAILABLE_POLICIES = (PrdFitPolicy.name, ContentSimilarityPolicy.name)


def create_scoring_policy(name: str, catalog: Catalog) -> ScoringPolicy:
    """Factory: build the policy registered under ``name``."""
    if name == PrdFitPolicy.name:
        return PrdFitPolicy()
    if name == ContentSimilarityPolicy.name:
        return ContentSimilarityPolicy(catalog)
    raise ValueError(f"Unknown scoring policy: {name}")
