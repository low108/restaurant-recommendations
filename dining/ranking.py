"""Versioned PRD baseline: explicit soft fit, neutral missingness and private features.

These are transparent starting weights, not a trained probability or safety score.
The recommender calls this module only after its independent hard checks pass.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean

from dining.catalog import MenuItem

POLICY_VERSION = "prd-fit-v1"
FEATURE_VERSION = "prd-features-v2"
ONTOLOGY_VERSION = "dining-tags-v1"
WEIGHTS = {"C": 0.45, "H": 0.20, "T": 0.15, "B": 0.10, "O": 0.10}
MINIMUM_INDIVIDUAL_FIT = 0.35
LOW_COVERAGE_THRESHOLD = 0.60
DIVERSITY_MARGIN = 0.10
BRANCH_DISTANCE_BENEFIT_KM = 1.0

# Only explicit catalog attributes contribute: arbitrary menu prose is never
# interpreted as evidence. Aliases collapse repeated synonyms into one dimension.
ALIASES = {
    "sup": "soup",
    "soupy": "soup",
    "soups": "soup",
    "noodle": "noodles",
    "mi": "noodles",
    "mee": "noodles",
    "nasi": "rice",
    "pedas": "spicy",
    "ringan": "light",
    "grilled": "grill",
    "barbecue": "grill",
    "bbq": "grill",
    "no chilli": "none",
    "no chili": "none",
    # Local dish names mapped to the curated dish families used by catalog tags.
    "ramen": "noodle_soup",
    "laksa": "noodle_soup",
    "pho": "noodle_soup",
    "biryani": "rice",
    "briyani": "rice",
    "beriani": "rice",
}
DIMENSIONS = {
    "dish": {
        "soup",
        "noodles",
        "rice",
        "pasta",
        "pizza",
        "salad",
        "sandwich",
        "grill",
        "noodle_soup",
    },
    "flavour": {"rich", "light", "smoky", "sweet", "sour", "savoury", "spicy"},
    "spice": {"none", "mild", "medium", "hot"},
    "portion": {"light", "regular", "hearty"},
}
OCCASIONS = {"quick", "quiet", "indoor"}
NEUTRAL_WORDS = {"anything", "any", "open", "no preference", "whatever"}
# A drink, side, dessert or add-on is never someone's meal (product decision, 7 Oct 2026).
NON_MEAL_ROLES = frozenset({"drink", "beverage", "dessert", "side", "add_on"})
CONFIRMED_MEAL_ROLES = frozenset({"main", "set"})
# Multi-word dish families map to one curated tag before tokenizing.
PHRASES = (
    (re.compile(r"\b(?:noodles? soups?|soup noodles?)\b"), "noodle_soup"),
    (re.compile(r"\b(?:curry (?:mee|mi|noodles?)|(?:mee|mi) kari)\b"), "noodle_soup"),
    (re.compile(r"\btom ?y[au]m (?:mee|noodles?|kuey ?teow)\b"), "noodle_soup"),
    (re.compile(r"\btom ?y[au]m\b"), "soup"),
    (re.compile(r"\bnasi lemak\b"), "rice"),
)
NEGATION = re.compile(r"\b(?:no|not|without|avoid|except)\b|don['’]?t")


@dataclass(frozen=True)
class Feature:
    value: float
    coverage: float

    def __post_init__(self):
        if not all(
            math.isfinite(v) and 0 <= v <= 1 for v in (self.value, self.coverage)
        ):
            raise ValueError(
                "Feature value and coverage must be finite values in [0,1]"
            )


@dataclass(frozen=True)
class ItemFit:
    fit: float
    coverage: float
    features: dict[str, Feature]


UNKNOWN = Feature(0.5, 0)


def individual_fit(features: dict[str, Feature]) -> float:
    return sum(
        weight * features.get(key, UNKNOWN).value for key, weight in WEIGHTS.items()
    )


def group_fit(fits: list[float]) -> float:
    if not fits or any(
        not math.isfinite(value) or not 0 <= value <= 1 for value in fits
    ):
        raise ValueError("Group fits require nonempty finite values in [0,1]")
    return 0.60 * mean(fits) + 0.40 * min(fits)


def base_score(
    fits: list[float], *, quality: float = 0.5, novelty: float = 0.5
) -> float:
    if any(
        not math.isfinite(value) or not 0 <= value <= 1 for value in (quality, novelty)
    ):
        raise ValueError("Quality and novelty must be finite values in [0,1]")
    return 0.85 * group_fit(fits) + 0.10 * quality + 0.05 * novelty


def _canonical(value: str) -> str:
    value = value.casefold().strip()
    return ALIASES.get(value, value)


def _tags(values) -> set[str]:
    return {_canonical(value) for value in values if isinstance(value, str)}


def _average(features: list[Feature]) -> Feature:
    return (
        Feature(
            mean(feature.value for feature in features),
            mean(feature.coverage for feature in features),
        )
        if features
        else UNKNOWN
    )


def _match(desired: set[str], available: set[str], *, broader: bool = False) -> Feature:
    if not available:
        return UNKNOWN
    if desired & available:
        return Feature(1, 1)
    if broader and (
        ("noodle_soup" in desired and available & {"soup", "noodles"})
        or ("noodle_soup" in available and desired & {"soup", "noodles"})
    ):
        return Feature(0.7, 1)
    return Feature(0.2, 1)


def _current_dimensions(response: dict) -> dict[str, set[str]]:
    dimensions = {}
    text = response.get("craving", "").casefold().strip()
    if response.get("taste_input_mode") == "structured":
        text = ""  # A diner removed/edited the chips; old prose has no authority.
    # Whole-token matches only. Unrecognized prose stays unknown; no LLM guesses.
    # Do not invert a negated free-text request into a positive preference.
    if NEGATION.search(text):
        text = ""
    for pattern, tag in PHRASES:
        text = pattern.sub(tag, text)
    words = _tags(re.findall(r"[\w]+", text))
    for dimension in ("dish", "flavour"):
        wanted = words & DIMENSIONS[dimension]
        if wanted:
            dimensions[dimension] = wanted
    dishes = _tags(response.get("dish_families", [])) & DIMENSIONS["dish"]
    if dishes:
        dimensions["dish"] = dishes
    flavours = _tags(response.get("flavour_tags", [])) & DIMENSIONS["flavour"]
    if flavours:
        dimensions["flavour"] = flavours
    if response.get("cuisines"):
        dimensions["cuisine"] = _tags(response["cuisines"])
    if response.get("spice") in DIMENSIONS["spice"]:
        dimensions["spice"] = {response["spice"]}
    return dimensions


def _craving(item: MenuItem, response: dict) -> Feature:
    desired = _current_dimensions(response)
    attributes = _tags(item.attributes)
    values = []
    for dimension, wanted in desired.items():
        available = (
            _tags(item.cuisine_tags)
            if dimension == "cuisine"
            else attributes & DIMENSIONS[dimension]
        )
        values.append(_match(wanted, available, broader=dimension == "dish"))
    if values:
        return _average(values)
    if response.get("craving", "").casefold().strip() in NEUTRAL_WORDS:
        return Feature(0.5, 1)
    return UNKNOWN


CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]+")


PER_WEIGHT = re.compile(
    r"\bper\s*\d*\s*(?:g|gm|kg|100\s*g)\b|\(\s*per\s+100", re.IGNORECASE
)
NEGATED_PHRASE = re.compile(
    r"(?:\b(?:no|not|without|avoid|except)\b|don['’]?t)\s+[\w-]+"
)


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def item_search_text(item: MenuItem) -> str:
    """Dish name and variant plus its reviewed translations (en/ms/zh), for matching only."""
    terms = [item.name, item.variant]
    for values in item.name_translations.values():
        terms.extend(values)
    return " | ".join(terms)


def name_term_overlap(query: str, text: str) -> float:
    """Share of the query's dish words found in a dish's searchable text (tie-break only).

    Latin words match whole words (light plural stemming); Chinese/Japanese runs match as
    substrings, since they have no spaces. Cross-language matching comes from each dish's
    reviewed ``name_translations``, not from a hard-coded word list. Negated phrases
    ("not spicy") are dropped rather than voiding the whole craving.
    """
    query = NEGATED_PHRASE.sub(" ", query.casefold())
    text = text.casefold()
    cjk_terms = set(CJK.findall(query))
    words = {
        _stem(w)
        for w in re.findall(r"[a-z0-9\u00c0-\u024f]+", query)
        if len(w) >= 3 and w not in NEUTRAL_WORDS
    }
    total = len(cjk_terms) + len(words)
    if not total:
        return 0.0
    have = {_stem(w) for w in re.findall(r"[a-z0-9\u00c0-\u024f]+", text)}
    hits = sum(term in text for term in cjk_terms) + len(words & have)
    return hits / total


def craving_name_overlap(item: MenuItem, response: dict) -> float:
    """Tie-break signal: today's craving words found in the dish's searchable text."""
    if response.get("taste_input_mode") == "structured":
        return 0.0
    return name_term_overlap(response.get("craving") or "", item_search_text(item))


def craving_match_level(item: MenuItem, response: dict) -> str | None:
    """'exact' / 'broader' match of today's dish, flavour or chilli wish (not cuisine)."""
    attributes = _tags(item.attributes)
    level = None
    for dimension, wanted in _current_dimensions(response).items():
        if dimension == "cuisine":
            continue
        available = attributes & DIMENSIONS[dimension]
        result = _match(wanted, available, broader=dimension == "dish")
        if result.coverage and result.value == 1:
            return "exact"
        if result.coverage and result.value >= 0.7:
            level = "broader"
    return level


