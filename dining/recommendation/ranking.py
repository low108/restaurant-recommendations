"""PRD §9 scoring baseline (``prd-fit-v1``): how well one dish fits one diner, and the group.

Everything here is a transparent, versioned starting point, not a trained probability or a
safety score. The recommender calls it only *after* its independent hard checks pass.

Individual fit = .45 C (today's craving) + .20 H (usual taste) + .15 T (travel)
               + .10 B (budget) + .10 O (occasion / appetite)
Group fit      = .60 × average + .40 × least-happy person
Base score     = .85 × group fit + .10 × quality + .05 × novelty

Every feature is a :class:`Feature` with a value in [0, 1] and a coverage (how much of it is
known). Missing information is the explicit neutral prior 0.5 with zero coverage, never a
guess.

Sections: vocabulary · feature model · today's craving · usual taste · travel / budget /
occasion · novelty · shortlist selection.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean

from dining.catalog.models import MenuItem
from dining.core.constants import (
    FLAVOUR_TAGS,
    OCCASION_FEATURES,
    SECONDS_PER_DAY,
    TASTE_RATINGS,
)

POLICY_VERSION = "prd-fit-v1"
FEATURE_VERSION = "prd-features-v2"
ONTOLOGY_VERSION = "dining-tags-v1"
WEIGHTS = {"C": 0.45, "H": 0.20, "T": 0.15, "B": 0.10, "O": 0.10}
MINIMUM_INDIVIDUAL_FIT = (
    0.35  # fairness floor: nobody below this at a shortlisted place
)
LOW_COVERAGE_THRESHOLD = (
    0.60  # below this, the card says the fit uses neutral assumptions
)
DIVERSITY_MARGIN = (
    0.10  # how far below the next-best score diversity/coverage may reach
)
BRANCH_DISTANCE_BENEFIT_KM = (
    1.0  # a same-brand branch must be this much closer to be kept
)

# =========================================================================== vocabulary
# Only explicit catalog attributes count as evidence: arbitrary menu prose is never
# interpreted. Aliases collapse synonyms (including Malay and local dish names) into the
# curated tags of the ``dining-tags-v1`` ontology.
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
    "flavour": set(FLAVOUR_TAGS),
    "spice": {"none", "mild", "medium", "hot"},
    "portion": {"light", "regular", "hearty"},
}
OCCASIONS = set(OCCASION_FEATURES)
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
NEGATED_PHRASE = re.compile(
    r"(?:\b(?:no|not|without|avoid|except)\b|don['’]?t)\s+[\w-]+"
)
CJK = re.compile(r"[぀-ヿ㐀-鿿]+")
LATIN_WORD = re.compile(r"[a-z0-9À-ɏ]+")
# A per-weight price ("per 100g", "/kg") is not the price of one person's meal.
PER_WEIGHT = re.compile(
    r"\bper\s*\d*\s*(?:g|gm|kg|100\s*g)\b|\(\s*per\s+100", re.IGNORECASE
)


def canonical_tag(value: str) -> str:
    """Lower-case a tag and resolve aliases to the curated vocabulary."""
    value = value.casefold().strip()
    return ALIASES.get(value, value)


def tag_set(values) -> set[str]:
    """Canonical tags from an iterable, ignoring non-strings."""
    return {canonical_tag(value) for value in values if isinstance(value, str)}


# ======================================================================== feature model
@dataclass(frozen=True)
class Feature:
    """One scored aspect of fit: ``value`` and ``coverage`` both in [0, 1]."""

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
    """A diner's fit with one dish: weighted total, known-share and the five features."""

    fit: float
    coverage: float
    features: dict[str, Feature]


UNKNOWN = Feature(0.5, 0)  # the neutral prior for anything not known


def average_features(features: list[Feature]) -> Feature:
    """Mean value and mean coverage; UNKNOWN when there is nothing to average."""
    if not features:
        return UNKNOWN
    return Feature(mean(f.value for f in features), mean(f.coverage for f in features))


