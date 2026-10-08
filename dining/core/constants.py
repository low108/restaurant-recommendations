"""Constants and vocabularies shared by more than one domain.

Values used by a single module stay next to that code; anything two modules must agree on
lives here, so a change is made once. Vocabularies are ``Literal`` types: request models
and LLM schemas use them directly, and :func:`typing.get_args` gives the runtime set.
"""

from __future__ import annotations

from typing import Literal, get_args

# ------------------------------------------------------------------------------ time
SECONDS_PER_DAY = 86_400
DEFAULT_MEAL_MINUTES = 60  # used when a meal has no explicit duration
LOCAL_TIMEZONE = "Asia/Kuala_Lumpur"
INVITE_SECONDS = 7 * SECONDS_PER_DAY  # room invite links stay valid for a week

# ------------------------------------------------------------------------ meal states
# A checked (``selected``) or organiser-picked (``manual_selected``) decision exists.
DECIDED_STATUSES = frozenset({"selected", "manual_selected"})
# Decided meals plus the post-meal feedback window.
POST_DECISION_STATUSES = DECIDED_STATUSES | {"awaiting_feedback"}
# The meal is over and can never change again.
TERMINAL_STATUSES = frozenset({"closed", "cancelled", "expired"})
# No more planning changes are possible (decided, in feedback or terminal).
FINALIZED_STATUSES = POST_DECISION_STATUSES | TERMINAL_STATUSES
# Statuses a recommendation run may return.
RESULT_STATUSES = frozenset(
    {"shortlisted", "needs_verification", "no_options", "needs_input"}
)

# ------------------------------------------------- private requirements (fail closed)
# These answers mean the requirement is not known, so suitability cannot be checked.
UNDISCLOSED_ALLERGY_STATUSES = frozenset({"unknown", "withheld"})
UNREVIEWED_HALAL_POLICIES = frozenset({"unknown", "review"})
PLANT_BASED_DIETS = frozenset({"vegetarian", "vegan"})

# ------------------------------------------------------------------------ catalog
LANGUAGES = ("en", "ms", "zh")  # reviewed dish-name translations
# Prices quoted per weight are never a per-person meal price.
PER_WEIGHT_UNITS = frozenset({"weight_100g", "weight_kg"})

# ------------------------------------------------------------------- versions/models
RETRIEVAL_POLICY_VERSION = "retrieval-policy-v1"
DEFAULT_ILMU_MODEL = "ilmu-mini-v3.3"

# ---------------------------------------------------------------- shared vocabularies
# Value order matters where a schema is shown to the language model (llm/preferences.py).
Appetite = Literal["light", "regular", "hearty", "any"]
Spice = Literal["none", "mild", "medium", "hot", "any"]
Novelty = Literal["familiar", "variety", "explore", "any"]
FlavourTag = Literal["rich", "light", "smoky", "sweet", "sour", "savoury", "spicy"]
OccasionFeature = Literal["quick", "quiet", "indoor"]
TasteRating = Literal["like", "neutral", "dislike"]
AttributeRating = Literal["positive", "neutral", "negative"]
MobilityMode = Literal["drive", "transit", "walk", "ehailing"]
ConsentDecision = Literal["accepted", "declined", "withdrawn"]
AccessibilityNeed = Literal[
    "step_free_entrance",
    "wheelchair_accessible_seating",
    "accessible_restroom",
    "low_noise_seating",
    "unknown",
    "withheld",
]

FLAVOUR_TAGS = frozenset(get_args(FlavourTag))
OCCASION_FEATURES = frozenset(get_args(OccasionFeature))
TASTE_RATINGS = frozenset(get_args(TasteRating))
CONSENT_DECISIONS = get_args(ConsentDecision)

# Compact, deterministic JSON (used for storage and fingerprints).
COMPACT_JSON_SEPARATORS = (",", ":")