def _event_time(event: dict, at: datetime) -> datetime | None:
    try:
        value = datetime.fromisoformat(
            event.get("created_at", "").replace("Z", "+00:00")
        )
        if value.tzinfo and value <= at:
            return value
    except (TypeError, ValueError):
        pass
    return None


def _outcome(event: dict) -> float | None:
    values = {"enjoyed": 1.0, "okay": 0.5, "did_not_enjoy": 0.0}
    if event.get("enjoyment") in values:
        return values[event["enjoyment"]]
    rating = event.get("rating")
    if type(rating) is int and 1 <= rating <= 5:
        return (rating - 1) / 4
    if type(event.get("would_repeat")) is bool:
        return float(event["would_repeat"])
    return None


def _visit_events(observations, outlet_id: str, at: datetime):
    return [
        event
        for event in observations
        if event.get("outlet_id") == outlet_id
        and event.get("source") == "confirmed_visit"
        and event.get("visited", True) is True
        and _event_time(event, at) is not None
    ]


def venue_affinity(observations, outlet_id: str, *, at: datetime) -> Feature:
    """Recompute from present owner-scoped visits; removed observations leave no state.

    The caller must supply only permitted personal observations. This function
    learns venue taste only, never cuisine, dietary or medical requirements.
    """
    weighted, total = 1.0, 2.0  # Two neutral prior observations.
    found = False
    for event in _visit_events(observations, outlet_id, at):
        outcome = _outcome(event)
        if outcome is None:
            continue
        age_days = (at - _event_time(event, at)).total_seconds() / 86400
        weight = math.exp(-math.log(2) * age_days / 90)
        weighted += weight * outcome
        total += weight
        found = True
    return Feature(weighted / total, 1) if found else UNKNOWN