def individual_fit(features: dict[str, Feature]) -> float:
    """Weighted sum of the C/H/T/B/O features (missing ones count as neutral)."""
    return sum(
        weight * features.get(key, UNKNOWN).value for key, weight in WEIGHTS.items()
    )


def group_fit(fits: list[float]) -> float:
    """Average plus least misery: .60 × mean + .40 × the lowest individual fit."""
    if not fits or any(
        not math.isfinite(value) or not 0 <= value <= 1 for value in fits
    ):
        raise ValueError("Group fits require nonempty finite values in [0,1]")
    return 0.60 * mean(fits) + 0.40 * min(fits)


def base_score(
    fits: list[float], *, quality: float = 0.5, novelty: float = 0.5
) -> float:
    """Outlet score used for ranking: .85 group fit + .10 quality + .05 novelty."""
    if any(
        not math.isfinite(value) or not 0 <= value <= 1 for value in (quality, novelty)
    ):
        raise ValueError("Quality and novelty must be finite values in [0,1]")
    return 0.85 * group_fit(fits) + 0.10 * quality + 0.05 * novelty


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
    """One diner's PRD fit with one dish (the ``prd-fit-v1`` scoring policy)."""
    at = at or datetime.now(timezone.utc)
    features = {
        "C": craving_feature(item, response),
        "H": usual_taste_feature(
            item, profile, response, observations, venue_preferences, outlet_id, at
        ),
        "T": travel_feature(response, route, at),
        "B": spending_feature(item, response, profile, attribute_preferences),
        "O": occasion_feature(item, response),
    }
    return ItemFit(
        individual_fit(features),
        sum(WEIGHTS[key] * feature.coverage for key, feature in features.items()),
        features,
    )


# ====================================================================== today's craving
def _match(desired: set[str], available: set[str], *, broader: bool = False) -> Feature:
    """PRD match levels: exact 1.0, related dish family 0.7, known mismatch 0.2."""
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


def requested_dimensions(response: dict) -> dict[str, set[str]]:
    """What the diner asked for today, by dimension (dish, flavour, cuisine, spice).

    Only whole curated words count; unrecognised prose stays unknown (no model guesses).
    A negated request is never inverted into a positive preference.
    """
    dimensions = {}
    text = response.get("craving", "").casefold().strip()
    if response.get("taste_input_mode") == "structured":
        text = ""  # The diner edited the chips; old prose has no authority.
    if NEGATION.search(text):
        text = ""
    for pattern, tag in PHRASES:
        text = pattern.sub(tag, text)
    words = tag_set(re.findall(r"[\w]+", text))
    for dimension in ("dish", "flavour"):
        if wanted := words & DIMENSIONS[dimension]:
            dimensions[dimension] = wanted
    if dishes := tag_set(response.get("dish_families", [])) & DIMENSIONS["dish"]:
        dimensions["dish"] = dishes
    if flavours := tag_set(response.get("flavour_tags", [])) & DIMENSIONS["flavour"]:
        dimensions["flavour"] = flavours
    if response.get("cuisines"):
        dimensions["cuisine"] = tag_set(response["cuisines"])
    if response.get("spice") in DIMENSIONS["spice"]:
        dimensions["spice"] = {response["spice"]}
    return dimensions


def craving_feature(item: MenuItem, response: dict) -> Feature:
    """C: average match over each dimension the diner asked about today."""
    attributes = tag_set(item.attributes)
    values = []
    for dimension, wanted in requested_dimensions(response).items():
        available = (
            tag_set(item.cuisine_tags)
            if dimension == "cuisine"
            else attributes & DIMENSIONS[dimension]
        )
        values.append(_match(wanted, available, broader=dimension == "dish"))
    if values:
        return average_features(values)
    if response.get("craving", "").casefold().strip() in NEUTRAL_WORDS:
        return Feature(0.5, 1)  # "Anything" is an answer, not missing data.
    return UNKNOWN


