"""Content-based similarity policy (``content-sim-v1``): TF-IDF feature vectors + cosine.

An alternative to the PRD baseline's rule-based craving (C) and lasting-taste (H) features.
Hard checks, travel (T), budget (B), occasion (O), group aggregation and the fairness floor
are unchanged; only C and H are computed as real similarity metrics:

* item vector: cuisine, curated taste tags and words from the dish's reviewed English/Malay
  translations, weighted by inverse document frequency over the catalog's reviewed dishes;
* today's vector: the diner's craving (tags + words) and today's cuisines;
* lasting vector: saved cuisine/tag likes (+) and dislikes (−), plus consented history of
  specific dishes (Rocchio-style: enjoyed adds the dish's vector, disliked subtracts it).

C = 0.2 + 0.8 × cos(today, item)       (graded; 0.2 = known mismatch, like the PRD)
H = 0.5 + 0.5 × cos(lasting, item)     (signed: dislikes pull below neutral)
Unknown inputs stay at the PRD's neutral 0.5 with zero coverage.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime

from dining.catalog import Catalog, MenuItem
from dining.ranking import (
    DIMENSIONS,
    NEGATED_PHRASE,
    NEUTRAL_WORDS,
    UNKNOWN,
    WEIGHTS,
    Feature,
    ItemFit,
    _canonical,
    _current_dimensions,
    _event_time,
    _occasion,
    _outcome,
    _spending,
    _travel,
)

POLICY = "content-sim-v1"
TAG_WEIGHT = 1.0
CUISINE_WEIGHT = 1.0
WORD_WEIGHT = 0.5
STOPWORDS = frozenset(
    {
        "and",
        "with",
        "the",
        "set",
        "regular",
        "small",
        "medium",
        "large",
        "per",
        "pax",
        "piece",
        "pcs",
        "dengan",
        "dan",
    }
)
LATIN = re.compile(r"[a-zÀ-ɏ]{3,}")


def _words(text: str) -> set[str]:
    text = NEGATED_PHRASE.sub(" ", text.casefold())
    return {
        _canonical(w[:-1] if len(w) > 3 and w.endswith("s") else w)
        for w in LATIN.findall(text)
        if w not in STOPWORDS and w not in NEUTRAL_WORDS
    }


def _tag_feature(tag: str) -> str | None:
    tag = _canonical(tag)
    for dimension, tags in DIMENSIONS.items():
        if tag in tags:
            return f"tag:{dimension}:{tag}"
    return None


def raw_item_features(
    item: MenuItem, outlet_cuisines: tuple[str, ...] = ()
) -> dict[str, float]:
    features: dict[str, float] = {}
    for cuisine in item.cuisine_tags or outlet_cuisines:
        features[f"cuisine:{cuisine.casefold()}"] = CUISINE_WEIGHT
    for attribute in item.attributes:
        if (name := _tag_feature(attribute)) is not None:
            features[name] = TAG_WEIGHT
    text = " ".join(
        [
            item.name,
            *item.name_translations.get("en", ()),
            *item.name_translations.get("ms", ()),
        ]
    )
    for word in _words(text):
        features.setdefault(f"word:{word}", WORD_WEIGHT)
    return features


class FeatureSpace:
    """IDF-weighted item vectors for one catalog (built once, read-only)."""

    def __init__(self, catalog: Catalog):
        cuisines = {o.outlet_id: tuple(o.cuisine_tags) for o in catalog.outlets}
        items = [i for i in catalog.menu_items if i.review_status == "reviewed"]
        raw = {
            i.item_id: raw_item_features(i, cuisines.get(i.outlet_id, ()))
            for i in items
        }
        df = Counter(feature for vector in raw.values() for feature in vector)
        n = max(1, len(raw))
        self.idf = {f: math.log((n + 1) / (count + 1)) + 1.0 for f, count in df.items()}
        self.vectors = {
            item_id: {f: w * self.idf[f] for f, w in vector.items()}
            for item_id, vector in raw.items()
        }

    def weight(self, feature: str) -> float:
        # Unseen features get the maximum IDF (they are as rare as possible).
        return self.idf.get(feature, max(self.idf.values(), default=1.0))

    def item(self, item: MenuItem) -> dict[str, float]:
        return self.vectors.get(item.item_id, {})


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(value * b.get(key, 0.0) for key, value in a.items())
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(
        sum(v * v for v in b.values())
    )
    return dot / norm if norm else 0.0


def today_vector(space: FeatureSpace, response: dict) -> dict[str, float]:
    vector: dict[str, float] = {}
    for dimension, tags in _current_dimensions(response).items():
        for tag in tags:
            name = (
                f"cuisine:{tag}" if dimension == "cuisine" else f"tag:{dimension}:{tag}"
            )
            vector[name] = (
                CUISINE_WEIGHT if dimension == "cuisine" else TAG_WEIGHT
            ) * space.weight(name)
    if response.get("taste_input_mode") != "structured":
        for word in _words(response.get("craving") or ""):
            name = f"word:{word}"
            vector.setdefault(name, WORD_WEIGHT * space.weight(name))
    if response.get("appetite") in DIMENSIONS["portion"]:
        name = f"tag:portion:{response['appetite']}"
        vector[name] = 0.5 * space.weight(name)
    return vector


def lasting_vector(
    space: FeatureSpace,
    catalog_items: dict[str, MenuItem],
    profile: dict,
    observations,
    at: datetime,
) -> dict[str, float]:
    vector: dict[str, float] = {}
    current = {}

    def add(name: str, amount: float) -> None:
        vector[name] = vector.get(name, 0.0) + amount * space.weight(name)

    for cuisine in profile.get("cuisines", []):
        add(f"cuisine:{cuisine.casefold()}", 1.0)
    if profile.get("spice") in DIMENSIONS["spice"]:
        add(f"tag:spice:{profile['spice']}", 1.0)
    for key, value in profile.get("taste_preferences", {}).items():
        sign = {"like": 1.0, "neutral": 0.0, "dislike": -1.0}.get(value)
        if sign is None or not isinstance(key, str):
            continue
        dimension, _, tag = key.casefold().partition(":")
        name = (
            f"cuisine:{tag}"
            if dimension == "cuisine"
            else _tag_feature(tag or dimension)
        )
        if name and sign:
            add(name, sign)
    if profile.get("memory_enabled"):
        # Rocchio-style history: only consented, confirmed visits that name a specific dish.
        for event in observations:
            item = catalog_items.get(event.get("item_id", ""))
            when = _event_time(event, at)
            outcome = _outcome(event)
            if (
                item is None
                or when is None
                or outcome is None
                or event.get("source") != "confirmed_visit"
            ):
                continue
            decay = math.exp(-math.log(2) * (at - when).total_seconds() / 86400 / 90)
            signed = (outcome - 0.5) * 2 * decay
            for name, weight in space.item(item).items():
                current[name] = current.get(name, 0.0) + signed * weight
        for name, weight in current.items():
            vector[name] = vector.get(name, 0.0) + weight
    return {k: v for k, v in vector.items() if v}


def score_item_content(
    item: MenuItem,
    profile: dict,
    response: dict,
    *,
    space: FeatureSpace,
    catalog_items: dict[str, MenuItem],
    outlet_id: str,
    at: datetime,
    observations=(),
    attribute_preferences=(),
    route: dict | None = None,
) -> ItemFit:
    item_vector = space.item(item)
    wish = today_vector(space, response)
    if wish and item_vector:
        craving = Feature(0.2 + 0.8 * max(0.0, cosine(wish, item_vector)), 1)
    elif (
        not wish and (response.get("craving") or "").casefold().strip() in NEUTRAL_WORDS
    ):
        craving = Feature(0.5, 1)  # "Anything" is an answer, not missing data.
    else:
        craving = UNKNOWN
    lasting = lasting_vector(space, catalog_items, profile, observations, at)
    usual = (
        Feature(min(1.0, max(0.0, 0.5 + 0.5 * cosine(lasting, item_vector))), 1)
        if lasting and item_vector
        else UNKNOWN
    )
    features = {
        "C": craving,
        "H": usual,
        "T": _travel(response, route, at),
        "B": _spending(item, response, profile, attribute_preferences),
        "O": _occasion(item, response),
    }
    return ItemFit(
        sum(WEIGHTS[k] * f.value for k, f in features.items()),
        sum(WEIGHTS[k] * f.coverage for k, f in features.items()),
        features,
    )
