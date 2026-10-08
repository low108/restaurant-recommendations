"""Authenticated, purpose-limited HTTP API for the group dining pilot.

The application enforces room boundaries and immutable recommendation revisions;
LLM output never supplies authorization or a participant's private requirements.

This module is the composition root. The pieces live in:

* :mod:`dining.api.common`     – clock, ids, hashing and session settings
* :mod:`dining.api.schemas`    – request bodies
* :mod:`dining.api.context`    – :class:`ApiContext`, the shared helpers every route uses
* :mod:`dining.api.generation` – :class:`GenerationPipeline` (snapshot → recommend → publish)
* :mod:`dining.api.routes`         – route groups (accounts, rooms, meals, adaptive, decisions, learning)
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter

from dining.api.common import (  # noqa: F401 - re-exported for callers and tests
    COOKIE,
    DUMMY_PASSWORD_HASH,
    PASSWORD_HASHER,
    SESSION_SECONDS,
    digest,
    new_id,
    stamp,
    system_now,
)
from dining.api.context import ApiContext
from dining.api.generation import GenerationPipeline
from dining.api.routes import ROUTE_MODULES
from dining.api.schemas import (  # noqa: F401 - request models stay importable from dining.api
    AdaptiveQuestionAnswer,
    AdaptiveQuestionRequest,
    Cancel,
    CheckIn,
    ConsentInput,
    Credentials,
    DataErrorReport,
    Delegation,
    Feedback,
    Generate,
    Input,
    InterpretPreferences,
    Join,
    ManualAcknowledgement,
    ManualPlan,
    MealCreate,
    MealOriginInput,
    ParticipantSet,
    PersonalAction,
    PreferenceReview,
    PreparationConfirmationInput,
    Profile,
    PushSubscriptionInput,
    PushUnsubscribeInput,
    Registration,
    RoomCreate,
    Select,
    Tags,
    Transfer,
    Vote,
)
from dining.core.store import DiningStore


def now() -> datetime:
    """The API clock (UTC). Tests freeze time for every route by patching this function."""
    return system_now()


def build_router(
    store: DiningStore,
    recommend: Callable[[dict], dict],
    demo_mode: bool = False,
    account_access: Callable[[str], None] | None = None,
    async_generation: bool = False,
    generation_options: dict | None = None,
) -> APIRouter:
    """Build the ``/api`` router around one store and one recommender."""
    router = APIRouter(prefix="/api")
    ctx = ApiContext(
        store,
        recommend,
        demo_mode=demo_mode,
        account_access=account_access,
        async_generation=async_generation,
        generation_options=generation_options,
    )
    GenerationPipeline(ctx)  # attaches ctx.generation and ctx.generation_worker
    for module in ROUTE_MODULES:
        module.register(router, ctx)

    # Handles used by the web app (worker lifecycle) and by tests.
    router.generation_worker = ctx.generation_worker
    router.authenticate = ctx.authenticate
    router.room_member = ctx.room_member
    return router