def craving_match_level(item: MenuItem, response: dict) -> str | None:
    """'exact' / 'broader' match of today's dish, flavour or chilli wish (not cuisine)."""
    attributes = tag_set(item.attributes)
    level = None
    for dimension, wanted in requested_dimensions(response).items():
        if dimension == "cuisine":
            continue
        result = _match(
            wanted, attributes & DIMENSIONS[dimension], broader=dimension == "dish"
        )
        if result.coverage and result.value == 1:
            return "exact"
        if result.coverage and result.value >= 0.7:
            level = "broader"
    return level


# --- tie-break text matching (never part of the PRD fit) --------------------------------
def _stem(word: str) -> str:
    """Very light plural stemming ('noodles' → 'noodle')."""
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
        for w in LATIN_WORD.findall(query)
        if len(w) >= 3 and w not in NEUTRAL_WORDS
    }
    total = len(cjk_terms) + len(words)
    if not total:
        return 0.0
    have = {_stem(w) for w in LATIN_WORD.findall(text)}
    hits = sum(term in text for term in cjk_terms) + len(words & have)
    return hits / total


def craving_name_overlap(item: MenuItem, response: dict) -> float:
    """Tie-break signal: today's craving words found in the dish's searchable text."""
    if response.get("taste_input_mode") == "structured":
        return 0.0
    return name_term_overlap(response.get("craving") or "", item_search_text(item))


# ========================================================================== usual taste
def event_time(event: dict, at: datetime) -> datetime | None:
    """When a learning event happened, if it is a valid timestamp no later than ``at``."""
    try:
        value = datetime.fromisoformat(
            event.get("created_at", "").replace("Z", "+00:00")
        )
        if value.tzinfo and value <= at:
            return value
    except (TypeError, ValueError):
        pass
    return None


def outcome_value(event: dict) -> float | None:
    """A visit's outcome in [0, 1]: enjoyment, else a 1–5 rating, else would-repeat."""
    values = {"enjoyed": 1.0, "okay": 0.5, "did_not_enjoy": 0.0}
    if event.get("enjoyment") in values:
        return values[event["enjoyment"]]
    rating = event.get("rating")
    if type(rating) is int and 1 <= rating <= 5:
        return (rating - 1) / 4
    if type(event.get("would_repeat")) is bool:
        return float(event["would_repeat"])
    return None


def _visit_events(observations, outlet_id: str, at: datetime) -> list[dict]:
    """Confirmed visits to one outlet, up to ``at``."""
    return [
        event
        for event in observations
        if event.get("outlet_id") == outlet_id
        and event.get("source") == "confirmed_visit"
        and event.get("visited", True) is True
        and event_time(event, at) is not None
    ]


def venue_affinity(observations, outlet_id: str, *, at: datetime) -> Feature:
    """Learned liking for a venue from consented visits, with a 90-day half-life.

    Recomputed from the observations present now, so a removed observation leaves no
    trace. Two neutral prior observations keep one visit from dominating. This learns venue
    taste only, never cuisine, dietary or medical requirements.
    """
    weighted, total = 1.0, 2.0  # two neutral prior observations
    found = False
    for event in _visit_events(observations, outlet_id, at):
        outcome = outcome_value(event)
        if outcome is None:
            continue
        age_days = (at - event_time(event, at)).total_seconds() / SECONDS_PER_DAY
        weight = math.exp(-math.log(2) * age_days / 90)
        weighted += weight * outcome
        total += weight
        found = True
    return Feature(weighted / total, 1) if found else UNKNOWN