def _lasting(
    item: MenuItem,
    profile: dict,
    response: dict,
    observations,
    venue_preferences,
    outlet_id: str,
    at: datetime,
) -> Feature:
    current = _current_dimensions(response)
    attributes = _tags(item.attributes)
    available = {
        **{key: attributes & tags for key, tags in DIMENSIONS.items()},
        "cuisine": _tags(item.cuisine_tags),
    }
    dimensions = {}
    preferences = dict(profile.get("taste_preferences", {}))
    for cuisine in profile.get("cuisines", []):
        preferences.setdefault(f"cuisine:{cuisine.casefold()}", "like")
    if profile.get("spice") in DIMENSIONS["spice"]:
        preferences.setdefault(f"spice:{profile['spice']}", "like")
    for key, value in preferences.items():
        if not isinstance(key, str) or value not in {"like", "neutral", "dislike"}:
            continue
        dimension, separator, tag = key.casefold().partition(":")
        if not separator:
            tag = _canonical(dimension)
            dimension = next(
                (name for name, tags in DIMENSIONS.items() if tag in tags), ""
            )
        tag = _canonical(tag)
        if dimension not in available or dimension in current:
            continue  # Today's explicit answer wins over this historical dimension.
        if tag in available[dimension]:
            dimensions.setdefault(dimension, []).append(
                {"like": 1.0, "neutral": 0.5, "dislike": 0.0}[value]
            )
    features = [Feature(mean(values), 1) for values in dimensions.values()]
    if profile.get("memory_enabled"):
        confirmed = next(
            (
                entry
                for entry in venue_preferences
                if entry.get("outlet_id") == outlet_id
                and type(entry.get("would_repeat")) is bool
            ),
            None,
        )
        venue = (
            Feature(float(confirmed["would_repeat"]), 1)
            if confirmed
            else venue_affinity(observations, outlet_id, at=at)
        )
        if venue.coverage:
            features.append(venue)
    return _average(features)


