"""Authenticated, purpose-limited HTTP API for the group dining pilot.

The application enforces room boundaries and immutable recommendation revisions;
LLM output never supplies authorization or a participant's private requirements.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pwdlib import PasswordHash
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .adaptive import build_m09_question
from .attribute_learning import (
    extract_attribute_signals,
    propose_attribute_patterns,
)
from .consent import ConsentManager
from .exposure import (
    EVENT_CANDIDATE_SELECTED,
    EVENT_CANDIDATE_VOTED_ON,
    record_card_impressions,
    record_exposure_event,
    record_generation_exposures,
)
from .generation import (
    GenerationWorker,
    Superseded,
    evidence_identity,
    job_view,
    policy_identity,
    snapshot_identity,
    supersede_jobs,
)
from .geocoding import get_geocoding_provider
from .inference import InferenceSettings
from .lifecycle import planned_finish, post_meal_transition
from .preferences import interpret_preferences
from .push import PushSubscriptionManager
from .store import DEFAULT_PROFILE, DiningStore, decode, encode

COOKIE = "dining_session"
SESSION_SECONDS = 7 * 86400
PASSWORD_HASHER = PasswordHash.recommended()
# A constant hash makes unknown-account login perform the same expensive check.
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("not-a-real-account-password")
Tags = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=60)]], Field(max_length=20)
]


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp() -> str:
    return now().isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
    spice: Literal["any", "none", "mild", "medium", "hot"] = "any"
    memory_enabled: bool = False
    requirements_reviewed: bool = False
    sensitive_data_consent: bool = False
    max_budget: float | None = Field(default=None, ge=1, le=2000)
    mobility_mode: Literal["drive", "walk", "transit", "ehailing"] = "drive"
    accessibility_requirements: list[
        Literal[
            "step_free_entrance",
            "wheelchair_accessible_seating",
            "accessible_restroom",
            "low_noise_seating",
            "unknown",
            "withheld",
        ]
    ] = Field(default_factory=list)
    language: Literal["en", "ms"] = "en"
    taste_preferences: dict[str, Literal["like", "neutral", "dislike"]] = Field(
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
    appetite: Literal["light", "regular", "hearty", "any"] = "regular"
    spice: Literal["any", "none", "mild", "medium", "hot"] = "any"
    budget: float | None = Field(default=None, ge=1, le=2000)
    avoid: Tags = Field(default_factory=list)
    novelty: Literal["familiar", "variety", "explore", "any"] = "any"
    ready: bool = False
    requirements_confirmed: bool = False
    soft_budget_target: float | None = Field(default=None, ge=1, le=2000)
    comfortable_travel_minutes: int | None = Field(default=None, ge=1, le=180)
    occasion_features: list[Literal["quick", "quiet", "indoor"]] = Field(
        default_factory=list, max_length=3
    )
    dish_families: Tags = Field(default_factory=list)
    flavour_tags: list[
        Literal["rich", "light", "smoky", "sweet", "sour", "savoury", "spicy"]
    ] = Field(default_factory=list, max_length=7)
    must_leave_by: str | None = Field(default=None, max_length=50)
    suggested_time: str | None = Field(default=None, max_length=50)
    accessibility_requirements: list[
        Literal[
            "step_free_entrance",
            "wheelchair_accessible_seating",
            "accessible_restroom",
            "low_noise_seating",
            "unknown",
            "withheld",
        ]
    ] = Field(default_factory=list)

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
    decision: Literal["accepted", "declined", "withdrawn"]
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
    attribute_ratings: dict[str, Literal["positive", "neutral", "negative"]] = Field(
        default_factory=dict
    )
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


def build_router(
    store: DiningStore,
    recommend: Callable[[dict], dict],
    demo_mode: bool = False,
    account_access: Callable[[str], None] | None = None,
    async_generation: bool = False,
    generation_options: dict | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    def origin_check(request: Request):
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Cross-site requests are not permitted")
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.netloc != request.url.netloc
                or parsed.scheme != request.url.scheme
            ):
                raise HTTPException(403, "Origin does not match this application")

    def authenticate(request: Request):
        token = request.cookies.get(COOKIE)
        if not token:
            raise HTTPException(401, "Sign in to continue")
        with store.transaction() as db:
            session = db.execute(
                "SELECT s.*,u.email,u.name FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?",
                (digest(token), time.time()),
            ).fetchone()
        if session is None:
            raise HTTPException(401, "Your session expired. Sign in again")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin_check(request)
            if not hmac.compare_digest(
                request.headers.get("x-csrf-token", ""), session["csrf_token"]
            ):
                raise HTTPException(
                    403, "Refresh this page and try again (CSRF token required)"
                )
        if (
            account_access
            and request.url.path
            not in {"/api/session", "/api/profile", "/api/export", "/api/account"}
            and not request.url.path.startswith("/api/auth/")
        ):
            account_access(session["user_id"])
        return dict(session)

    Auth = Depends(authenticate)

    def user_view(row):
        return {"id": row["id"], "email": row["email"], "name": row["name"]}

    def session_view(user_id, csrf):
        with store.transaction() as db:
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return {
            "user": user_view(user),
            "profile": {
                **decode(user["profile"]),
                "profile_revision": user["profile_revision"],
            },
            "csrf_token": csrf,
            "demo_mode": demo_mode,
        }

    def create_session(
        user_id, response: Response, request: Request, verified_password_hash=None
    ):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with store.transaction() as db:
            current = db.execute(
                "SELECT password_hash FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if current is None or (
                verified_password_hash is not None
                and current["password_hash"] != verified_password_hash
            ):
                raise HTTPException(401, "Credentials changed. Sign in again")
            db.execute("DELETE FROM sessions WHERE expires_at<?", (time.time(),))
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?)",
                (digest(token), user_id, csrf, time.time() + SESSION_SECONDS),
            )
        response.set_cookie(
            COOKIE,
            token,
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
        return session_view(user_id, csrf)

    def login_limit(request: Request, email: str):
        # Persist both account+IP and IP-wide buckets. No trusted forwarded IP in this local server.
        origin_check(request)
        if (
            not request.headers.get("content-type", "")
            .lower()
            .startswith("application/json")
        ):
            raise HTTPException(415, "Send JSON")
        client = request.client.host if request.client else "unknown"
        account_max = 500 if demo_mode else 10
        ip_max = 2000 if demo_mode else 50
        buckets = [
            (digest("account:" + client + ":" + email), account_max),
            (digest("ip:" + client), ip_max),
        ]
        blocked = False
        with store.transaction() as db:
            db.execute(
                "DELETE FROM login_attempts WHERE attempted_at<?", (time.time() - 900,)
            )
            for bucket, maximum in buckets:
                count = db.execute(
                    "SELECT COUNT(*) FROM login_attempts WHERE bucket=?", (bucket,)
                ).fetchone()[0]
                blocked |= count >= maximum
            if not blocked:
                db.executemany(
                    "INSERT INTO login_attempts VALUES(?,?)",
                    [(b, time.time()) for b, _ in buckets],
                )
        if blocked:
            raise HTTPException(
                429, "Too many sign-in attempts. Try again in 15 minutes"
            )

    def room_member(db, room_id, user_id, owner=False, active=False):
        row = db.execute(
            "SELECT r.* FROM rooms r JOIN members m ON m.room_id=r.id WHERE r.id=? AND m.user_id=?",
            (room_id, user_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Room not found")
        if owner and row["owner_id"] != user_id:
            raise HTTPException(403, "Only the room owner can do that")
        if active and row["archived"]:
            raise HTTPException(409, "This room is archived")
        return row

    def refresh_lifecycle(db, meal):
        if meal["status"] in {"cancelled", "expired", "closed"}:
            return meal
        payload = decode(meal["payload"])
        end = planned_finish(payload)
        current = now()
        if meal["status"] in {"selected", "manual_selected", "awaiting_feedback"}:
            transition = post_meal_transition(meal["status"], payload, current)
            if transition is None:
                return meal
            status, reason = transition
        else:
            status = (
                "expired"
                if current >= end
                else (
                    "decision_overdue"
                    if current >= datetime.fromisoformat(payload["decision_by"])
                    else meal["status"]
                )
            )
            reason = (
                "Planned meal ended without a confirmed decision"
                if status == "expired"
                else "Decision deadline passed without a confirmed decision"
            )
        if status != meal["status"]:
            if status in {"expired", "decision_overdue"}:
                supersede_jobs(db, meal["id"])
            db.execute(
                "UPDATE meals SET status=?,lifecycle_reason=? WHERE id=?",
                (status, reason, meal["id"]),
            )
            if status == "expired":
                db.execute(
                    "UPDATE meals SET result=NULL,result_revision=NULL,manual_plan=NULL,frozen_participants=NULL WHERE id=?",
                    (meal["id"],),
                )
                db.execute("DELETE FROM votes WHERE meal_id=?", (meal["id"],))
                db.execute("DELETE FROM delegations WHERE meal_id=?", (meal["id"],))
                db.execute(
                    "DELETE FROM manual_acknowledgements WHERE meal_id=?", (meal["id"],)
                )
            audit(db, None, "meal_" + status, meal["room_id"], meal["id"])
            meal = db.execute(
                "SELECT * FROM meals WHERE id=?", (meal["id"],)
            ).fetchone()
        return meal

    def meal_member(
        db, meal_id, user_id, organizer=False, mutable=False, allow_selected=False
    ):
        meal = db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone()
        if meal is None:
            raise HTTPException(404, "Meal not found")
        room_member(db, meal["room_id"], user_id, active=mutable)
        participant = db.execute(
            "SELECT * FROM participants WHERE meal_id=? AND user_id=?",
            (meal_id, user_id),
        ).fetchone()
        if participant is None:
            raise HTTPException(404, "Meal not found")
        if organizer and meal["organizer_id"] != user_id:
            raise HTTPException(403, "Only the meal organizer can do that")
        meal = refresh_lifecycle(db, meal)
        if mutable and meal["status"] in {
            "selected",
            "manual_selected",
            "awaiting_feedback",
            "closed",
            "cancelled",
            "expired",
        }:
            revisable = (
                allow_selected
                and meal["status"] in {"selected", "manual_selected"}
                and datetime.fromisoformat(decode(meal["payload"])["meal_at"]) > now()
            )
            if not revisable:
                raise HTTPException(409, "This meal is already finalized")
        return meal

    def room_view(db, room):
        return {
            "id": room["id"],
            "name": room["name"],
            "owner_id": room["owner_id"],
            "archived": bool(room["archived"]),
            "member_count": db.execute(
                "SELECT COUNT(*) FROM members WHERE room_id=?", (room["id"],)
            ).fetchone()[0],
        }

    def audit(db, actor, kind, room_id=None, meal_id=None):
        db.execute(
            "INSERT INTO audit_events VALUES(?,?,?,?,?,?)",
            (new_id(), actor, room_id, meal_id, kind, stamp()),
        )

    def notify(db, users, kind, message, room_id=None, meal_id=None):
        db.executemany(
            "INSERT INTO notifications VALUES(?,?,?,?,?,?,?,0)",
            [
                (new_id(), uid, room_id, meal_id, kind, message, stamp())
                for uid in set(users)
            ],
        )

    def invalidate(db, meal_id):
        meal = db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone()
        if meal is None or meal["status"] in {
            "awaiting_feedback",
            "closed",
            "cancelled",
            "expired",
        }:
            return False
        if (
            meal["status"] in {"selected", "manual_selected"}
            and datetime.fromisoformat(decode(meal["payload"])["meal_at"]) <= now()
        ):
            return False  # Preserve completed shared history; future outings need new consent.
        supersede_jobs(db, meal_id)
        reconfirm = meal["status"] in {"selected", "manual_selected"} or bool(
            meal["reconfirmation_required"]
        )
        db.execute(
            "UPDATE meals SET revision=revision+1,status=?,reconfirmation_required=?,result=NULL,result_revision=NULL,frozen_participants=NULL,decision=NULL,manual_plan=NULL,lifecycle_reason=NULL WHERE id=?",
            (
                "reconfirmation_required" if reconfirm else "collecting",
                int(reconfirm),
                meal_id,
            ),
        )
        db.execute("DELETE FROM votes WHERE meal_id=?", (meal_id,))
        db.execute("DELETE FROM delegations WHERE meal_id=?", (meal_id,))
        db.execute("DELETE FROM manual_acknowledgements WHERE meal_id=?", (meal_id,))
        if meal["status"] in {"selected", "manual_selected"}:
            notify(
                db,
                [
                    p["user_id"]
                    for p in db.execute(
                        "SELECT user_id FROM participants WHERE meal_id=?", (meal_id,)
                    )
                ],
                "reconfirmation_required",
                "The meal changed. A new checked decision needs everyone's confirmation.",
                meal["room_id"],
                meal_id,
            )
        return True

    def invalidate_member_meals(db, room_id, user_id):
        meals = db.execute(
            "SELECT m.id FROM meals m JOIN participants p ON p.meal_id=m.id WHERE m.room_id=? AND p.user_id=? AND m.status!='cancelled'",
            (room_id, user_id),
        ).fetchall()
        for row in meals:
            invalidate(db, row["id"])

    def cancel_upcoming_room_meals(db, room_id):
        for meal in db.execute(
            "SELECT id FROM meals WHERE room_id=? AND status!='cancelled'", (room_id,)
        ).fetchall():
            if invalidate(db, meal["id"]):
                db.execute(
                    "UPDATE meals SET status='cancelled' WHERE id=?", (meal["id"],)
                )

    def remove_member(db, room, user_id, actor):
        invalidate_member_meals(db, room["id"], user_id)
        db.execute(
            "DELETE FROM participants WHERE user_id=? AND meal_id IN (SELECT id FROM meals WHERE room_id=? AND status NOT IN ('selected','manual_selected','awaiting_feedback','closed','cancelled','expired'))",
            (user_id, room["id"]),
        )
        for affected in db.execute(
            "SELECT id FROM meals WHERE room_id=? AND organizer_id=? AND status NOT IN ('selected','manual_selected','awaiting_feedback','closed','cancelled','expired')",
            (room["id"], user_id),
        ).fetchall():
            remaining = [
                r["user_id"]
                for r in db.execute(
                    "SELECT p.user_id FROM participants p JOIN members m ON m.user_id=p.user_id AND m.room_id=? WHERE p.meal_id=? ORDER BY p.user_id",
                    (room["id"], affected["id"]),
                )
            ]
            replacement = (
                room["owner_id"]
                if room["owner_id"] in remaining
                else (remaining[0] if remaining else None)
            )
            db.execute(
                "UPDATE meals SET organizer_id=? WHERE id=?",
                (replacement, affected["id"]),
            )
            if len(remaining) < 2:
                db.execute(
                    "UPDATE meals SET status='cancelled' WHERE id=?", (affected["id"],)
                )
        db.execute(
            "DELETE FROM members WHERE room_id=? AND user_id=?", (room["id"], user_id)
        )
        db.execute(
            "DELETE FROM notifications WHERE room_id=? AND user_id=?",
            (room["id"], user_id),
        )
        db.execute(
            "UPDATE rooms SET membership_revision=membership_revision+1 WHERE id=?",
            (room["id"],),
        )
        audit(db, actor, "member_removed", room["id"])

    def approved_users(db, meal, option_id):
        included = set(decode(meal["frozen_participants"], []))
        votes = {
            r["user_id"]: r["choice"]
            for r in db.execute(
                "SELECT user_id,choice FROM votes WHERE meal_id=? AND revision=? AND option_id=?",
                (meal["id"], meal["revision"], option_id),
            )
        }
        delegated = {
            r["user_id"]
            for r in db.execute(
                "SELECT user_id,option_ids FROM delegations WHERE meal_id=? AND revision=?",
                (meal["id"], meal["revision"]),
            )
            if option_id in decode(r["option_ids"], [])
        }
        # A participant's explicit negative choice always overrides delegation.
        return {
            uid
            for uid in included
            if votes.get(uid) == "works" or (uid in delegated and uid not in votes)
        }

    def meal_view(db, meal, user_id):
        meal = refresh_lifecycle(db, meal)
        result = dict(decode(meal["payload"]))
        result.update(
            {
                "id": meal["id"],
                "room_id": meal["room_id"],
                "organizer_id": meal["organizer_id"],
                "revision": meal["revision"],
                "status": meal["status"],
                "created_at": meal["created_at"],
                "original_answer_by": meal["original_answer_by"],
                "original_decision_by": meal["original_decision_by"],
                "original_invited_count": meal["original_invited_count"],
                "reconfirmation_required": bool(meal["reconfirmation_required"]),
                "lifecycle_reason": meal["lifecycle_reason"],
            }
        )
        rows = db.execute(
            "SELECT p.*,u.name FROM participants p JOIN users u ON u.id=p.user_id WHERE p.meal_id=? ORDER BY u.name",
            (meal["id"],),
        ).fetchall()
        result["participants"] = [
            {
                "id": p["user_id"],
                "name": p["name"],
                "attendance": p["attendance"],
                "ready": bool(decode(p["response"], {}).get("ready")),
                "responded": p["response"] is not None,
            }
            for p in rows
        ]
        own = next((p for p in rows if p["user_id"] == user_id), None)
        result["my_response"] = decode(own["response"]) if own else None
        result["my_response_revision"] = own["response_revision"] if own else 0
        origin_row = db.execute(
            "SELECT * FROM meal_origins WHERE meal_id=? AND user_id=?",
            (meal["id"], user_id),
        ).fetchone()
        result["my_origin"] = (
            {
                "origin_mode": origin_row["origin_mode"],
                "latitude": origin_row["latitude"],
                "longitude": origin_row["longitude"],
                "approximate_area": origin_row["approximate_area"],
                "route_consent": bool(origin_row["route_consent"]),
                "updated_at": origin_row["updated_at"],
            }
            if origin_row
            else None
        )
        result["my_votes"] = {
            r["option_id"]: {"choice": r["choice"], "reason": r["reason"]}
            for r in db.execute(
                "SELECT option_id,choice,reason FROM votes WHERE meal_id=? AND user_id=? AND revision=?",
                (meal["id"], user_id, meal["revision"]),
            )
        }
        delegation = db.execute(
            "SELECT * FROM delegations WHERE meal_id=? AND user_id=? AND revision=?",
            (meal["id"], user_id, meal["revision"]),
        ).fetchone()
        result["my_delegation"] = {
            "enabled": delegation is not None,
            "revision": meal["revision"],
            "option_ids": decode(delegation["option_ids"]) if delegation else [],
        }
        result["my_vote"] = {
            r["option_id"]: bool(r["approve"])
            for r in db.execute(
                "SELECT option_id,approve FROM votes WHERE meal_id=? AND user_id=? AND revision=?",
                (meal["id"], user_id, meal["revision"]),
            )
        }
        result["generation_worker_status"] = generation_worker.health()
        result["generation_history"] = [
            job_view(db, job)
            for job in db.execute(
                "SELECT * FROM generation_jobs WHERE meal_id=? ORDER BY rowid DESC LIMIT 10",
                (meal["id"],),
            )
        ]
        result["generation_job"] = next(
            (
                job
                for job in result["generation_history"]
                if job["context_revision"] == meal["revision"]
            ),
            None,
        )
        decoded_result = decode(meal["result"])
        if decoded_result and "_private_routes" in decoded_result:
            result["my_route_estimates"] = decoded_result.pop(
                "_private_routes", {}
            ).get(user_id, {})
        else:
            result["my_route_estimates"] = {}
        result["result"] = decoded_result
        result["decision"] = decode(meal["decision"])
        result["manual_plan"] = decode(meal["manual_plan"])
        acknowledged = {
            r["user_id"]
            for r in db.execute(
                "SELECT user_id FROM manual_acknowledgements WHERE meal_id=? AND revision=?",
                (meal["id"], meal["revision"]),
            )
        }
        result["my_manual_ack"] = user_id in acknowledged
        result["manual_acceptance"] = {
            "acknowledged_count": len(acknowledged),
            "required_count": len(decode(meal["frozen_participants"], [])),
        }
        result["frozen_participant_ids"] = decode(meal["frozen_participants"], [])
        result["acceptance"] = {}
        for option in (result["result"] or {}).get("options", []):
            option_id = str(option.get("id", option.get("option_id", "")))
            approved = len(approved_users(db, meal, option_id))
            result["acceptance"][option_id] = {
                "approved_count": approved,
                "required_count": len(result["frozen_participant_ids"]),
            }
        feedback = db.execute(
            "SELECT payload FROM feedback WHERE meal_id=? AND user_id=?",
            (meal["id"], user_id),
        ).fetchone()
        result["my_feedback"] = decode(feedback["payload"]) if feedback else None

        # Schedule conflict detection (PRD REQ-03 / M04)
        conflicts = []
        participant_ids = [p["id"] for p in result["participants"]]
        if (
            participant_ids
            and result["status"] not in {"closed", "cancelled", "expired"}
            and "meal_at" in result
        ):
            meal_start = datetime.fromisoformat(result["meal_at"])
            meal_end = meal_start + timedelta(
                minutes=result.get("duration_minutes", 60)
            )
            placeholders = ",".join("?" for _ in participant_ids)
            overlapping_rows = db.execute(
                f"""SELECT DISTINCT p.user_id FROM participants p
                    JOIN meals other ON other.id=p.meal_id
                    WHERE p.user_id IN ({placeholders})
                    AND other.id!=?
                    AND other.status NOT IN ('closed','cancelled','expired')
                """,
                (*participant_ids, meal["id"]),
            ).fetchall()
            for row in overlapping_rows:
                user_other_meals = db.execute(
                    "SELECT payload FROM meals m JOIN participants p ON p.meal_id=m.id WHERE p.user_id=? AND m.id!=? AND m.status NOT IN ('closed','cancelled','expired')",
                    (row["user_id"], meal["id"]),
                ).fetchall()
                for om in user_other_meals:
                    om_payload = decode(om["payload"], {})
                    if "meal_at" in om_payload:
                        om_start = datetime.fromisoformat(om_payload["meal_at"])
                        om_end = om_start + timedelta(
                            minutes=om_payload.get("duration_minutes", 60)
                        )
                        if max(meal_start, om_start) < min(meal_end, om_end):
                            conflicts.append(row["user_id"])
                            break
        conflict_count = len(set(conflicts))
        result["schedule_conflicts"] = (
            [
                {
                    "message": f"{conflict_count} invited participant{' has' if conflict_count == 1 else 's have'} an overlapping meal around this time"
                }
            ]
            if conflict_count > 0
            else []
        )
        return result

    def revision_check(meal, expected):
        if meal["revision"] != expected:
            raise HTTPException(
                409, "This meal changed. Refresh and review the new version"
            )

    def option_check(meal, option_id):
        if (
            meal["result_revision"] != meal["revision"]
            or meal["status"] != "shortlisted"
        ):
            raise HTTPException(409, "Generate a current, eligible shortlist first")
        if datetime.fromisoformat(decode(meal["payload"])["decision_by"]) <= now():
            raise HTTPException(
                409,
                "The decision deadline passed. Update the meal times and regenerate",
            )
        options = decode(meal["result"], {}).get("options", [])
        option = next(
            (
                o
                for o in options
                if str(o.get("id", o.get("option_id", ""))) == option_id
            ),
            None,
        )
        if not option:
            raise HTTPException(422, "Choose an option in the current shortlist")
        if option.get("eligible") is False or option.get("status") in {
            "needs_verification",
            "ineligible",
        }:
            raise HTTPException(409, "This option has unresolved requirements")
        return option

    @router.get("/session")
    def session(request: Request):
        try:
            auth = authenticate(request)
        except HTTPException as error:
            if error.status_code != 401:
                raise
            return {
                "user": None,
                "profile": None,
                "csrf_token": None,
                "demo_mode": demo_mode,
            }
        return session_view(auth["user_id"], auth["csrf_token"])

    @router.post("/auth/register", status_code=201)
    def register(body: Registration, request: Request, response: Response):
        login_limit(request, body.email)
        if not body.adult_confirmed or not body.terms_accepted:
            raise HTTPException(
                422, "Confirm adult eligibility and accept the pilot terms"
            )
        user_id, password_hash = new_id(), PASSWORD_HASHER.hash(body.password)
        try:
            with store.transaction() as db:
                db.execute(
                    "INSERT INTO users(id,email,name,password_hash,profile,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        user_id,
                        body.email,
                        body.name,
                        password_hash,
                        encode(DEFAULT_PROFILE),
                        stamp(),
                    ),
                )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "An account already uses this email") from None
        return create_session(
            user_id, response, request, verified_password_hash=password_hash
        )

    @router.post("/auth/login")
    def login(body: Credentials, request: Request, response: Response):
        login_limit(request, body.email)
        with store.transaction() as db:
            user = db.execute(
                "SELECT * FROM users WHERE email=?", (body.email,)
            ).fetchone()
        correct = PASSWORD_HASHER.verify(
            body.password, user["password_hash"] if user else DUMMY_PASSWORD_HASH
        )
        if not user or not correct:
            raise HTTPException(401, "Email or password is incorrect")
        return create_session(
            user["id"], response, request, verified_password_hash=user["password_hash"]
        )

    @router.post("/auth/logout")
    def logout(response: Response, auth=Auth):
        with store.transaction() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (auth["token_hash"],))
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
        return {
            "user": None,
            "profile": None,
            "csrf_token": None,
            "demo_mode": demo_mode,
        }

    @router.get("/profile")
    def get_profile(auth=Auth):
        return session_view(auth["user_id"], auth["csrf_token"])["profile"]

    @router.patch("/profile")
    def update_profile(body: dict, auth=Auth):
        if not body or set(body) - set(Profile.model_fields) - {
            "expected_profile_revision"
        }:
            raise HTTPException(422, "Provide supported profile fields")
        with store.transaction() as db:
            user = db.execute(
                "SELECT * FROM users WHERE id=?", (auth["user_id"],)
            ).fetchone()
            expected = body.get("expected_profile_revision")
            if expected is not None and (
                type(expected) is not int or expected != user["profile_revision"]
            ):
                raise HTTPException(
                    409,
                    {
                        "code": "PROFILE_CHANGED",
                        "message": "Your profile changed in another tab. Review the current values.",
                        "current_profile": {
                            **decode(user["profile"]),
                            "profile_revision": user["profile_revision"],
                        },
                    },
                )
            data = decode(user["profile"])
            data.update(
                {k: v for k, v in body.items() if k != "expected_profile_revision"}
            )
            try:
                profile = Profile.model_validate(data).model_dump()
            except ValueError as error:
                raise HTTPException(
                    422,
                    "Profile values are inconsistent; check requirement states and sensitive-data consent",
                ) from error
            if profile != decode(user["profile"]):
                db.execute(
                    "UPDATE users SET profile=?,profile_revision=profile_revision+1 WHERE id=?",
                    (encode(profile), auth["user_id"]),
                )
                for row in db.execute(
                    "SELECT meal_id FROM participants WHERE user_id=?",
                    (auth["user_id"],),
                ).fetchall():
                    invalidate(db, row["meal_id"])
                    db.execute(
                        "UPDATE participants SET response_revision=response_revision+1,response=json_set(response,'$.requirements_confirmed',json('false'),'$.ready',json('false')) WHERE meal_id=? AND user_id=? AND response IS NOT NULL AND EXISTS(SELECT 1 FROM meals WHERE id=? AND status NOT IN ('selected','manual_selected','awaiting_feedback','closed','cancelled','expired'))",
                        (row["meal_id"], auth["user_id"], row["meal_id"]),
                    )
            if not profile["memory_enabled"]:
                db.execute(
                    "DELETE FROM learning_suggestion_log WHERE user_id=?",
                    (auth["user_id"],),
                )
                db.execute(
                    "DELETE FROM observations WHERE user_id=?", (auth["user_id"],)
                )
            audit(db, auth["user_id"], "profile_updated")
            profile["profile_revision"] = db.execute(
                "SELECT profile_revision FROM users WHERE id=?", (auth["user_id"],)
            ).fetchone()[0]
            return profile

    @router.get("/consent")
    def get_consent(auth=Auth):
        with store.transaction() as db:
            return ConsentManager.get_consent_status(db, auth["user_id"])

    @router.post("/consent")
    def record_consent(body: ConsentInput, auth=Auth):
        with store.transaction() as db:
            record = ConsentManager.record_consent(
                db,
                auth["user_id"],
                body.purpose,
                body.notice_version,
                body.decision,
                body.source_interface,
            )
            audit(db, auth["user_id"], f"consent_{body.purpose}_{body.decision}")
            return record

    @router.post("/push/subscriptions", status_code=201)
    def register_push_subscription(body: PushSubscriptionInput, auth=Auth):
        with store.transaction() as db:
            return PushSubscriptionManager.register(
                db,
                auth["user_id"],
                body.endpoint,
                body.p256dh,
                body.auth,
                body.user_agent,
            )

    @router.get("/push/subscriptions")
    def list_push_subscriptions(auth=Auth):
        with store.transaction() as db:
            return PushSubscriptionManager.list_user_subscriptions(db, auth["user_id"])

    @router.post("/push/unsubscribe")
    def unsubscribe_push(body: PushUnsubscribeInput, auth=Auth):
        with store.transaction() as db:
            success = PushSubscriptionManager.unsubscribe(
                db, auth["user_id"], body.endpoint
            )
            if not success:
                raise HTTPException(404, "Subscription not found")
            return {"unsubscribed": True}

    @router.get("/rooms")
    def rooms(auth=Auth):
        with store.transaction() as db:
            rows = db.execute(
                "SELECT r.* FROM rooms r JOIN members m ON m.room_id=r.id WHERE m.user_id=? ORDER BY r.created_at DESC",
                (auth["user_id"],),
            ).fetchall()
            return {"rooms": [room_view(db, r) for r in rows]}

    @router.post("/rooms", status_code=201)
    def create_room(body: RoomCreate, auth=Auth):
        room_id, token = new_id(), secrets.token_urlsafe(32)
        with store.transaction() as db:
            db.execute(
                "INSERT INTO rooms(id,name,owner_id,invite_hash,invite_expires,created_at) VALUES(?,?,?,?,?,?)",
                (
                    room_id,
                    body.name,
                    auth["user_id"],
                    digest(token),
                    time.time() + 7 * 86400,
                    stamp(),
                ),
            )
            db.execute(
                "INSERT INTO members VALUES(?,?,?)", (room_id, auth["user_id"], stamp())
            )
            audit(db, auth["user_id"], "room_created", room_id)
            room = room_view(
                db, db.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
            )
        return {"room": room, "invite_token": token}

    @router.get("/rooms/preview")
    def preview_room(token: str):
        with store.transaction() as db:
            room = db.execute(
                "SELECT * FROM rooms WHERE invite_hash=? AND invite_expires>? AND archived=0",
                (digest(token), time.time()),
            ).fetchone()
            if room is None:
                raise HTTPException(404, "This invitation expired or was revoked")
            member_count = db.execute(
                "SELECT COUNT(*) FROM members WHERE room_id=?", (room["id"],)
            ).fetchone()[0]
            owner = None
            if room["owner_id"]:
                owner_row = db.execute(
                    "SELECT name FROM users WHERE id=?", (room["owner_id"],)
                ).fetchone()
                if owner_row:
                    owner = owner_row["name"]
            return {
                "room_id": room["id"],
                "name": room["name"],
                "member_count": member_count,
                "created_at": room["created_at"],
                "owner_name": owner,
            }

    @router.post("/rooms/join")
    def join_room(body: Join, auth=Auth):
        with store.transaction() as db:
            room = db.execute(
                "SELECT * FROM rooms WHERE invite_hash=? AND invite_expires>? AND archived=0",
                (digest(body.token), time.time()),
            ).fetchone()
            if room is None:
                raise HTTPException(404, "This invitation expired or was revoked")
            existing = db.execute(
                "SELECT 1 FROM members WHERE room_id=? AND user_id=?",
                (room["id"], auth["user_id"]),
            ).fetchone()
            if not existing:
                if (
                    db.execute(
                        "SELECT COUNT(*) FROM members WHERE room_id=?", (room["id"],)
                    ).fetchone()[0]
                    >= 20
                ):
                    raise HTTPException(
                        409, "This pilot supports up to 20 members per room"
                    )
                db.execute(
                    "INSERT INTO members VALUES(?,?,?)",
                    (room["id"], auth["user_id"], stamp()),
                )
                db.execute(
                    "UPDATE rooms SET membership_revision=membership_revision+1 WHERE id=?",
                    (room["id"],),
                )
                audit(db, auth["user_id"], "room_joined", room["id"])
            return room_view(db, room)

    @router.get("/rooms/{room_id}")
    def room_detail(room_id: str, auth=Auth):
        with store.transaction() as db:
            room = room_member(db, room_id, auth["user_id"])
            output = room_view(db, room)
            output["members"] = [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "is_owner": r["id"] == room["owner_id"],
                }
                for r in db.execute(
                    "SELECT u.id,u.name FROM users u JOIN members m ON m.user_id=u.id WHERE m.room_id=? ORDER BY m.joined_at",
                    (room_id,),
                )
            ]
            for pending in db.execute(
                "SELECT m.* FROM meals m JOIN participants p ON p.meal_id=m.id WHERE m.room_id=? AND p.user_id=?",
                (room_id, auth["user_id"]),
            ).fetchall():
                refresh_lifecycle(db, pending)
            output["meals"] = [
                {
                    "id": m["id"],
                    "status": m["status"],
                    "revision": m["revision"],
                    "organizer_id": m["organizer_id"],
                    **{
                        k: decode(m["payload"])[k]
                        for k in ("kind", "meal_at", "location_label")
                    },
                }
                for m in db.execute(
                    "SELECT m.* FROM meals m JOIN participants p ON p.meal_id=m.id WHERE m.room_id=? AND p.user_id=? ORDER BY m.created_at DESC",
                    (room_id, auth["user_id"]),
                )
            ]
            return output

    @router.post("/rooms/{room_id}/invite")
    def rotate_invite(room_id: str, auth=Auth):
        token = secrets.token_urlsafe(32)
        with store.transaction() as db:
            room_member(db, room_id, auth["user_id"], owner=True, active=True)
            db.execute(
                "UPDATE rooms SET invite_hash=?,invite_expires=? WHERE id=?",
                (digest(token), time.time() + 7 * 86400, room_id),
            )
            audit(db, auth["user_id"], "invite_rotated", room_id)
        return {"invite_token": token, "expires_in_days": 7}

    @router.post("/rooms/{room_id}/leave")
    def leave_room(room_id: str, auth=Auth):
        with store.transaction() as db:
            room = room_member(db, room_id, auth["user_id"])
            if room["owner_id"] == auth["user_id"] and not room["archived"]:
                raise HTTPException(
                    409, "Transfer ownership or archive the room before leaving"
                )
            remove_member(db, room, auth["user_id"], auth["user_id"])
        return {"ok": True}

    @router.delete("/rooms/{room_id}/members/{user_id}")
    def kick_member(room_id: str, user_id: str, auth=Auth):
        with store.transaction() as db:
            room = room_member(db, room_id, auth["user_id"], owner=True, active=True)
            if user_id == room["owner_id"]:
                raise HTTPException(409, "Transfer ownership before removing the owner")
            if not db.execute(
                "SELECT 1 FROM members WHERE room_id=? AND user_id=?",
                (room_id, user_id),
            ).fetchone():
                raise HTTPException(404, "Member not found")
            remove_member(db, room, user_id, auth["user_id"])
        return {"ok": True}

    @router.post("/rooms/{room_id}/transfer")
    def transfer_owner(room_id: str, body: Transfer, auth=Auth):
        with store.transaction() as db:
            room_member(db, room_id, auth["user_id"], owner=True, active=True)
            if not db.execute(
                "SELECT 1 FROM members WHERE room_id=? AND user_id=?",
                (room_id, body.user_id),
            ).fetchone():
                raise HTTPException(422, "Choose an existing member")
            db.execute(
                "UPDATE rooms SET owner_id=? WHERE id=?", (body.user_id, room_id)
            )
            audit(db, auth["user_id"], "ownership_transferred", room_id)
        return {"ok": True}

    @router.post("/rooms/{room_id}/archive")
    def archive_room(room_id: str, auth=Auth):
        with store.transaction() as db:
            room_member(db, room_id, auth["user_id"], owner=True)
            db.execute(
                "UPDATE rooms SET archived=1,invite_hash=NULL,invite_expires=NULL WHERE id=?",
                (room_id,),
            )
            cancel_upcoming_room_meals(db, room_id)
            audit(db, auth["user_id"], "room_archived", room_id)
        return {"ok": True}

    @router.post("/rooms/{room_id}/meals", status_code=201)
    def create_meal(room_id: str, body: MealCreate, auth=Auth):
        with store.transaction() as db:
            room_member(db, room_id, auth["user_id"], active=True)
            existing = db.execute(
                "SELECT * FROM meals WHERE room_id=? AND organizer_id=? AND idempotency_key=?",
                (room_id, auth["user_id"], body.idempotency_key),
            ).fetchone()
            if existing:
                return meal_view(db, existing, auth["user_id"])
            members = {
                r["user_id"]
                for r in db.execute(
                    "SELECT user_id FROM members WHERE room_id=?", (room_id,)
                )
            }
            invited = (
                set(body.participant_ids)
                if body.participant_ids is not None
                else members
            )
            if (
                not 2 <= len(invited) <= 8
                or not invited.issubset(members)
                or auth["user_id"] not in invited
            ):
                raise HTTPException(
                    422, "Invite 2–8 current room members including yourself"
                )
            meal_id = new_id()
            payload = body.model_dump(
                mode="json",
                exclude={"participant_ids", "idempotency_key", "deadline_minutes"},
            )
            payload["timezone"] = "Asia/Kuala_Lumpur"
            if demo_mode:
                payload["synthetic"] = True
            db.execute(
                "INSERT INTO meals(id,room_id,organizer_id,payload,created_at,idempotency_key,original_answer_by,original_decision_by,original_invited_count) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    meal_id,
                    room_id,
                    auth["user_id"],
                    encode(payload),
                    stamp(),
                    body.idempotency_key,
                    payload["answer_by"],
                    payload["decision_by"],
                    len(invited),
                ),
            )
            db.executemany(
                "INSERT INTO participants(meal_id,user_id) VALUES(?,?)",
                [(meal_id, uid) for uid in invited],
            )
            notify(
                db,
                invited,
                "meal_invitation",
                "A meal is open for your private check-in.",
                room_id,
                meal_id,
            )
            audit(db, auth["user_id"], "meal_created", room_id, meal_id)
            return meal_view(
                db,
                db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                auth["user_id"],
            )

    @router.get("/locations/neighbourhoods")
    def lookup_neighbourhoods(query: str = ""):
        provider = get_geocoding_provider()
        res = provider.lookup_neighbourhood(query)
        return res.to_dict()

    @router.get("/locations/reverse")
    def reverse_lookup_location(latitude: float, longitude: float):
        provider = get_geocoding_provider()
        res = provider.reverse_lookup(latitude, longitude)
        return res.to_dict()

    @router.get("/meals/{meal_id}")
    def get_meal(meal_id: str, auth=Auth):
        with store.transaction() as db:
            view = meal_view(
                db, meal_member(db, meal_id, auth["user_id"]), auth["user_id"]
            )
            if view.get("result") and view["result"].get("options"):
                record_card_impressions(
                    db,
                    meal_id=view["id"],
                    meal_revision=view["revision"],
                    policy_version=view["result"].get("policy_version", "1.0"),
                    user_id=auth["user_id"],
                    options=view["result"]["options"],
                )
            return view

    @router.get("/meals/{meal_id}/recommendation-history")
    def recommendation_history(meal_id: str, auth=Auth):
        with store.transaction() as db:
            meal_member(db, meal_id, auth["user_id"])
            rows = db.execute(
                "SELECT revision,payload,created_at FROM recommendation_archives WHERE meal_id=? ORDER BY revision DESC",
                (meal_id,),
            ).fetchall()
            return [
                {
                    "revision": r["revision"],
                    "created_at": r["created_at"],
                    "result": decode(r["payload"]),
                }
                for r in rows
            ]

    @router.patch("/meals/{meal_id}")
    def edit_meal(meal_id: str, body: dict, auth=Auth):
        fields = {
            "kind",
            "meal_at",
            "answer_by",
            "decision_by",
            "duration_minutes",
            "location_label",
            "latitude",
            "longitude",
            "radius_km",
        }
        if not body or set(body) - fields - {"expected_revision"}:
            raise HTTPException(422, "Provide supported meal fields")
        with store.transaction() as db:
            meal = meal_member(
                db,
                meal_id,
                auth["user_id"],
                organizer=True,
                mutable=True,
                allow_selected=True,
            )
            revision_check(meal, body.get("expected_revision"))
            values = decode(meal["payload"])
            values.pop("timezone", None)
            values.pop("synthetic", None)
            values.update({k: v for k, v in body.items() if k in fields})
            values["idempotency_key"] = meal["idempotency_key"]
            try:
                validated = MealCreate.model_validate(values)
            except ValueError as error:
                raise HTTPException(
                    422, "Check meal coordinates and future deadline order"
                ) from error
            payload = validated.model_dump(
                mode="json",
                exclude={"participant_ids", "idempotency_key", "deadline_minutes"},
            )
            payload["timezone"] = "Asia/Kuala_Lumpur"
            if demo_mode:
                payload["synthetic"] = True
            db.execute(
                "UPDATE meals SET payload=? WHERE id=?", (encode(payload), meal_id)
            )
            invalidate(db, meal_id)
            # Changed logistics require every participant to actively reconfirm.
            db.execute(
                "UPDATE participants SET response_revision=response_revision+1,response=json_set(response,'$.ready',json('false')) WHERE meal_id=? AND response IS NOT NULL",
                (meal_id,),
            )
            users = [
                r["user_id"]
                for r in db.execute(
                    "SELECT user_id FROM participants WHERE meal_id=?", (meal_id,)
                )
            ]
            notify(
                db,
                users,
                "meal_changed",
                "The meal details changed. Please review your check-in.",
                meal["room_id"],
                meal_id,
            )
            audit(db, auth["user_id"], "meal_edited", meal["room_id"], meal_id)
            return meal_view(
                db,
                db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                auth["user_id"],
            )

    @router.post("/meals/{meal_id}/cancel")
    def cancel_meal(meal_id: str, body: Cancel, auth=Auth):
        with store.transaction() as db:
            # Future decisions can be withdrawn explicitly. Completed history cannot.
            meal = meal_member(db, meal_id, auth["user_id"], organizer=True)
            room_member(db, meal["room_id"], auth["user_id"], active=True)
            revision_check(meal, body.expected_revision)
            if meal["status"] == "cancelled":
                raise HTTPException(409, "This meal is already cancelled")
            if datetime.fromisoformat(decode(meal["payload"])["meal_at"]) <= now():
                raise HTTPException(
                    409,
                    "Past meals remain in history; record what happened through feedback",
                )
            db.execute(
                "UPDATE meals SET status='cancelled',revision=revision+1,result=NULL,result_revision=NULL,frozen_participants=NULL,decision=NULL,manual_plan=NULL WHERE id=?",
                (meal_id,),
            )
            db.execute("DELETE FROM votes WHERE meal_id=?", (meal_id,))
            db.execute("DELETE FROM delegations WHERE meal_id=?", (meal_id,))
            db.execute(
                "DELETE FROM manual_acknowledgements WHERE meal_id=?", (meal_id,)
            )
            users = [
                row["user_id"]
                for row in db.execute(
                    "SELECT user_id FROM participants WHERE meal_id=?", (meal_id,)
                )
            ]
            notify(
                db,
                users,
                "meal_cancelled",
                "The organizer cancelled this meal.",
                meal["room_id"],
                meal_id,
            )
            audit(db, auth["user_id"], "meal_cancelled", meal["room_id"], meal_id)
            return meal_view(
                db,
                db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                auth["user_id"],
            )

    @router.post("/meals/{meal_id}/participants")
    def edit_participants(meal_id: str, body: ParticipantSet, auth=Auth):
        selected = set(body.participant_ids)
        if len(selected) != len(body.participant_ids):
            raise HTTPException(422, "Participant IDs must be unique")
        with store.transaction() as db:
            meal = meal_member(
                db,
                meal_id,
                auth["user_id"],
                organizer=True,
                mutable=True,
                allow_selected=True,
            )
            revision_check(meal, body.expected_revision)
            members = {
                r["user_id"]
                for r in db.execute(
                    "SELECT user_id FROM members WHERE room_id=?", (meal["room_id"],)
                )
            }
            if not selected.issubset(members) or auth["user_id"] not in selected:
                raise HTTPException(422, "Select room members including the organizer")
            current = {
                r["user_id"]
                for r in db.execute(
                    "SELECT user_id FROM participants WHERE meal_id=?", (meal_id,)
                )
            }
            for uid in current - selected:
                db.execute(
                    "DELETE FROM participants WHERE meal_id=? AND user_id=?",
                    (meal_id, uid),
                )
            for uid in selected - current:
                db.execute(
                    "INSERT INTO participants(meal_id,user_id) VALUES(?,?)",
                    (meal_id, uid),
                )
            invalidate(db, meal_id)
            notify(
                db,
                current | selected,
                "participants_changed",
                "The meal participant list changed. Review the current list.",
                meal["room_id"],
                meal_id,
            )
            audit(
                db,
                auth["user_id"],
                "meal_participants_changed",
                meal["room_id"],
                meal_id,
            )
            return meal_view(
                db,
                db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                auth["user_id"],
            )

    @router.post("/meals/{meal_id}/interpret-preferences")
    def interpret_craving(
        meal_id: str, body: InterpretPreferences, request: Request, auth=Auth
    ):
        def current(db):
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            if (
                meal["decision"]
                or datetime.fromisoformat(decode(meal["payload"])["answer_by"]) <= now()
            ):
                raise HTTPException(
                    409, "This meal no longer accepts preference drafts"
                )
            person = db.execute(
                "SELECT response_revision FROM participants WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            ).fetchone()
            if not person:
                raise HTTPException(404, "Meal not found")
            if (
                meal["revision"] != body.expected_revision
                or person["response_revision"] != body.expected_response_revision
            ):
                raise HTTPException(
                    409,
                    "The meal or your answer changed. Refresh before interpreting again",
                )

        with store.transaction() as db:
            current(db)
            count = db.execute(
                "SELECT COUNT(*) FROM preference_requests WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            ).fetchone()[0]
            recent = db.execute(
                "SELECT COUNT(*) FROM preference_requests WHERE user_id=? AND requested_at>?",
                (auth["user_id"], time.time() - 3600),
            ).fetchone()[0]
            if count >= 2 or recent >= 10:
                raise HTTPException(
                    429,
                    "AI draft limit reached. You can still edit and submit your preferences manually",
                )
            request_id = new_id()
            db.execute(
                "INSERT INTO preference_requests(id,meal_id,user_id,requested_at,status) VALUES(?,?,?,?,?)",
                (request_id, meal_id, auth["user_id"], time.time(), "requested"),
            )
        # Never hold the database lock across model I/O. Nothing here saves answers.
        settings = getattr(recommend, "settings", InferenceSettings())
        result = interpret_preferences(settings, body.text)
        authenticate(
            request
        )  # Logout/deletion while the provider ran revokes return access.
        with store.transaction() as db:
            current(db)
            db.execute(
                "UPDATE preference_requests SET status=?,metadata=? WHERE id=?",
                (result["status"], encode(result["agent"]), request_id),
            )
        return {
            **result,
            "request_id": request_id,
            "remaining_requests": max(0, 1 - count),
        }

    @router.put("/meals/{meal_id}/response")
    def checkin(meal_id: str, body: CheckIn, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            if datetime.fromisoformat(decode(meal["payload"])["answer_by"]) <= now():
                raise HTTPException(
                    409, "The check-in deadline passed. Ask the organizer to extend it"
                )
            profile = decode(
                db.execute(
                    "SELECT profile FROM users WHERE id=?", (auth["user_id"],)
                ).fetchone()["profile"]
            )
            if body.ready and not profile.get("requirements_reviewed"):
                raise HTTPException(
                    422, "Review your private profile requirements first"
                )
            previous = db.execute(
                "SELECT response_revision,response FROM participants WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            ).fetchone()
            if previous["response_revision"] != body.expected_response_revision:
                raise HTTPException(
                    409,
                    {
                        "code": "RESPONSE_CHANGED",
                        "message": "Your answer changed in another tab. Review the current answer before saving.",
                        "current_response_revision": previous["response_revision"],
                        "current_response": decode(previous["response"]),
                    },
                )
            data = body.model_dump(exclude={"expected_response_revision"})
            if body.budget is not None:
                data["budget_minor"] = round(body.budget * 100)
            db.execute(
                "UPDATE participants SET attendance=?,response=?,response_revision=response_revision+1 WHERE meal_id=? AND user_id=?",
                (body.attendance, encode(data), meal_id, auth["user_id"]),
            )
            invalidate(db, meal_id)
            audit(db, auth["user_id"], "checkin_updated", meal["room_id"], meal_id)
            return meal_view(
                db,
                db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                auth["user_id"],
            )

    @router.put("/meals/{meal_id}/origin")
    def set_meal_origin(meal_id: str, body: MealOriginInput, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            participant = db.execute(
                "SELECT * FROM participants WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            ).fetchone()
            if not participant:
                raise HTTPException(404, "Participant record not found")
            now_iso = stamp()
            db.execute(
                """
                INSERT INTO meal_origins(
                    meal_id, user_id, origin_mode, latitude, longitude,
                    approximate_area, route_consent, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(meal_id, user_id) DO UPDATE SET
                    origin_mode=excluded.origin_mode,
                    latitude=excluded.latitude,
                    longitude=excluded.longitude,
                    approximate_area=excluded.approximate_area,
                    route_consent=excluded.route_consent,
                    updated_at=excluded.updated_at
                """,
                (
                    meal_id,
                    auth["user_id"],
                    body.origin_mode,
                    body.latitude,
                    body.longitude,
                    body.approximate_area,
                    1 if body.route_consent else 0,
                    now_iso,
                    now_iso,
                ),
            )
            db.execute(
                "UPDATE participants SET response_revision=response_revision+1 WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            )
            invalidate(db, meal_id)
            audit(db, auth["user_id"], "meal_origin_updated", meal["room_id"], meal_id)
            return {
                "meal_id": meal_id,
                "origin_mode": body.origin_mode,
                "latitude": body.latitude,
                "longitude": body.longitude,
                "approximate_area": body.approximate_area,
                "route_consent": body.route_consent,
                "updated_at": now_iso,
            }

    @router.get("/meals/{meal_id}/origin")
    def get_meal_origin(meal_id: str, auth=Auth):
        with store.transaction() as db:
            meal_member(db, meal_id, auth["user_id"])
            row = db.execute(
                "SELECT * FROM meal_origins WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            ).fetchone()
            if not row:
                return None
            return {
                "meal_id": meal_id,
                "origin_mode": row["origin_mode"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "approximate_area": row["approximate_area"],
                "route_consent": bool(row["route_consent"]),
                "updated_at": row["updated_at"],
            }

    @router.delete("/meals/{meal_id}/origin")
    def delete_meal_origin(meal_id: str, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            db.execute(
                "DELETE FROM meal_origins WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            )
            db.execute(
                "UPDATE participants SET response_revision=response_revision+1 WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            )
            invalidate(db, meal_id)
            audit(db, auth["user_id"], "meal_origin_deleted", meal["room_id"], meal_id)
            return {"deleted": True}

    @router.post("/meals/{meal_id}/preparation-confirmations", status_code=201)
    def add_preparation_confirmation(
        meal_id: str, body: PreparationConfirmationInput, auth=Auth
    ):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            conf_id = str(uuid.uuid4())
            now_iso = stamp()
            db.execute(
                """
                INSERT INTO preparation_confirmations(
                    id, meal_id, outlet_id, requirement_category, confirmed_by,
                    confirmation_channel, confirmed_at, expires_at, exact_bounded_claim, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conf_id,
                    meal_id,
                    body.outlet_id,
                    body.requirement_category,
                    body.confirmed_by,
                    body.confirmation_channel,
                    body.confirmed_at,
                    body.expires_at,
                    body.exact_bounded_claim,
                    now_iso,
                ),
            )
            invalidate(db, meal_id)
            audit(
                db, auth["user_id"], "preparation_confirmed", meal["room_id"], meal_id
            )
            return {
                "id": conf_id,
                "meal_id": meal_id,
                "outlet_id": body.outlet_id,
                "requirement_category": body.requirement_category,
                "confirmed_by": body.confirmed_by,
                "confirmation_channel": body.confirmation_channel,
                "confirmed_at": body.confirmed_at,
                "expires_at": body.expires_at,
                "exact_bounded_claim": body.exact_bounded_claim,
                "created_at": now_iso,
            }

    @router.get("/meals/{meal_id}/preparation-confirmations")
    def list_preparation_confirmations(meal_id: str, auth=Auth):
        with store.transaction() as db:
            meal_member(db, meal_id, auth["user_id"])
            rows = db.execute(
                "SELECT * FROM preparation_confirmations WHERE meal_id=? ORDER BY created_at",
                (meal_id,),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "meal_id": r["meal_id"],
                    "outlet_id": r["outlet_id"],
                    "requirement_category": r["requirement_category"],
                    "confirmed_by": r["confirmed_by"],
                    "confirmation_channel": r["confirmation_channel"],
                    "confirmed_at": r["confirmed_at"],
                    "expires_at": r["expires_at"],
                    "exact_bounded_claim": r["exact_bounded_claim"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    @router.delete("/meals/{meal_id}/preparation-confirmations/{confirmation_id}")
    def delete_preparation_confirmation(meal_id: str, confirmation_id: str, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            db.execute(
                "DELETE FROM preparation_confirmations WHERE id=? AND meal_id=?",
                (confirmation_id, meal_id),
            )
            invalidate(db, meal_id)
            audit(
                db,
                auth["user_id"],
                "preparation_confirmation_deleted",
                meal["room_id"],
                meal_id,
            )
            return {"deleted": True}

    def snapshot_for(db, meal, included):
        participants = []
        for uid in included:
            row = db.execute(
                "SELECT p.*,u.profile,u.profile_revision FROM participants p JOIN users u ON u.id=p.user_id JOIN members mb ON mb.user_id=p.user_id AND mb.room_id=? WHERE p.meal_id=? AND p.user_id=?",
                (meal["room_id"], meal["id"], uid),
            ).fetchone()
            if not row or row["attendance"] != "join":
                raise HTTPException(
                    409, "A participant is no longer included. Refresh this meal"
                )
            profile, response = decode(row["profile"]), decode(row["response"], {})
            if not response.get("ready") or not response.get("requirements_confirmed"):
                raise HTTPException(
                    409,
                    "Every included participant must finish and confirm their check-in",
                )
            person = {
                "user_id": uid,
                "profile": profile,
                "response": response,
                "profile_revision": row["profile_revision"],
                "response_revision": row["response_revision"],
            }
            origin_row = db.execute(
                "SELECT * FROM meal_origins WHERE meal_id=? AND user_id=?",
                (meal["id"], uid),
            ).fetchone()
            if origin_row:
                person["origin"] = {
                    "origin_mode": origin_row["origin_mode"],
                    "latitude": origin_row["latitude"],
                    "longitude": origin_row["longitude"],
                    "approximate_area": origin_row["approximate_area"],
                    "route_consent": bool(origin_row["route_consent"]),
                }
            else:
                person["origin"] = {
                    "origin_mode": "do_not_use",
                    "latitude": None,
                    "longitude": None,
                    "approximate_area": None,
                    "route_consent": False,
                }
            if profile.get("memory_enabled"):
                person["venue_preferences"] = [
                    {
                        "outlet_id": r["outlet_id"],
                        "would_repeat": bool(r["would_repeat"]),
                        "source_observation_id": r["observation_id"],
                        "created_at": r["created_at"],
                    }
                    for r in db.execute(
                        "SELECT * FROM preference_proposals WHERE user_id=? AND status='accepted' AND (proposal_type='venue' OR proposal_type IS NULL) ORDER BY reviewed_at DESC",
                        (uid,),
                    )
                ]
                person["attribute_preferences"] = [
                    {
                        "attribute": r["attribute"],
                        "proposed_value": r["proposed_value"],
                        "description": r["description"],
                        "created_at": r["created_at"],
                    }
                    for r in db.execute(
                        "SELECT * FROM preference_proposals WHERE user_id=? AND status='accepted' AND proposal_type='attribute' ORDER BY reviewed_at DESC",
                        (uid,),
                    )
                ]
                person["observations"] = [
                    {
                        **decode(observation["payload"]),
                        "created_at": observation["created_at"],
                    }
                    for observation in db.execute(
                        "SELECT payload,created_at FROM observations WHERE user_id=? AND created_at>=? ORDER BY created_at DESC LIMIT 20",
                        (uid, (now() - timedelta(days=90)).isoformat()),
                    )
                ]
            participants.append(person)
        # Shared history contains confirmed occurrences, not named individual ratings.
        history = []
        for old in db.execute(
            "SELECT m.id,m.decision,m.result,m.payload,m.created_at FROM meals m WHERE m.room_id=? AND m.status IN ('selected','manual_selected','awaiting_feedback','closed') ORDER BY m.created_at DESC LIMIT 20",
            (meal["room_id"],),
        ):
            visited = db.execute(
                "SELECT 1 FROM feedback WHERE meal_id=? AND json_extract(payload,'$.visited')=1 LIMIT 1",
                (old["id"],),
            ).fetchone()
            decision = decode(old["decision"], {})
            option = next(
                (
                    o
                    for o in decode(old["result"], {}).get("options", [])
                    if str(o.get("id", o.get("option_id"))) == decision.get("option_id")
                ),
                {},
            )
            history.append(
                {
                    "meal_id": old["id"],
                    "outlet_id": option.get("outlet_id"),
                    "visited": bool(visited),
                    "created_at": old["created_at"],
                    "meal_at": decode(old["payload"])["meal_at"],
                }
            )
        confirmations = [
            {
                "id": r["id"],
                "meal_id": r["meal_id"],
                "outlet_id": r["outlet_id"],
                "requirement_category": r["requirement_category"],
                "confirmed_by": r["confirmed_by"],
                "confirmation_channel": r["confirmation_channel"],
                "confirmed_at": r["confirmed_at"],
                "expires_at": r["expires_at"],
                "exact_bounded_claim": r["exact_bounded_claim"],
            }
            for r in db.execute(
                "SELECT * FROM preparation_confirmations WHERE meal_id=?",
                (meal["id"],),
            )
        ]
        return {
            "meal": {
                **decode(meal["payload"]),
                "id": meal["id"],
                "room_id": meal["room_id"],
                "revision": meal["revision"],
            },
            "participants": participants,
            "history": history,
            "preparation_confirmations": confirmations,
            "revision": meal["revision"],
        }

    def safe_recommend(snapshot, revalidate=False):
        # Defense in depth: even a mistaken tool callback cannot treat undisclosed requirements as satisfied.
        if any(
            p["profile"].get("allergy_status") in {"unknown", "withheld"}
            or p["profile"].get("halal_policy") in {"unknown", "review"}
            or not p["profile"].get("requirements_reviewed")
            for p in snapshot["participants"]
        ):
            return {
                "status": "needs_verification",
                "options": [],
                "verification": [],
                "explanation": "Some private requirements need clarification before suitability can be checked.",
            }
        worker = (
            getattr(recommend, "revalidate", recommend) if revalidate else recommend
        )
        result = worker(snapshot)
        if (
            not isinstance(result, dict)
            or result.get("status")
            not in {"shortlisted", "needs_verification", "no_options", "needs_input"}
            or not isinstance(result.get("options", []), list)
        ):
            raise ValueError("Invalid recommendation output")
        # The deterministic recommendation service returns a purpose-limited public result.
        allowed = {
            "status",
            "options",
            "verification",
            "explanation",
            "catalog_id",
            "catalog_version",
            "synthetic",
            "policy_version",
            "feature_version",
            "ontology_version",
            "examined_outlets",
            "coverage",
            "metrics",
            "run_id",
            "agent",
            "_exposure_candidates",
        }
        return {k: v for k, v in result.items() if k in allowed}

    def adaptive_context(db, meal_id, user_id, body):
        meal = meal_member(db, meal_id, user_id, mutable=True)
        payload = decode(meal["payload"])
        if meal["decision"] or datetime.fromisoformat(payload["decision_by"]) <= now():
            raise HTTPException(
                409, "This meal no longer accepts an optional follow-up"
            )
        participant = db.execute(
            "SELECT response,response_revision FROM participants WHERE meal_id=? AND user_id=?",
            (meal_id, user_id),
        ).fetchone()
        if (
            meal["revision"] != body.expected_revision
            or participant["response_revision"] != body.expected_response_revision
        ):
            raise HTTPException(
                409, "The meal or your answer changed. Refresh before continuing"
            )
        response = decode(participant["response"], {})
        if not response.get("ready") or response.get("attendance") != "join":
            raise HTTPException(
                409, "Finish your core check-in before an optional follow-up"
            )
        included = [
            row["user_id"]
            for row in db.execute(
                "SELECT user_id,response FROM participants WHERE meal_id=? AND attendance='join' ORDER BY user_id",
                (meal_id,),
            )
            if decode(row["response"], {}).get("ready")
            and decode(row["response"], {}).get("requirements_confirmed")
        ]
        if len(included) < 2:
            raise HTTPException(
                409, "Wait until at least two people finish checking in"
            )
        return meal, participant, snapshot_for(db, meal, included)

    @router.post("/meals/{meal_id}/adaptive-question")
    def issue_adaptive_question(
        meal_id: str, body: AdaptiveQuestionRequest, request: Request, auth=Auth
    ):
        with store.transaction() as db:
            meal, participant, snapshot = adaptive_context(
                db, meal_id, auth["user_id"], body
            )
            existing = db.execute(
                "SELECT * FROM adaptive_questions WHERE meal_id=? AND user_id=? ORDER BY issued_at DESC LIMIT 1",
                (meal_id, auth["user_id"]),
            ).fetchone()
            if existing:
                if (
                    existing["status"] == "issued"
                    and existing["meal_revision"] == meal["revision"]
                    and existing["response_revision"]
                    == participant["response_revision"]
                ):
                    return {
                        "status": "available",
                        "request_id": existing["id"],
                        "question_id": "M09",
                        "question_version": "adaptive-m09-v1",
                        "prompt": "Which sounds better for this meal?",
                        "dimension": "cuisines",
                        "choices": decode(existing["choices"], []),
                        "optional": True,
                        "purpose": "These choices lead to different preliminary shortlist orders.",
                    }
                if existing["status"] == "issued":
                    db.execute(
                        "UPDATE adaptive_questions SET status='stale' WHERE id=?",
                        (existing["id"],),
                    )
                    return {"status": "already_stale", "question_id": "M09"}
                return {"status": f"already_{existing['status']}", "question_id": "M09"}

        deterministic = getattr(recommend, "recommender", None)
        question = (
            build_m09_question(deterministic, snapshot, auth["user_id"])
            if deterministic is not None
            else None
        )
        authenticate(request)
        with store.transaction() as db:
            meal, participant, _ = adaptive_context(db, meal_id, auth["user_id"], body)
            existing = db.execute(
                "SELECT status FROM adaptive_questions WHERE meal_id=? AND user_id=? LIMIT 1",
                (meal_id, auth["user_id"]),
            ).fetchone()
            if existing:
                return {"status": f"already_{existing['status']}", "question_id": "M09"}
            if question is None:
                return {
                    "status": "not_needed",
                    "question_id": "M09",
                    "reason": "No optional choice was found that would change the preliminary order.",
                }
            request_id = new_id()
            db.execute(
                "INSERT INTO adaptive_questions VALUES(?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    meal_id,
                    auth["user_id"],
                    time.time(),
                    meal["revision"],
                    participant["response_revision"],
                    encode(question["choices"]),
                    "issued",
                ),
            )
            audit(
                db,
                auth["user_id"],
                "adaptive_question_issued",
                meal["room_id"],
                meal_id,
            )
        return {**question, "status": "available", "request_id": request_id}

    @router.post("/meals/{meal_id}/adaptive-question/{request_id}")
    def answer_adaptive_question(
        meal_id: str, request_id: str, body: AdaptiveQuestionAnswer, auth=Auth
    ):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            if datetime.fromisoformat(decode(meal["payload"])["decision_by"]) <= now():
                raise HTTPException(
                    409, "This meal no longer accepts an optional follow-up"
                )
            question = db.execute(
                "SELECT * FROM adaptive_questions WHERE id=? AND meal_id=? AND user_id=?",
                (request_id, meal_id, auth["user_id"]),
            ).fetchone()
            if not question:
                raise HTTPException(404, "Optional question not found")
            if question["status"] != "issued":
                raise HTTPException(409, "This optional question was already completed")
            participant = db.execute(
                "SELECT response,response_revision FROM participants WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            ).fetchone()
            stale = (
                meal["revision"] != question["meal_revision"]
                or participant["response_revision"] != question["response_revision"]
            )
            if stale:
                db.execute(
                    "UPDATE adaptive_questions SET status='stale' WHERE id=?",
                    (request_id,),
                )
            elif body.skip:
                db.execute(
                    "UPDATE adaptive_questions SET status='skipped' WHERE id=?",
                    (request_id,),
                )
                audit(
                    db,
                    auth["user_id"],
                    "adaptive_question_skipped",
                    meal["room_id"],
                    meal_id,
                )
                return meal_view(db, meal, auth["user_id"])
            else:
                choices = decode(question["choices"], [])
                if body.choice not in choices:
                    raise HTTPException(
                        422, "Choose one of the current offered answers"
                    )
                response = decode(participant["response"], {})
                response.update(taste_input_mode="structured", cuisines=[body.choice])
                db.execute(
                    "UPDATE participants SET response=?,response_revision=response_revision+1 WHERE meal_id=? AND user_id=?",
                    (encode(response), meal_id, auth["user_id"]),
                )
                db.execute(
                    "UPDATE adaptive_questions SET status='answered' WHERE id=?",
                    (request_id,),
                )
                invalidate(db, meal_id)
                audit(
                    db,
                    auth["user_id"],
                    "adaptive_question_answered",
                    meal["room_id"],
                    meal_id,
                )
                return meal_view(
                    db,
                    db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                    auth["user_id"],
                )
        raise HTTPException(
            409, "The meal or your answer changed. Refresh before continuing"
        )

    def prepare_generation(db, job):
        meal = db.execute(
            "SELECT * FROM meals WHERE id=?", (job["meal_id"],)
        ).fetchone()
        if (
            not meal
            or meal["revision"] != job["context_revision"]
            or meal["status"] != "generating"
        ):
            raise Superseded()
        try:
            meal = meal_member(
                db, meal["id"], meal["organizer_id"], organizer=True, mutable=True
            )
            room = room_member(db, meal["room_id"], meal["organizer_id"], active=True)
            if (
                room["membership_revision"] != job["membership_revision"]
                or datetime.fromisoformat(decode(meal["payload"])["decision_by"])
                <= now()
            ):
                raise Superseded()
            inputs = db.execute(
                "SELECT * FROM generation_inputs WHERE job_id=? ORDER BY user_id",
                (job["id"],),
            ).fetchall()
            included = [row["user_id"] for row in inputs]
            if len(included) < 2 or included != sorted(
                decode(meal["frozen_participants"], [])
            ):
                raise Superseded()
            snapshot = snapshot_for(db, meal, included)
            if any(
                person["profile_revision"] != old["profile_revision"]
                or person["response_revision"] != old["response_revision"]
                for person, old in zip(snapshot["participants"], inputs, strict=True)
            ):
                raise Superseded()
        except HTTPException:
            raise Superseded() from None
        if (
            job["evidence_revision"] != evidence_identity(recommend)
            or job["policy_version"] != policy_identity()
        ):
            raise Superseded()
        if job["input_fingerprint"] is not None and job[
            "input_fingerprint"
        ] != snapshot_identity(snapshot):
            raise Superseded()
        return snapshot

    def publish_generation(db, job, result):
        # Result publication, the terminal attempt and inbox notices commit together.
        result = {
            **result,
            "generation_job_id": job["id"],
            "evidence_revision": job["evidence_revision"],
        }
        meal = db.execute(
            "SELECT * FROM meals WHERE id=?", (job["meal_id"],)
        ).fetchone()
        db.execute(
            "UPDATE meals SET status=?,result=?,result_revision=? WHERE id=?",
            (result["status"], encode(result), job["context_revision"], job["meal_id"]),
        )
        db.execute(
            "INSERT OR REPLACE INTO recommendation_archives(id,meal_id,revision,payload,created_at) VALUES(?,?,?,?,?)",
            (
                f"{job['meal_id']}:{job['context_revision']}",
                job["meal_id"],
                job["context_revision"],
                encode(result),
                stamp(),
            ),
        )
        record_generation_exposures(db, job["meal_id"], job["context_revision"], result)
        notify(
            db,
            decode(meal["frozen_participants"], []),
            "recommendations_ready",
            "Your meal options are ready to review.",
            meal["room_id"],
            meal["id"],
        )
        audit(
            db,
            meal["organizer_id"],
            "generation_published",
            meal["room_id"],
            meal["id"],
        )

    generation_worker = GenerationWorker(
        store,
        prepare_generation,
        safe_recommend,
        publish_generation,
        **(generation_options or {}),
    )

    @router.post("/meals/{meal_id}/generate")
    def generate(meal_id: str, body: Generate, response: Response, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(
                db, meal_id, auth["user_id"], organizer=True, mutable=True
            )
            revision_check(meal, body.expected_revision)
            if meal["status"] == "manual_proposed":
                raise HTTPException(
                    409,
                    "Revise the manual plan or update the meal context before requesting a checked shortlist",
                )
            if meal["status"] == "generating" and not async_generation:
                raise HTTPException(409, "This version is already being checked")
            if datetime.fromisoformat(decode(meal["payload"])["decision_by"]) <= now():
                raise HTTPException(
                    409, "The decision deadline passed. Update meal times first"
                )
            rows = db.execute(
                "SELECT * FROM participants WHERE meal_id=?", (meal_id,)
            ).fetchall()
            all_ids = {r["user_id"] for r in rows}
            joined = {r["user_id"] for r in rows if r["attendance"] == "join"}
            included = (
                set(body.participant_ids)
                if body.participant_ids is not None
                else joined
            )
            pending = {r["user_id"] for r in rows if r["attendance"] == "pending"}
            if pending and not body.exclude_pending:
                raise HTTPException(
                    409,
                    "Some invitees have not answered. Explicitly confirm their exclusion or wait",
                )
            if (
                not 2 <= len(included) <= 8
                or not included.issubset(joined)
                or auth["user_id"] not in included
            ):
                raise HTTPException(
                    409,
                    "Include 2–8 joined, ready participants including the organizer",
                )
            # Changing the explicitly included set produces a fresh revision, even if answers stayed identical.
            previous = decode(meal["frozen_participants"])
            if previous is not None and set(previous) != included:
                invalidate(db, meal_id)
                meal = db.execute(
                    "SELECT * FROM meals WHERE id=?", (meal_id,)
                ).fetchone()
            if async_generation:
                previous_job = db.execute(
                    "SELECT * FROM generation_jobs WHERE meal_id=? AND context_revision=? ORDER BY rowid DESC LIMIT 1",
                    (meal_id, meal["revision"]),
                ).fetchone()
                if previous_job is not None and (
                    previous_job["status"] == "superseded"
                    or previous_job["evidence_revision"] != evidence_identity(recommend)
                    or previous_job["policy_version"] != policy_identity()
                ):
                    # Shared-version CAS also binds votes/delegation to this
                    # recommendation set, even if provider option IDs are reused.
                    invalidate(db, meal_id)
                    meal = db.execute(
                        "SELECT * FROM meals WHERE id=?", (meal_id,)
                    ).fetchone()
            snapshot = snapshot_for(db, meal, sorted(included))
            revision = meal["revision"]
            if async_generation:
                _job, created = generation_worker.enqueue(
                    db, meal, snapshot, evidence_identity(recommend)
                )
                response.status_code = 202
                if not created:
                    return meal_view(db, meal, auth["user_id"])
            db.execute(
                "UPDATE meals SET status='generating',result=NULL,result_revision=NULL,frozen_participants=? WHERE id=?",
                (encode(sorted(included)), meal_id),
            )
            db.execute("DELETE FROM votes WHERE meal_id=?", (meal_id,))
            db.execute("DELETE FROM delegations WHERE meal_id=?", (meal_id,))
            notify(
                db,
                all_ids - included,
                "meal_exclusion",
                "The organizer is checking options for a smaller participant set. You are not included in that decision.",
                meal["room_id"],
                meal_id,
            )
            audit(db, auth["user_id"], "generation_started", meal["room_id"], meal_id)
            if async_generation:
                return meal_view(
                    db,
                    db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                    auth["user_id"],
                )
        try:
            result = safe_recommend(snapshot)
        except Exception:  # noqa: BLE001 — the provider boundary must not disclose private exception text
            with store.transaction() as db:
                db.execute(
                    "UPDATE meals SET status='collecting' WHERE id=? AND revision=? AND status='generating'",
                    (meal_id, revision),
                )
            raise HTTPException(
                503,
                "Recommendations are temporarily unavailable. Your answers are saved",
            ) from None
        with store.transaction() as db:
            current = meal_member(
                db, meal_id, auth["user_id"], organizer=True, mutable=True
            )
            if current["revision"] != revision or current["status"] != "generating":
                raise HTTPException(
                    409, "Answers or membership changed while checking. Generate again"
                )
            db.execute(
                "UPDATE meals SET status=?,result=?,result_revision=? WHERE id=?",
                (result["status"], encode(result), revision, meal_id),
            )
            db.execute(
                "INSERT OR REPLACE INTO recommendation_archives(id,meal_id,revision,payload,created_at) VALUES(?,?,?,?,?)",
                (f"{meal_id}:{revision}", meal_id, revision, encode(result), stamp()),
            )
            record_generation_exposures(db, meal_id, revision, result)
            notify(
                db,
                included,
                "recommendations_ready",
                "Your meal options are ready to review.",
                current["room_id"],
                meal_id,
            )
            audit(
                db, auth["user_id"], "generation_published", current["room_id"], meal_id
            )
            return meal_view(
                db,
                db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                auth["user_id"],
            )

    @router.post("/meals/{meal_id}/votes")
    def vote(meal_id: str, body: Vote, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            revision_check(meal, body.expected_revision)
            option_check(meal, body.option_id)
            if auth["user_id"] not in decode(meal["frozen_participants"], []):
                raise HTTPException(
                    403, "You are not included in this recommendation set"
                )
            db.execute(
                "INSERT INTO votes(meal_id,user_id,option_id,revision,approve,choice,reason) VALUES(?,?,?,?,?,?,?) ON CONFLICT(meal_id,user_id,option_id) DO UPDATE SET revision=excluded.revision,approve=excluded.approve,choice=excluded.choice,reason=excluded.reason",
                (
                    meal_id,
                    auth["user_id"],
                    body.option_id,
                    meal["revision"],
                    int(body.choice == "works"),
                    body.choice,
                    body.reason,
                ),
            )
            outlet_id = next(
                (
                    opt.get("outlet_id")
                    for opt in decode(meal["result"], {}).get("options", [])
                    if opt.get("id") == body.option_id
                    or opt.get("option_id") == body.option_id
                ),
                body.option_id,
            )
            policy_version = decode(meal["result"], {}).get("policy_version", "1.0")
            record_exposure_event(
                db,
                EVENT_CANDIDATE_VOTED_ON,
                meal_id=meal_id,
                meal_revision=meal["revision"],
                policy_version=policy_version,
                outlet_id=outlet_id,
                option_id=body.option_id,
                user_id=auth["user_id"],
                metadata={"choice": body.choice, "approved": body.choice == "works"},
            )
            audit(db, auth["user_id"], "private_vote_updated", meal["room_id"], meal_id)
            return meal_view(db, meal, auth["user_id"])

    @router.post("/meals/{meal_id}/delegation")
    def delegate(meal_id: str, body: Delegation, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            revision_check(meal, body.expected_revision)
            if auth["user_id"] not in decode(meal["frozen_participants"], []):
                raise HTTPException(
                    403, "You are not included in this recommendation set"
                )
            options = decode(meal["result"], {}).get("options", [])
            if body.enabled:
                ids = [
                    str(option.get("id", option.get("option_id", "")))
                    for option in options
                ]
                if not ids:
                    raise HTTPException(
                        409, "Generate a checked shortlist before delegating"
                    )
                for option_id in ids:
                    option_check(meal, option_id)
                db.execute(
                    "INSERT INTO delegations VALUES(?,?,?,?,?) ON CONFLICT(meal_id,user_id) DO UPDATE SET revision=excluded.revision,option_ids=excluded.option_ids,created_at=excluded.created_at",
                    (meal_id, auth["user_id"], meal["revision"], encode(ids), stamp()),
                )
            else:
                db.execute(
                    "DELETE FROM delegations WHERE meal_id=? AND user_id=?",
                    (meal_id, auth["user_id"]),
                )
            audit(db, auth["user_id"], "delegation_updated", meal["room_id"], meal_id)
            return meal_view(db, meal, auth["user_id"])

    @router.post("/meals/{meal_id}/select")
    def select(meal_id: str, body: Select, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(
                db, meal_id, auth["user_id"], organizer=True, mutable=True
            )
            revision_check(meal, body.expected_revision)
            included = decode(meal["frozen_participants"], [])
            if len(included) < 2:
                raise HTTPException(
                    409,
                    "Every included participant must approve this option or delegate within this shortlist",
                )
            prior_result = decode(meal["result"], {})
            options = prior_result.get("options", [])
            target_option_id = body.option_id

            if body.choice_mode == "manual":
                if not target_option_id:
                    raise HTTPException(
                        422, "Provide an option_id for manual selection"
                    )
                option_check(meal, target_option_id)
                approvals = approved_users(db, meal, target_option_id)
                if set(included) != approvals:
                    raise HTTPException(
                        409,
                        "Every included participant must approve this option or delegate within this shortlist",
                    )
            elif body.choice_mode in {"tie_break", "random_draw"}:
                accepted_options = [
                    opt
                    for opt in options
                    if approved_users(
                        db, meal, str(opt.get("id", opt.get("option_id", "")))
                    )
                    == set(included)
                ]
                if not accepted_options:
                    raise HTTPException(
                        409,
                        "No option has been approved by every included participant",
                    )
                if body.choice_mode == "tie_break":

                    def tie_break_key(opt):
                        fit_score = opt.get("_score") or opt.get("score") or 0.0
                        dist = opt.get("distance_km", 999.0)
                        ev_count = len(opt.get("evidence", []))
                        venue_id = str(opt.get("outlet_id") or opt.get("id") or "")
                        return (-fit_score, dist, -ev_count, venue_id)

                    best = min(accepted_options, key=tie_break_key)
                    target_option_id = str(best.get("id", best.get("option_id", "")))
                else:
                    import random

                    draw_choice = random.choice(accepted_options)
                    target_option_id = str(
                        draw_choice.get("id", draw_choice.get("option_id", ""))
                    )
            else:
                raise HTTPException(422, f"Unknown choice_mode: {body.choice_mode}")

            snapshot = snapshot_for(db, meal, included)
            prior_result = decode(meal["result"], {})
        try:
            checked = safe_recommend(snapshot, revalidate=True)
        except Exception:  # noqa: BLE001 — the provider boundary must not disclose private exception text
            raise HTTPException(
                503, "The final evidence check is unavailable. Your votes are saved"
            ) from None
        still_valid = (
            checked.get("status") == "shortlisted"
            and any(
                str(o.get("id", o.get("option_id", ""))) == target_option_id
                for o in checked.get("options", [])
            )
            and checked.get("catalog_version") == prior_result.get("catalog_version")
            and checked.get("catalog_id") == prior_result.get("catalog_id")
            and (
                not prior_result.get("evidence_revision")
                or prior_result["evidence_revision"] == evidence_identity(recommend)
            )
        )
        with store.transaction() as db:
            meal = meal_member(
                db, meal_id, auth["user_id"], organizer=True, mutable=True
            )
            revision_check(meal, body.expected_revision)
            # Re-read votes after potentially slow external checks; a participant may revoke approval.
            approvals = approved_users(db, meal, target_option_id)
            if approvals != set(included):
                raise HTTPException(409, "An approval changed during the final check")
            if not still_valid or (
                prior_result.get("evidence_revision")
                and prior_result["evidence_revision"] != evidence_identity(recommend)
            ):
                invalidate(db, meal_id)
            else:
                option_check(meal, target_option_id)
                decision = {
                    "option_id": target_option_id,
                    "selected_by": auth["user_id"],
                    "selected_at": stamp(),
                    "revision": meal["revision"],
                    "participant_ids": included,
                    "checked": True,
                    "choice_mode": body.choice_mode,
                }
                db.execute(
                    "UPDATE meals SET status='selected',decision=?,reconfirmation_required=0,lifecycle_reason=NULL WHERE id=?",
                    (encode(decision), meal_id),
                )
                outlet_id = next(
                    (
                        opt.get("outlet_id")
                        for opt in prior_result.get("options", [])
                        if opt.get("id") == target_option_id
                        or opt.get("option_id") == target_option_id
                    ),
                    target_option_id,
                )
                record_exposure_event(
                    db,
                    EVENT_CANDIDATE_SELECTED,
                    meal_id=meal_id,
                    meal_revision=meal["revision"],
                    policy_version=prior_result.get("policy_version", "1.0"),
                    outlet_id=outlet_id,
                    option_id=target_option_id,
                    user_id=auth["user_id"],
                    metadata={"choice_mode": body.choice_mode},
                )
                msg = (
                    "Everyone approved a meal option. A random draw selected the final place."
                    if body.choice_mode == "random_draw"
                    else "Everyone approved a meal option. A score tie-break selected the final place."
                    if body.choice_mode == "tie_break"
                    else "Everyone approved a meal option. View the shared decision."
                )
                notify(
                    db,
                    included,
                    "meal_selected",
                    msg,
                    meal["room_id"],
                    meal_id,
                )
                audit(db, auth["user_id"], "meal_selected", meal["room_id"], meal_id)
                return meal_view(
                    db,
                    db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                    auth["user_id"],
                )
        raise HTTPException(
            409, "Restaurant evidence changed. Review a new recommendation set"
        )

    @router.post("/meals/{meal_id}/manual-plan")
    def propose_manual(meal_id: str, body: ManualPlan, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(
                db, meal_id, auth["user_id"], organizer=True, mutable=True
            )
            revision_check(meal, body.expected_revision)
            if datetime.fromisoformat(decode(meal["payload"])["decision_by"]) <= now():
                raise HTTPException(
                    409, "Extend the decision deadline before proposing a manual plan"
                )
            rows = db.execute(
                "SELECT user_id,attendance FROM participants WHERE meal_id=?",
                (meal_id,),
            ).fetchall()
            joined = {p["user_id"] for p in rows if p["attendance"] == "join"}
            included = (
                set(body.participant_ids)
                if body.participant_ids is not None
                else joined
            )
            if (
                any(p["attendance"] == "pending" for p in rows)
                and not body.exclude_pending
            ):
                raise HTTPException(
                    409, "Wait for pending invitees or explicitly exclude them"
                )
            if (
                not 2 <= len(included) <= 8
                or not included.issubset(joined)
                or auth["user_id"] not in included
            ):
                raise HTTPException(
                    409, "Include 2–8 joined participants including the organizer"
                )
            invalidate(db, meal_id)
            plan = {
                "id": new_id(),
                "name": body.name,
                "address": body.address,
                "note": body.note,
                "unverified": True,
                "requirements_status": "not_verified",
                "notice": "A plan proposed by your group. Restaurant, price and dietary checks remain unresolved; acknowledgements do not establish suitability.",
            }
            db.execute(
                "UPDATE meals SET status='manual_proposed',manual_plan=?,frozen_participants=? WHERE id=?",
                (encode(plan), encode(sorted(included)), meal_id),
            )
            notify(
                db,
                included,
                "manual_plan_proposed",
                "Your group proposed an unverified plan. Review the unresolved checks before acknowledging.",
                meal["room_id"],
                meal_id,
            )
            audit(db, auth["user_id"], "manual_plan_proposed", meal["room_id"], meal_id)
            return meal_view(
                db,
                db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                auth["user_id"],
            )

    def manual_scope(db, meal_id, expected, auth, organizer=False):
        meal = meal_member(
            db, meal_id, auth["user_id"], organizer=organizer, mutable=True
        )
        revision_check(meal, expected)
        if meal["status"] != "manual_proposed" or not meal["manual_plan"]:
            raise HTTPException(409, "Review the current manual plan first")
        included = set(decode(meal["frozen_participants"], []))
        if auth["user_id"] not in included:
            raise HTTPException(403, "You are not included in this manual plan")
        return meal, included

    @router.post("/meals/{meal_id}/manual-ack")
    def acknowledge_manual(meal_id: str, body: ManualAcknowledgement, auth=Auth):
        with store.transaction() as db:
            meal, _ = manual_scope(db, meal_id, body.expected_revision, auth)
            if body.acknowledge_unverified:
                db.execute(
                    "INSERT INTO manual_acknowledgements VALUES(?,?,?,?) ON CONFLICT(meal_id,user_id) DO UPDATE SET revision=excluded.revision,created_at=excluded.created_at",
                    (meal_id, auth["user_id"], meal["revision"], stamp()),
                )
            else:
                db.execute(
                    "DELETE FROM manual_acknowledgements WHERE meal_id=? AND user_id=?",
                    (meal_id, auth["user_id"]),
                )
            audit(
                db,
                auth["user_id"],
                "manual_plan_acknowledgement",
                meal["room_id"],
                meal_id,
            )
            return meal_view(db, meal, auth["user_id"])

    @router.post("/meals/{meal_id}/manual-select")
    def select_manual(meal_id: str, body: Cancel, auth=Auth):
        with store.transaction() as db:
            meal, included = manual_scope(
                db, meal_id, body.expected_revision, auth, organizer=True
            )
            acknowledged = {
                r["user_id"]
                for r in db.execute(
                    "SELECT user_id FROM manual_acknowledgements WHERE meal_id=? AND revision=?",
                    (meal_id, meal["revision"]),
                )
            }
            if len(included) < 2 or included != acknowledged:
                raise HTTPException(
                    409,
                    "Every included participant must acknowledge the unresolved checks for this exact plan",
                )
            plan = decode(meal["manual_plan"])
            decision = {
                "option_id": plan["id"],
                "selected_by": auth["user_id"],
                "selected_at": stamp(),
                "revision": meal["revision"],
                "participant_ids": sorted(included),
                "checked": False,
                "choice_mode": "manual_unverified",
                "requirements_status": "not_verified",
            }
            db.execute(
                "UPDATE meals SET status='manual_selected',decision=?,reconfirmation_required=0 WHERE id=?",
                (encode(decision), meal_id),
            )
            notify(
                db,
                included,
                "manual_plan_recorded",
                "Your group recorded an unverified plan. Restaurant checks remain unresolved.",
                meal["room_id"],
                meal_id,
            )
            audit(
                db, auth["user_id"], "manual_choice_recorded", meal["room_id"], meal_id
            )
            return meal_view(
                db,
                db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                auth["user_id"],
            )

    @router.post("/meals/{meal_id}/feedback")
    def feedback(meal_id: str, body: Feedback, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"])
            decision = decode(meal["decision"], {})
            if (
                meal["status"]
                not in {"selected", "manual_selected", "awaiting_feedback"}
                or auth["user_id"] not in decision.get("participant_ids", [])
                or body.option_id != decision.get("option_id")
            ):
                raise HTTPException(
                    409, "Feedback must refer to your selected meal option"
                )
            if (
                body.visited
                and datetime.fromisoformat(decode(meal["payload"])["meal_at"])
                + timedelta(minutes=decode(meal["payload"]).get("duration_minutes", 60))
                > now()
            ):
                raise HTTPException(
                    409, "Confirm your visit after the planned meal finishes"
                )
            payload = body.model_dump()
            db.execute(
                "INSERT INTO feedback VALUES(?,?,?,?) ON CONFLICT(meal_id,user_id) DO UPDATE SET payload=excluded.payload,created_at=excluded.created_at",
                (meal_id, auth["user_id"], encode(payload), stamp()),
            )
            profile = decode(
                db.execute(
                    "SELECT profile FROM users WHERE id=?", (auth["user_id"],)
                ).fetchone()["profile"]
            )
            previous_observations = [
                r["id"]
                for r in db.execute(
                    "SELECT id FROM observations WHERE user_id=? AND meal_id=?",
                    (auth["user_id"], meal_id),
                )
            ]
            for observation in previous_observations:
                remove_observation(db, auth["user_id"], observation)

            if (
                profile.get("memory_enabled")
                and decision.get("checked", meal["result"] is not None) is True
                and body.visited
                and (
                    body.rating is not None
                    or body.would_repeat is not None
                    or body.enjoyment not in {None, "skipped"}
                )
            ):
                # Observations never rewrite confirmed profile requirements or preferences.
                option = next(
                    (
                        o
                        for o in decode(meal["result"], {}).get("options", [])
                        if str(o.get("id", o.get("option_id"))) == body.option_id
                    ),
                    {},
                )
                attr_signals = extract_attribute_signals(body.model_dump())
                observation_id = new_id()
                db.execute(
                    "INSERT INTO observations VALUES(?,?,?,?,?)",
                    (
                        observation_id,
                        auth["user_id"],
                        meal_id,
                        encode(
                            {
                                "outlet_id": option.get("outlet_id"),
                                "rating": body.rating,
                                "would_repeat": body.would_repeat,
                                "source": "confirmed_visit",
                                "enjoyment": body.enjoyment,
                                "influences": body.influences,
                                "cost_expectation": body.cost_expectation,
                                "actual_cost_minor": body.actual_cost_minor,
                                "dish_text": body.dish_text,
                                "fairness": body.fairness,
                                "attribute_signals": attr_signals,
                            }
                        ),
                        stamp(),
                    ),
                )
                if body.would_repeat is not None and option.get("outlet_id"):
                    propose_repeat_pattern(db, auth["user_id"], option["outlet_id"])
                propose_attribute_patterns(db, auth["user_id"], observation_id)
            # Feedback changes personal taste evidence, never another person's constraints.
            for upcoming in db.execute(
                "SELECT meal_id FROM participants WHERE user_id=? AND meal_id!=?",
                (auth["user_id"], meal_id),
            ).fetchall():
                invalidate(db, upcoming["meal_id"])
            audit(
                db, auth["user_id"], "private_feedback_saved", meal["room_id"], meal_id
            )
            return meal_view(db, meal, auth["user_id"])

    @router.post("/meals/{meal_id}/report-data-error")
    def report_data_error(meal_id: str, body: DataErrorReport, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"])
            report_id = f"rpt_{secrets.token_hex(8)}"
            now_stamp = stamp()
            db.execute(
                "INSERT INTO data_error_reports(id,meal_id,user_id,outlet_id,category,description,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    report_id,
                    meal_id,
                    auth["user_id"],
                    body.outlet_id,
                    body.category,
                    body.description,
                    "investigating",
                    now_stamp,
                ),
            )
            audit(db, auth["user_id"], "data_error_reported", meal["room_id"], meal_id)
            return {
                "report_id": report_id,
                "status": "investigating",
                "message": "Your report was logged for operator investigation. It is kept separate from taste learning.",
                "created_at": now_stamp,
            }

    def remove_observation(db, user_id, observation_id):
        own = db.execute(
            "SELECT id FROM observations WHERE id=? AND user_id=?",
            (observation_id, user_id),
        ).fetchone()
        if own is None:
            return False
        # Any source correction/deletion removes the dependent inference, even after acceptance.
        db.execute(
            "DELETE FROM preference_proposals WHERE user_id=? AND id IN (SELECT proposal_id FROM proposal_sources WHERE observation_id=?)",
            (user_id, observation_id),
        )
        db.execute(
            "DELETE FROM observations WHERE id=? AND user_id=?",
            (observation_id, user_id),
        )
        return True

    def propose_repeat_pattern(db, user_id, outlet_id):
        records = db.execute(
            "SELECT * FROM observations WHERE user_id=? AND json_extract(payload,'$.outlet_id')=? AND json_extract(payload,'$.would_repeat') IS NOT NULL ORDER BY created_at DESC LIMIT 3",
            (user_id, outlet_id),
        ).fetchall()
        if len(records) < 3 or len({r["meal_id"] for r in records}) < 3:
            return
        signals = {decode(r["payload"])["would_repeat"] for r in records}
        if len(signals) != 1:
            return
        repeat = int(signals.pop())
        # Confirmed preferences remain authoritative; no automatic contradictory promotion.
        if db.execute(
            "SELECT 1 FROM preference_proposals WHERE user_id=? AND outlet_id=? AND status IN ('pending','accepted')",
            (user_id, outlet_id),
        ).fetchone():
            return
        if db.execute(
            "SELECT 1 FROM learning_suggestion_log WHERE user_id=? AND created_at>=?",
            (user_id, (now() - timedelta(days=30)).isoformat()),
        ).fetchone():
            return
        if db.execute(
            "SELECT 1 FROM learning_suggestion_log WHERE user_id=? AND outlet_id=? AND would_repeat=? AND decision='reject' AND reviewed_at>=?",
            (user_id, outlet_id, repeat, (now() - timedelta(days=90)).isoformat()),
        ).fetchone():
            return
        proposal_id = new_id()
        db.execute(
            "INSERT INTO preference_proposals(id,user_id,observation_id,outlet_id,would_repeat,created_at) VALUES(?,?,?,?,?,?)",
            (proposal_id, user_id, records[0]["id"], outlet_id, repeat, stamp()),
        )
        db.executemany(
            "INSERT INTO proposal_sources VALUES(?,?)",
            [(proposal_id, r["id"]) for r in records],
        )
        db.execute(
            "INSERT INTO learning_suggestion_log(id,user_id,outlet_id,would_repeat,created_at) VALUES(?,?,?,?,?)",
            (proposal_id, user_id, outlet_id, repeat, stamp()),
        )

    def learning_view(db, user_id):
        user = db.execute("SELECT profile FROM users WHERE id=?", (user_id,)).fetchone()
        observations = [
            {
                "id": row["id"],
                "meal_id": row["meal_id"],
                "created_at": row["created_at"],
                **decode(row["payload"]),
            }
            for row in db.execute(
                "SELECT * FROM observations WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            )
        ]
        proposals = []
        for raw_row in db.execute(
            "SELECT * FROM preference_proposals WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        ):
            r = dict(raw_row)
            proposals.append(
                {
                    "id": r["id"],
                    "proposal_type": r.get("proposal_type") or "venue",
                    "attribute": r.get("attribute"),
                    "proposed_value": r.get("proposed_value"),
                    "description": r.get("description"),
                    "observation_id": r["observation_id"],
                    "source_observation_ids": [
                        source["observation_id"]
                        for source in db.execute(
                            "SELECT observation_id FROM proposal_sources WHERE proposal_id=?",
                            (r["id"],),
                        )
                    ],
                    "outlet_id": r["outlet_id"],
                    "would_repeat": bool(r["would_repeat"]),
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "reviewed_at": r["reviewed_at"],
                }
            )
        return {
            "enabled": bool(decode(user["profile"]).get("memory_enabled")),
            "observations": observations,
            "proposals": proposals,
            "venue_preferences": [
                p
                for p in proposals
                if p["status"] == "accepted" and p["proposal_type"] == "venue"
            ],
            "attribute_preferences": [
                p
                for p in proposals
                if p["status"] == "accepted" and p["proposal_type"] == "attribute"
            ],
        }

    def invalidate_learning(db, user_id):
        for upcoming in db.execute(
            "SELECT meal_id FROM participants WHERE user_id=?", (user_id,)
        ).fetchall():
            invalidate(db, upcoming["meal_id"])

    @router.get("/learning")
    def learning(auth=Auth):
        with store.transaction() as db:
            return learning_view(db, auth["user_id"])

    @router.delete("/learning/observations/{observation_id}")
    def delete_observation(observation_id: str, auth=Auth):
        with store.transaction() as db:
            if not remove_observation(db, auth["user_id"], observation_id):
                raise HTTPException(404, "Observation not found")
            invalidate_learning(db, auth["user_id"])
            audit(db, auth["user_id"], "learning_observation_deleted")
            return {"ok": True}

    @router.post("/learning/clear")
    def clear_learning(auth=Auth):
        with store.transaction() as db:
            db.execute("DELETE FROM observations WHERE user_id=?", (auth["user_id"],))
            db.execute(
                "DELETE FROM learning_suggestion_log WHERE user_id=?",
                (auth["user_id"],),
            )
            invalidate_learning(db, auth["user_id"])
            audit(db, auth["user_id"], "learning_cleared")
            return {"ok": True, **learning_view(db, auth["user_id"])}

    @router.post("/learning/proposals/{proposal_id}")
    def review_preference(proposal_id: str, body: PreferenceReview, auth=Auth):
        with store.transaction() as db:
            proposal = db.execute(
                "SELECT * FROM preference_proposals WHERE id=? AND user_id=?",
                (proposal_id, auth["user_id"]),
            ).fetchone()
            if proposal is None:
                raise HTTPException(404, "Preference suggestion not found")
            if not learning_view(db, auth["user_id"])["enabled"]:
                raise HTTPException(409, "Learning is disabled")
            status = "accepted" if body.decision == "accept" else "rejected"
            if proposal["status"] != status:
                db.execute(
                    "UPDATE preference_proposals SET status=?,reviewed_at=? WHERE id=?",
                    (status, stamp(), proposal_id),
                )
                db.execute(
                    "UPDATE learning_suggestion_log SET decision=?,reviewed_at=? WHERE id=?",
                    (body.decision, stamp(), proposal_id),
                )
                invalidate_learning(db, auth["user_id"])
                audit(db, auth["user_id"], "preference_" + status)
            return {"id": proposal_id, "status": status}

    @router.get("/notifications")
    def notifications(auth=Auth):
        with store.transaction() as db:
            rows = db.execute(
                "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 100",
                (auth["user_id"],),
            ).fetchall()
            return {
                "notifications": [
                    {
                        "id": r["id"],
                        "room_id": r["room_id"],
                        "meal_id": r["meal_id"],
                        "kind": r["kind"],
                        "message": r["message"],
                        "created_at": r["created_at"],
                        "read": bool(r["read"]),
                    }
                    for r in rows
                ]
            }

    @router.post("/notifications/{notification_id}/read")
    def read_notification(notification_id: str, auth=Auth):
        with store.transaction() as db:
            cursor = db.execute(
                "UPDATE notifications SET read=1 WHERE id=? AND user_id=?",
                (notification_id, auth["user_id"]),
            )
            if cursor.rowcount == 0:
                raise HTTPException(404, "Notification not found")
        return {"ok": True}

    @router.get("/export")
    def export(auth=Auth):
        with store.transaction() as db:
            user = db.execute(
                "SELECT * FROM users WHERE id=?", (auth["user_id"],)
            ).fetchone()
            return {
                "exported_at": stamp(),
                "user": user_view(user),
                "profile": decode(user["profile"]),
                "room_memberships": [
                    {"room_id": r["room_id"], "joined_at": r["joined_at"]}
                    for r in db.execute(
                        "SELECT * FROM members WHERE user_id=?", (auth["user_id"],)
                    )
                ],
                "meal_responses": [
                    {
                        "meal_id": r["meal_id"],
                        "attendance": r["attendance"],
                        "response": decode(r["response"]),
                    }
                    for r in db.execute(
                        "SELECT * FROM participants WHERE user_id=?", (auth["user_id"],)
                    )
                ],
                "feedback": [
                    {"meal_id": r["meal_id"], **decode(r["payload"])}
                    for r in db.execute(
                        "SELECT * FROM feedback WHERE user_id=?", (auth["user_id"],)
                    )
                ],
                "private_votes": [
                    {
                        "meal_id": r["meal_id"],
                        "option_id": r["option_id"],
                        "revision": r["revision"],
                        "choice": r["choice"],
                        "reason": r["reason"],
                    }
                    for r in db.execute(
                        "SELECT * FROM votes WHERE user_id=?", (auth["user_id"],)
                    )
                ],
                "delegations": [
                    {
                        "meal_id": r["meal_id"],
                        "revision": r["revision"],
                        "option_ids": decode(r["option_ids"]),
                        "created_at": r["created_at"],
                    }
                    for r in db.execute(
                        "SELECT * FROM delegations WHERE user_id=?", (auth["user_id"],)
                    )
                ],
                "manual_acknowledgements": [
                    {
                        "meal_id": r["meal_id"],
                        "revision": r["revision"],
                        "created_at": r["created_at"],
                    }
                    for r in db.execute(
                        "SELECT * FROM manual_acknowledgements WHERE user_id=?",
                        (auth["user_id"],),
                    )
                ],
                "observations": learning_view(db, auth["user_id"])["observations"],
                "preference_proposals": learning_view(db, auth["user_id"])["proposals"],
                "data_error_reports": [
                    {
                        "id": r["id"],
                        "meal_id": r["meal_id"],
                        "outlet_id": r["outlet_id"],
                        "category": r["category"],
                        "description": r["description"],
                        "status": r["status"],
                        "created_at": r["created_at"],
                    }
                    for r in db.execute(
                        "SELECT * FROM data_error_reports WHERE user_id=?",
                        (auth["user_id"],),
                    )
                ],
                "meal_origins": [
                    {
                        "meal_id": r["meal_id"],
                        "origin_mode": r["origin_mode"],
                        "latitude": r["latitude"],
                        "longitude": r["longitude"],
                        "approximate_area": r["approximate_area"],
                        "route_consent": bool(r["route_consent"]),
                        "created_at": r["created_at"],
                        "updated_at": r["updated_at"],
                    }
                    for r in db.execute(
                        "SELECT * FROM meal_origins WHERE user_id=?",
                        (auth["user_id"],),
                    )
                ],
                "exposure_events": [
                    {
                        "meal_id": r["meal_id"],
                        "meal_revision": r["meal_revision"],
                        "event_type": r["event_type"],
                        "outlet_id": r["outlet_id"],
                        "option_id": r["option_id"],
                        "created_at": r["created_at"],
                    }
                    for r in db.execute(
                        "SELECT * FROM exposure_events WHERE user_id=?",
                        (auth["user_id"],),
                    )
                ],
                "consent_records": [
                    {
                        "id": r["id"],
                        "notice_version": r["notice_version"],
                        "purpose": r["purpose"],
                        "decision": r["decision"],
                        "source_interface": r["source_interface"],
                        "timestamp": r["timestamp"],
                        "created_at": r["created_at"],
                    }
                    for r in db.execute(
                        "SELECT * FROM consent_records WHERE user_id=? ORDER BY created_at",
                        (auth["user_id"],),
                    )
                ],
                "push_subscriptions": [
                    {
                        "id": r["id"],
                        "endpoint": r["endpoint"],
                        "status": r["status"],
                        "created_at": r["created_at"],
                    }
                    for r in db.execute(
                        "SELECT id, endpoint, status, created_at FROM push_subscriptions WHERE user_id=?",
                        (auth["user_id"],),
                    )
                ],
            }

    @router.delete("/account")
    def delete_account(response: Response, auth=Auth):
        with store.transaction() as db:
            rooms = db.execute(
                "SELECT r.* FROM rooms r JOIN members m ON m.room_id=r.id WHERE m.user_id=?",
                (auth["user_id"],),
            ).fetchall()
            for room in rooms:
                if room["owner_id"] == auth["user_id"]:
                    db.execute(
                        "UPDATE rooms SET archived=1,invite_hash=NULL,invite_expires=NULL WHERE id=?",
                        (room["id"],),
                    )
                    cancel_upcoming_room_meals(db, room["id"])
                else:
                    remove_member(db, room, auth["user_id"], auth["user_id"])
            # Remove the deleted account ID from shared immutable decision summaries.
            for meal in db.execute(
                "SELECT id,decision,frozen_participants FROM meals WHERE decision IS NOT NULL OR frozen_participants IS NOT NULL"
            ).fetchall():
                decision = decode(meal["decision"])
                if decision is not None:
                    decision["participant_ids"] = [
                        uid
                        for uid in decision.get("participant_ids", [])
                        if uid != auth["user_id"]
                    ]
                    if decision.get("selected_by") == auth["user_id"]:
                        decision["selected_by"] = None
                frozen = [
                    uid
                    for uid in decode(meal["frozen_participants"], [])
                    if uid != auth["user_id"]
                ]
                db.execute(
                    "UPDATE meals SET decision=?,frozen_participants=? WHERE id=?",
                    (
                        encode(decision) if decision is not None else None,
                        encode(frozen),
                        meal["id"],
                    ),
                )
            db.execute("DELETE FROM users WHERE id=?", (auth["user_id"],))
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
        return {
            "ok": True,
            "message": "Account and private records deleted. Rooms you owned were archived.",
        }

    router.generation_worker = generation_worker
    router.authenticate = authenticate
    router.room_member = room_member
    return router