def usual_taste_feature(
    item: MenuItem,
    profile: dict,
    response: dict,
    observations,
    venue_preferences,
    outlet_id: str,
    at: datetime,
) -> Feature:
    """H: saved likes/dislikes that match the dish, plus learned venue liking.

    A dimension the diner answered *today* is skipped here: today's answer wins.
    """
    current = requested_dimensions(response)
    attributes = tag_set(item.attributes)
    available = {
        **{key: attributes & tags for key, tags in DIMENSIONS.items()},
        "cuisine": tag_set(item.cuisine_tags),
    }
    preferences = dict(profile.get("taste_preferences", {}))
    for cuisine in profile.get("cuisines", []):
        preferences.setdefault(f"cuisine:{cuisine.casefold()}", "like")
    if profile.get("spice") in DIMENSIONS["spice"]:
        preferences.setdefault(f"spice:{profile['spice']}", "like")

    by_dimension: dict[str, list[float]] = {}
    for key, value in preferences.items():
        if not isinstance(key, str) or value not in TASTE_RATINGS:
            continue
        dimension, separator, tag = key.casefold().partition(":")
        if not separator:  # a bare tag: find which dimension it belongs to
            tag = canonical_tag(dimension)
            dimension = next(
                (name for name, tags in DIMENSIONS.items() if tag in tags), ""
            )
        tag = canonical_tag(tag)
        if dimension not in available or dimension in current:
            continue
        if tag in available[dimension]:
            by_dimension.setdefault(dimension, []).append(
                {"like": 1.0, "neutral": 0.5, "dislike": 0.0}[value]
            )
    features = [Feature(mean(values), 1) for values in by_dimension.values()]

    if profile.get("memory_enabled"):
        # An explicit "would / wouldn't go again" overrides what was learned from visits.
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
    return average_features(features)