def _travel(response: dict, route: dict | None, at: datetime) -> Feature:
    comfortable = response.get("comfortable_travel_minutes")
    if (
        not isinstance(comfortable, (int, float))
        or isinstance(comfortable, bool)
        or not math.isfinite(comfortable)
        or comfortable <= 0
        or not route
    ):
        return UNKNOWN
    eta = route.get("eta_minutes")
    if (
        not isinstance(eta, (int, float))
        or isinstance(eta, bool)
        or not math.isfinite(eta)
        or eta < 0
        or not route.get("source_id")
    ):
        return UNKNOWN
    try:
        observed = datetime.fromisoformat(route["observed_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(route["expires_at"].replace("Z", "+00:00"))
        if not observed.tzinfo or not expires.tzinfo or not observed <= at < expires:
            return UNKNOWN
    except (KeyError, TypeError, ValueError):
        return UNKNOWN
    return Feature(1 - min(eta / max(comfortable, 1), 1), 1)


def _spending(
    item: MenuItem, response: dict, profile: dict, attribute_preferences=()
) -> Feature:
    target = response.get("soft_budget_target")
    price = item.price
    if (
        not isinstance(target, (int, float))
        or isinstance(target, bool)
        or not math.isfinite(target)
        or target <= 0
        or price is None
        or not price.all_mandatory_charges_known
        or price.payable_amount_minor is None
    ):
        return UNKNOWN
    amount = price.payable_amount_minor / 100
    is_budget_sensitive = any(
        p.get("attribute") == "value" and p.get("proposed_value") == "budget_sensitive"
        for p in attribute_preferences
    )
    if amount <= target:
        return Feature(1, 1)
    cap = response.get("budget", profile.get("max_budget"))
    if (
        isinstance(cap, (int, float))
        and not isinstance(cap, bool)
        and math.isfinite(cap)
    ):
        if cap <= target:
            return Feature(0, 1)
        base = max(0, 1 - (amount - target) / (cap - target))
        if is_budget_sensitive:
            base *= 0.8
        return Feature(base, 1)
    base = min(1, target / amount)
    if is_budget_sensitive:
        base *= 0.8
    return Feature(base, 1)


def _occasion(item: MenuItem, response: dict) -> Feature:
    attributes = _tags(item.attributes)
    values = []
    requested = _tags(response.get("occasion_features", []))
    for tag in sorted(requested):
        if tag not in OCCASIONS:
            values.append(UNKNOWN)
        elif tag in attributes:
            values.append(Feature(1, 1))
        elif f"not_{tag}" in attributes:
            values.append(Feature(0, 1))
        else:
            values.append(UNKNOWN)
    appetite = response.get("appetite")
    if appetite in DIMENSIONS["portion"]:
        known = attributes & DIMENSIONS["portion"]
        values.append(Feature(float(appetite in known), 1) if known else UNKNOWN)
    return _average(values)


def score_item(
    item: MenuItem,
    profile: dict,
    response: dict,
    *,
    outlet_id: str,
    at: datetime | None = None,
    observations=(),
    venue_preferences=(),
    attribute_preferences=(),
    route: dict | None = None,
) -> ItemFit:
    at = at or datetime.now(timezone.utc)
    features = {
        "C": _craving(item, response),
        "H": _lasting(
            item, profile, response, observations, venue_preferences, outlet_id, at
        ),
        "T": _travel(response, route, at),
        "B": _spending(item, response, profile, attribute_preferences),
        "O": _occasion(item, response),
    }
    return ItemFit(
        individual_fit(features),
        sum(WEIGHTS[key] * feature.coverage for key, feature in features.items()),
        features,
    )


def novelty_feature(person: dict, outlet_id: str, *, at: datetime) -> Feature:
    intent = person["response"].get("novelty", "any")
    if intent == "any":
        return Feature(0.5, 1)
    if intent not in {"explore", "variety", "familiar"} or not person["profile"].get(
        "memory_enabled"
    ):
        return UNKNOWN
    visits = _visit_events(person.get("observations", []), outlet_id, at)
    if not visits:
        # An incomplete personal history cannot prove a place has never been visited.
        return (
            Feature(1, 1)
            if person.get("visit_history_complete") and intent in {"explore", "variety"}
            else UNKNOWN
        )
    if intent in {"explore", "variety"}:
        return Feature(0.2, 1)
    events = sorted(visits, key=lambda event: _event_time(event, at), reverse=True)
    outcome = next(
        (_outcome(event) for event in events if _outcome(event) is not None), None
    )
    if outcome is None:
        return UNKNOWN
    return Feature(1 if outcome > 0.5 else 0.6 if outcome == 0.5 else 0.2, 1)


def rank_diverse(candidates: list[dict], *, limit: int = 3) -> list[dict]:
    """Prefer new primary cuisines within .10, dedupe comparable same-brand branches.

    A >=1km closer branch is retained as a bounded distance benefit. This does not
    claim to establish a route-time benefit without an actual route estimate.
    """
    remaining = sorted(
        candidates,
        key=lambda row: (
            -row["_score"],
            -row.get("_relevance", 0.0),  # Relevance beats distance (decision 3).
            not row.get(
                "_confirmed_meal", True
            ),  # Then confirmed meals before unknown roles.
            row["distance_km"],
            row["outlet_id"],
        ),
    )
    selected = []
    while remaining and len(selected) < limit:
        comparable = [
            row
            for row in remaining
            if not any(
                row["brand_id"] == prior["brand_id"]
                and row["distance_km"]
                > prior["distance_km"] - BRANCH_DISTANCE_BENEFIT_KM
                for prior in selected
            )
        ]
        if not comparable:
            break
        best = comparable[0]
        covered = {p for row in selected for p in row.get("_serves", ())}
        # Coverage: prefer an outlet that is the best match for a diner whose wish isn't
        # served yet. Slot 1 stays the highest score (PRD §9.3), so there coverage only
        # breaks an exact tie; later slots use the .10 diversity margin.
        margin = DIVERSITY_MARGIN if selected else 1e-9
        uncovered = next(
            (
                row
                for row in comparable
                if set(row.get("_serves", ())) - covered
                and row["_score"] >= best["_score"] - margin
            ),
            None,
        )
        if uncovered:
            selected.append(uncovered)
            remaining.remove(uncovered)
            continue
        if selected:
            used = {
                row["cuisines"][0].casefold() for row in selected if row.get("cuisines")
            }
            diverse = next(
                (
                    row
                    for row in comparable
                    if row.get("cuisines")
                    and row["cuisines"][0].casefold() not in used
                    and row["_score"] >= best["_score"] - DIVERSITY_MARGIN
                ),
                None,
            )
            if diverse:
                best = diverse
        selected.append(best)
        remaining.remove(best)
    return selected
