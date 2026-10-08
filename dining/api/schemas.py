"""Request bodies accepted by the API.

Every model forbids unknown fields and strips whitespace, so clients cannot smuggle extra
data into a request.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dining.api.common import now
from dining.core.constants import (
    AccessibilityNeed,
    Appetite,
    AttributeRating,
    ConsentDecision,
    FlavourTag,
    MobilityMode,
    Novelty,
    OccasionFeature,
    Spice,
    TasteRating,
)

Tags = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=60)]], Field(max_length=20)
]


class Input(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, allow_inf_nan=False
    )


class Credentials(Input):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)

    @field_validator("email")
    @classmethod
    def email_format(cls, value):
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address")
        return value.lower()


class Registration(Credentials):
    name: str = Field(min_length=1, max_length=60)
    adult_confirmed: bool
    terms_accepted: bool


class Profile(Input):
    allergy_status: Literal["none", "declared", "unknown", "withheld"] = "unknown"
    allergens: Tags = Field(default_factory=list)
    dietary_requirements: Tags = Field(default_factory=list)
    halal_policy: Literal["none", "certified", "review", "unknown"] = "unknown"
    cuisines: Tags = Field(default_factory=list)
    spice: Spice = "any"
    memory_enabled: bool = False
    requirements_reviewed: bool = False
    sensitive_data_consent: bool = False
    max_budget: float | None = Field(default=None, ge=1, le=2000)
    mobility_mode: MobilityMode = "drive"
    accessibility_requirements: list[AccessibilityNeed] = Field(default_factory=list)
    language: Literal["en", "ms"] = "en"
    taste_preferences: dict[str, TasteRating] = Field(
        default_factory=dict, max_length=40
    )

    @model_validator(mode="after")
    def requirements_consistent(self):
        if self.allergy_status == "declared" and not self.allergens:
            raise ValueError("List the declared allergens, or choose unknown/withheld")
        if self.allergy_status != "declared" and self.allergens:
            raise ValueError("Allergens require allergy_status=declared")
        if (
            self.allergens
            or self.dietary_requirements
            or self.halal_policy in {"certified", "review"}
        ) and not self.sensitive_data_consent:
            raise ValueError(
                "Explicit consent is required to store your sensitive dietary requirements"
            )
        return self


class RoomCreate(Input):
    name: str = Field(min_length=1, max_length=80)


class Join(Input):
    token: str = Field(min_length=16, max_length=128)


class Transfer(Input):
    user_id: str = Field(max_length=50)


class MealCreate(Input):
    kind: Literal["breakfast", "lunch", "dinner", "other"]
    meal_at: datetime
    answer_by: datetime | None = None
    decision_by: datetime | None = None
    duration_minutes: int = Field(default=60, ge=15, le=240)
    location_label: str = Field(min_length=1, max_length=120)
    latitude: float = Field(ge=2.4, le=3.9)
    longitude: float = Field(ge=100.7, le=102.0)
    radius_km: float = Field(default=5, ge=0.5, le=30)
    deadline_minutes: int = Field(default=30, ge=1, le=1440)
    participant_ids: list[str] | None = Field(default=None, min_length=2, max_length=8)
    idempotency_key: str = Field(min_length=8, max_length=100)

    @model_validator(mode="after")
    def valid_schedule(self):
        if self.meal_at.tzinfo is None or any(
            v is not None and v.tzinfo is None
            for v in (self.answer_by, self.decision_by)
        ):
            raise ValueError("Times must include their UTC offset")
        self.meal_at = self.meal_at.astimezone(timezone.utc)
        if self.meal_at <= now():
            raise ValueError("Meal time must be in the future")
        self.decision_by = self.decision_by or self.meal_at - timedelta(minutes=10)
        self.answer_by = self.answer_by or min(
            now() + timedelta(minutes=self.deadline_minutes),
            self.decision_by - timedelta(minutes=5),
        )
        if not now() < self.answer_by < self.decision_by < self.meal_at:
            raise ValueError("Use future times with answer_by < decision_by < meal_at")
        if self.participant_ids is not None and len(set(self.participant_ids)) != len(
            self.participant_ids
        ):
            raise ValueError("Participant IDs must be unique")
        return self


class CheckIn(Input):
    expected_response_revision: int = Field(ge=0, strict=True)
    attendance: Literal["join", "decline", "pending"] = "join"
    cuisines: Tags = Field(default_factory=list)
    craving: str = Field(default="", max_length=500)
    taste_input_mode: Literal["legacy_text", "structured"] = "legacy_text"
    appetite: Appetite = "regular"
    spice: Spice = "any"
    budget: float | None = Field(default=None, ge=1, le=2000)
    avoid: Tags = Field(default_factory=list)
    novelty: Novelty = "any"
    ready: bool = False
    requirements_confirmed: bool = False
    soft_budget_target: float | None = Field(default=None, ge=1, le=2000)
    comfortable_travel_minutes: int | None = Field(default=None, ge=1, le=180)
    occasion_features: list[OccasionFeature] = Field(default_factory=list, max_length=3)
    dish_families: Tags = Field(default_factory=list)
    flavour_tags: list[FlavourTag] = Field(default_factory=list, max_length=7)
    must_leave_by: str | None = Field(default=None, max_length=50)
    suggested_time: str | None = Field(default=None, max_length=50)
    accessibility_requirements: list[AccessibilityNeed] = Field(default_factory=list)

    @model_validator(mode="after")
    def ready_valid(self):
        if self.attendance != "join":
            self.ready = False
        if self.ready and (not self.requirements_confirmed or self.budget is None):
            raise ValueError(
                "Confirm requirements and provide today's maximum budget before marking ready"
            )
        if (
            self.soft_budget_target is not None
            and self.budget is not None
            and self.soft_budget_target > self.budget
        ):
            raise ValueError("The comfortable target cannot exceed your firm maximum")
        return self


class MealOriginInput(Input):
    origin_mode: Literal["precise", "approximate", "do_not_use"]
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    approximate_area: str | None = Field(default=None, max_length=100)
    route_consent: bool = False

    @model_validator(mode="after")
    def validate_origin(self):
        if self.origin_mode == "precise":
            if self.latitude is None or self.longitude is None:
                raise ValueError("Precise origin requires both latitude and longitude")
            if not self.route_consent:
                raise ValueError("Route processing requires explicit consent")
        elif self.origin_mode == "approximate":
            if not self.approximate_area and (
                self.latitude is None or self.longitude is None
            ):
                raise ValueError(
                    "Approximate origin requires an area label or coordinates"
                )
        elif self.origin_mode == "do_not_use":
            self.latitude = None
            self.longitude = None
            self.approximate_area = None
            self.route_consent = False
        return self


class PreparationConfirmationInput(Input):
    outlet_id: str = Field(min_length=1)
    requirement_category: Literal["allergen", "dietary", "cross_contact", "other"]
    exact_bounded_claim: str = Field(min_length=5, max_length=500)
    confirmed_by: str = Field(min_length=1, max_length=120)
    confirmation_channel: Literal[
        "phone", "in_person", "written_statement", "platform_chat"
    ]
    confirmed_at: str
    expires_at: str

    @model_validator(mode="after")
    def validate_times(self):
        try:
            c = datetime.fromisoformat(self.confirmed_at.replace("Z", "+00:00"))
            e = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            if e <= c:
                raise ValueError("expires_at must be strictly after confirmed_at")
        except ValueError as err:
            raise ValueError(f"Invalid timestamp: {err}") from err
        return self


class ConsentInput(Input):
    purpose: Literal[
        "terms",
        "sensitive_dietary_data",
        "location_routing",
        "learning",
        "model_processing",
        "analytics",
    ]
    notice_version: str = Field(min_length=1, max_length=50)
    decision: ConsentDecision
    source_interface: str = Field(default="web_settings", max_length=100)


class PushSubscriptionInput(Input):
    endpoint: str = Field(min_length=10, max_length=500)
    p256dh: str = Field(min_length=16, max_length=200)
    auth: str = Field(min_length=10, max_length=100)
    user_agent: str | None = Field(default=None, max_length=200)


class PushUnsubscribeInput(Input):
    endpoint: str = Field(min_length=10, max_length=500)


class Generate(Input):
    expected_revision: int = Field(ge=1)
    participant_ids: list[str] | None = Field(default=None, min_length=2, max_length=8)
    exclude_pending: bool = False


class InterpretPreferences(Input):
    text: str = Field(min_length=1, max_length=500)
    expected_revision: int = Field(ge=1, strict=True)
    expected_response_revision: int = Field(ge=0, strict=True)
    allow_model_processing: Literal[True]


class AdaptiveQuestionRequest(Input):
    expected_revision: int = Field(ge=1, strict=True)
    expected_response_revision: int = Field(ge=1, strict=True)


class AdaptiveQuestionAnswer(Input):
    choice: str | None = Field(default=None, min_length=1, max_length=60)
    skip: bool = False

    @model_validator(mode="after")
    def exactly_one_action(self):
        if self.skip == (self.choice is not None):
            raise ValueError("Choose one answer or skip this optional question")
        return self


class Vote(Input):
    expected_revision: int = Field(ge=1)
    option_id: str = Field(min_length=1, max_length=200)
    approve: bool | None = None
    choice: Literal["works", "prefer_another", "cannot_eat"] | None = None
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def explicit_choice(self):
        if self.choice is None and self.approve is None:
            raise ValueError("Choose works, prefer another or cannot eat")
        if self.choice is None:
            self.choice = "works" if self.approve else "prefer_another"
        if self.approve is not None and self.approve != (self.choice == "works"):
            raise ValueError("Approval and choice disagree")
        return self


class Delegation(Input):
    expected_revision: int = Field(ge=1)
    enabled: bool


class PreferenceReview(Input):
    decision: Literal["accept", "reject"]


class ManualPlan(Input):
    expected_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    address: str = Field(default="", max_length=300)
    note: str = Field(default="", max_length=500)
    participant_ids: list[str] | None = Field(default=None, min_length=2, max_length=8)
    exclude_pending: bool = False


class ManualAcknowledgement(Input):
    expected_revision: int = Field(ge=1)
    acknowledge_unverified: bool


class Cancel(Input):
    expected_revision: int = Field(ge=1)


class Select(Input):
    expected_revision: int = Field(ge=1)
    option_id: str | None = Field(default=None, max_length=200)
    choice_mode: Literal["manual", "tie_break", "random_draw"] = "manual"

    @model_validator(mode="after")
    def validate_choice_mode(self):
        if self.choice_mode == "manual" and not self.option_id:
            raise ValueError("Provide option_id when choice_mode is manual")
        return self


class DataErrorReport(Input):
    outlet_id: str = Field(min_length=1, max_length=200)
    category: Literal[
        "dietary_claim",
        "halal_status",
        "allergen",
        "price_error",
        "hours_closure",
        "other",
    ]
    description: str = Field(min_length=5, max_length=1000)


class PersonalAction(Input):
    expected_revision: int
    action: Literal["save_backup", "choose_separately", "dismiss"]


class Feedback(Input):
    visited: bool
    option_id: str = Field(min_length=1, max_length=200)
    rating: int | None = Field(default=None, ge=1, le=5)
    would_repeat: bool | None = None
    comment: str = Field(default="", max_length=500)
    outcome: (
        Literal[
            "ate_here", "somewhere_else", "plans_changed", "did_not_join", "not_yet"
        ]
        | None
    ) = None
    enjoyment: Literal["enjoyed", "okay", "did_not_enjoy", "skipped"] | None = None
    repeat_intent: Literal["yes", "another_occasion", "no", "not_sure"] | None = None
    influences: list[
        Literal[
            "taste",
            "portion",
            "value",
            "travel",
            "queue",
            "service",
            "atmosphere",
            "quietness",
            "dietary_information",
            "other",
        ]
    ] = Field(default_factory=list, max_length=10)
    attribute_ratings: dict[str, AttributeRating] = Field(default_factory=dict)
    cost_expectation: (
        Literal["within_estimate", "higher", "lower", "not_sure"] | None
    ) = None
    actual_cost_minor: int | None = Field(default=None, ge=0, le=200000, strict=True)
    dish_text: str = Field(default="", max_length=500)
    fairness: Literal["yes", "somewhat", "no", "skipped"] | None = None
    alternate_outlet_id: str | None = Field(default=None, max_length=200)
    alternate_outlet_name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def actual_experience(self):
        if self.outcome is not None and self.visited != (self.outcome == "ate_here"):
            raise ValueError("Visit confirmation must agree with what happened")
        if self.repeat_intent is not None:
            repeat = {"yes": True, "no": False}.get(self.repeat_intent)
            if self.would_repeat is not None and self.would_repeat != repeat:
                raise ValueError("Repeat intent values disagree")
            self.would_repeat = repeat
        if not self.visited and (
            self.rating is not None
            or self.would_repeat is not None
            or self.enjoyment not in {None, "skipped"}
            or self.repeat_intent is not None
            or self.actual_cost_minor is not None
            or self.dish_text
            or self.cost_expectation is not None
        ):
            raise ValueError("Enjoyment feedback requires confirming an actual visit")
        return self


class ParticipantSet(Input):
    participant_ids: list[str] = Field(min_length=2, max_length=8)
    expected_revision: int = Field(ge=1)


__all__ = [
    "AdaptiveQuestionAnswer",
    "AdaptiveQuestionRequest",
    "Cancel",
    "CheckIn",
    "ConsentInput",
    "Credentials",
    "DataErrorReport",
    "Delegation",
    "Feedback",
    "Generate",
    "Input",
    "InterpretPreferences",
    "Join",
    "ManualAcknowledgement",
    "ManualPlan",
    "MealCreate",
    "MealOriginInput",
    "ParticipantSet",
    "PersonalAction",
    "PreferenceReview",
    "PreparationConfirmationInput",
    "Profile",
    "PushSubscriptionInput",
    "PushUnsubscribeInput",
    "Registration",
    "RoomCreate",
    "Select",
    "Tags",
    "Transfer",
    "Vote",
]