# ============================================================ travel, budget, occasion
def _is_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def travel_feature(response: dict, route: dict | None, at: datetime) -> Feature:
    """T: 1 − ETA / comfortable minutes, only with fresh, attributable route evidence."""
    comfortable = response.get("comfortable_travel_minutes")
    if not _is_number(comfortable) or comfortable <= 0 or not route:
        return UNKNOWN
    eta = route.get("eta_minutes")
    if not _is_number(eta) or eta < 0 or not route.get("source_id"):
        return UNKNOWN
    try:
        observed = datetime.fromisoformat(route["observed_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(route["expires_at"].replace("Z", "+00:00"))
        if not observed.tzinfo or not expires.tzinfo or not observed <= at < expires:
            return UNKNOWN
    except (KeyError, TypeError, ValueError):
        return UNKNOWN
    return Feature(1 - min(eta / max(comfortable, 1), 1), 1)


def spending_feature(
    item: MenuItem, response: dict, profile: dict, attribute_preferences=()
) -> Feature:
    """B: 1 up to the comfortable target, then falling to 0 at the firm cap.

    Without a cap it is target / price. Needs a known all-in price; a confirmed
    "budget-sensitive" preference scales the result by 0.8 above the target.
    """
    target = response.get("soft_budget_target")
    price = item.price
    if (
        not _is_number(target)
        or target <= 0
        or price is None
        or not price.all_mandatory_charges_known
        or price.payable_amount_minor is None
    ):
        return UNKNOWN
    amount = price.payable_amount_minor / 100
    if amount <= target:
        return Feature(1, 1)
    budget_sensitive = any(
        p.get("attribute") == "value" and p.get("proposed_value") == "budget_sensitive"
        for p in attribute_preferences
    )
    cap = response.get("budget", profile.get("max_budget"))
    if _is_number(cap):
        if cap <= target:
            return Feature(0, 1)
        base = max(0, 1 - (amount - target) / (cap - target))
    else:
        base = min(1, target / amount)
    return Feature(base * 0.8 if budget_sensitive else base, 1)


def occasion_feature(item: MenuItem, response: dict) -> Feature:
    """O: share of requested soft features (quick, quiet, indoor, appetite) the dish meets."""
    attributes = tag_set(item.attributes)
    values = []
    for tag in sorted(tag_set(response.get("occasion_features", []))):
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
    return average_features(values)


# ============================================================================= novelty
def novelty_feature(person: dict, outlet_id: str, *, at: datetime) -> Feature:
    """N: 'explore' prefers unvisited places, 'familiar' prefers places they enjoyed.

    Uses only actual consented visits. A disliked visit never becomes a favourite.
    """
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
        if person.get("visit_history_complete") and intent in {"explore", "variety"}:
            return Feature(1, 1)
        return UNKNOWN
    if intent in {"explore", "variety"}:
        return Feature(0.2, 1)
    latest_first = sorted(visits, key=lambda event: event_time(event, at), reverse=True)
    outcome = next(
        (outcome_value(e) for e in latest_first if outcome_value(e) is not None), None
    )
    if outcome is None:
        return UNKNOWN
    return Feature(1 if outcome > 0.5 else 0.6 if outcome == 0.5 else 0.2, 1)


# ================================================================= shortlist selection
def _ranking_key(row: dict) -> tuple:
    """Order: score, then search relevance, then confirmed meals, then distance, then ID."""
    return (
        -row["_score"],
        -row.get("_relevance", 0.0),  # relevance beats distance (product decision 3)
        not row.get(
            "_confirmed_meal", True
        ),  # confirmed mains/sets before unknown roles
        row["distance_km"],
        row["outlet_id"],
    )


def _not_duplicate_branch(row: dict, selected: list[dict]) -> bool:
    """A same-brand branch is only kept if it is at least 1 km closer than one chosen."""
    return not any(
        row["brand_id"] == prior["brand_id"]
        and row["distance_km"] > prior["distance_km"] - BRANCH_DISTANCE_BENEFIT_KM
        for prior in selected
    )


def _first_covering(
    comparable: list[dict], selected: list[dict], best: dict
) -> dict | None:
    """An outlet that best serves a diner nobody chosen serves yet, within the margin.

    Slot 1 stays the highest score (PRD §9.3), so there coverage only breaks an exact tie.
    """
    covered = {p for row in selected for p in row.get("_serves", ())}
    margin = DIVERSITY_MARGIN if selected else 1e-9
    return next(
        (
            row
            for row in comparable
            if set(row.get("_serves", ())) - covered
            and row["_score"] >= best["_score"] - margin
        ),
        None,
    )


def _first_new_cuisine(
    comparable: list[dict], selected: list[dict], best: dict
) -> dict | None:
    """An outlet with a primary cuisine not yet chosen, within the diversity margin."""
    used = {row["cuisines"][0].casefold() for row in selected if row.get("cuisines")}
    return next(
        (
            row
            for row in comparable
            if row.get("cuisines")
            and row["cuisines"][0].casefold() not in used
            and row["_score"] >= best["_score"] - DIVERSITY_MARGIN
        ),
        None,
    )


def rank_diverse(candidates: list[dict], *, limit: int = 3) -> list[dict]:
    """Pick up to ``limit`` options: best first, then coverage, then cuisine variety.

    Each slot takes the best remaining comparable outlet unless, within the margin, one
    serves an uncovered diner (any slot) or adds a new cuisine (slots 2+). Same-brand
    branches without a real distance benefit are skipped.
    """
    remaining = sorted(candidates, key=_ranking_key)
    selected: list[dict] = []
    while remaining and len(selected) < limit:
        comparable = [row for row in remaining if _not_duplicate_branch(row, selected)]
        if not comparable:
            break
        best = comparable[0]
        choice = _first_covering(comparable, selected, best)
        if choice is None and selected:
            choice = _first_new_cuisine(comparable, selected, best)
        choice = choice or best
        selected.append(choice)
        remaining.remove(choice)
    return selected


# Backwards-compatible private names (older modules and tests import these).
_canonical = canonical_tag
_tags = tag_set
_average = average_features
_current_dimensions = requested_dimensions
_craving = craving_feature
_lasting = usual_taste_feature
_event_time = event_time
_outcome = outcome_value
_travel = travel_feature
_spending = spending_feature
_occasion = occasion_feature
