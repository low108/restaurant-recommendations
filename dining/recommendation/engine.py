"""Group recommendation pipeline: one frozen meal snapshot in, one shortlist result out.

The :class:`Recommender` is deterministic and outlet-level. Menu similarity never grants
eligibility; it only helps find and order candidates. The pipeline runs in five steps:

1. **Readiness gate** – enough people, a place and time, and every requirement answered.
2. **Area search** – outlets inside the meal's radius, optionally narrowed and ordered by
   semantic menu search (``dining.retrieval.index``).
3. **Outlet assessment** – hard checks per outlet (hours, halal, access, allergy
   confirmations, travel) and the best eligible dish for every diner, scored by the active
   :mod:`scoring policy <dining.recommendation.scoring_policies>`.
4. **Coverage** – which outlet best serves each diner's own wish.
5. **Shortlist** – group score, fairness floor, diversity and coverage
   (:func:`dining.recommendation.ranking.rank_diverse`), then the result status.

``semantic_candidate_search`` and ``rank_diverse`` are looked up on this module at call
time, so tests and evaluation scripts can substitute them.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from datetime import time as local_time
from zoneinfo import ZoneInfo

from dining.catalog.audit import (
    dietary_claim_conflicts,
    mentions_animal,
    public_catalog_status,
)
from dining.catalog.models import Catalog, MenuItem, Outlet
from dining.core.constants import (
    PER_WEIGHT_UNITS,
    PLANT_BASED_DIETS,
    RETRIEVAL_POLICY_VERSION,
    UNDISCLOSED_ALLERGY_STATUSES,
    UNREVIEWED_HALAL_POLICIES,
)
from dining.location.routing import (
    RouteCoordinate,
    RouteEvidence,
    RouteRequest,
    RouteStatus,
    RoutingProvider,
    get_routing_provider,
)
from dining.recommendation.ranking import (
    CONFIRMED_MEAL_ROLES,
    FEATURE_VERSION,
    LOW_COVERAGE_THRESHOLD,
    MINIMUM_INDIVIDUAL_FIT,
    NEUTRAL_WORDS,
    NON_MEAL_ROLES,
    ONTOLOGY_VERSION,
    PER_WEIGHT,
    POLICY_VERSION,
    base_score,
    craving_name_overlap,
    novelty_feature,
    rank_diverse,
)
from dining.recommendation.scoring_policies import create_scoring_policy
from dining.recommendation.service_hours import (
    ServiceCheck,
    open_for,
    service_for,
    weekly_service,
)
from dining.retrieval.index import (
    EMBEDDING_MODEL_NAME,
    CatalogEmbeddingIndex,
    RetrievalQuery,
    semantic_candidate_search,
)

# Backwards-compatible names (tests and older callers import these from here).
_service_for = service_for
_open_for = open_for
_weekly_service = weekly_service
__all__ = ["Recommender", "ServiceCheck", "distance_km"]

DEFAULT_RADIUS_KM = 5
DEFAULT_DURATION_MINUTES = 60
MAX_RETRIEVAL_QUERIES = 8
MAX_RETRIEVED_OUTLETS = 30
# Accessibility needs that are hard requirements when a diner asks for them.
FIRM_ACCESSIBILITY_FEATURES = frozenset(
    {
        "step_free_entrance",
        "wheelchair_accessible_seating",
        "accessible_restroom",
        "low_noise_seating",
    }
)
# Shown on every "Needs confirmation" entry; deliberately says nothing about whose
# requirement or which requirement is involved.
NEEDS_CONFIRMATION_REASON = "Additional evidence or a private requirement review is needed before this place can be shortlisted."
# Private working fields carried on candidate options; removed before the result is shared.
PRIVATE_OPTION_KEYS = ("_score", "_relevance", "_serves", "_fits", "_confirmed_meal")


def distance_km(a: float, b: float, c: float, d: float) -> float:
    """Great-circle distance in km between (lat a, lng b) and (lat c, lng d) (haversine)."""
    lat1, lat2, dl, dn = map(math.radians, (a, c, c - a, d - b))
    h = math.sin(dl / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dn / 2) ** 2
    return 6371 * 2 * math.asin(min(1, math.sqrt(h)))


def _canonical(item: MenuItem) -> tuple:
    """Identity of a dish; duplicate scrape copies and IDs add no ranking evidence."""
    return (
        item.outlet_id,
        item.name.casefold().strip(),
        item.variant.casefold().strip(),
        item.menu_version,
        item.price.channel if item.price else None,
    )


def _listed_price_minor(item: MenuItem) -> int | None:
    """Payable price if known, else the listed price, else None (in sen)."""
    if item.price and item.price.payable_amount_minor is not None:
        return item.price.payable_amount_minor
    if item.price and item.price.amount_minor is not None:
        return item.price.amount_minor
    return None


def _parse_aware(value: str) -> datetime:
    """Parse an ISO timestamp, accepting a trailing ``Z``."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# --------------------------------------------------------------------------- pipeline state
@dataclass
class MealContext:
    """Everything about the meal that every pipeline step needs, parsed once."""

    snapshot: dict
    meal: dict
    people: list[dict]
    now: datetime
    at: datetime
    latitude: float
    longitude: float

    @property
    def radius_km(self) -> float:
        return self.meal.get("radius_km", DEFAULT_RADIUS_KM)

    @property
    def duration(self) -> int:
        return self.meal.get("duration_minutes", DEFAULT_DURATION_MINUTES)

    @property
    def has_allergy_needs(self) -> bool:
        """Any declared allergy or avoid list makes the session 'strict'."""
        return any(
            p["profile"].get("allergy_status") == "declared"
            or p["profile"].get("allergens")
            or p["response"].get("avoid")
            for p in self.people
        )

    @property
    def needs_certified_halal(self) -> bool:
        return any(p["profile"].get("halal_policy") == "certified" for p in self.people)


@dataclass
class RetrievalOutcome:
    """Which outlets to assess, and the search signals used for ordering and coverage."""

    outlets: list[Outlet]
    status: str = "structured_fallback"
    receipt: dict | None = None
    relevance: dict[str, float] = field(default_factory=dict)  # group search relevance
    own_relevance: dict[int, dict[str, float]] = field(
        default_factory=dict
    )  # per diner


@dataclass
class OutletLedger:
    """Running record of what happened to each assessed outlet."""

    verification: list[dict]  # shared "Needs confirmation" list (part of the result)
    candidates: list[dict] = field(default_factory=list)
    eligible: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    examined: int = 0
    preference_conflict: bool = False  # someone fell below the fairness floor
    evaluation_blocked: bool = False  # missing data prevented a judgement

    def reject(self, outlet: Outlet, category: str) -> None:
        self.rejected.append({"outlet_id": outlet.outlet_id, "category": category})

    def needs_confirmation(self, outlet: Outlet) -> None:
        self.reject(outlet, "hard_requirement")
        self.verification.append(
            {
                "outlet_id": outlet.outlet_id,
                "name": outlet.name,
                "reasons": [NEEDS_CONFIRMATION_REASON],
            }
        )


class Recommender:
    """Deterministic group recommender over one catalog (see module docstring)."""

    def __init__(
        self,
        catalog: Catalog,
        routing_provider: RoutingProvider | None = None,
        embedding_index: CatalogEmbeddingIndex | None = None,
        scoring_policy: str = POLICY_VERSION,
    ):
        self.catalog = catalog
        self.routing_provider = routing_provider or get_routing_provider()
        self.embedding_index = embedding_index
        # "prd-fit-v1" (default, PRD §9) or "content-sim-v1" (TF-IDF cosine for C and H).
        self._scorer = create_scoring_policy(scoring_policy, catalog)
        self.scoring_policy = self._scorer.name
        # Identifies the catalog content in option IDs, so IDs change when data changes.
        self.fingerprint = hashlib.sha256(
            catalog.model_dump_json().encode()
        ).hexdigest()

    def request_route(
        self,
        origin: RouteCoordinate,
        destination: RouteCoordinate,
        mobility_mode: str = "drive",
        departure_time: datetime | None = None,
        arrival_time: datetime | None = None,
    ) -> RouteEvidence:
        """Ask the routing provider for one diner's travel evidence."""
        request = RouteRequest(
            origin=origin,
            destination=destination,
            mobility_mode=mobility_mode,
            departure_time=departure_time,
            arrival_time=arrival_time,
        )
        return self.routing_provider.calculate_route(request)

    # ----------------------------------------------------------------------- entry point
    def __call__(self, snapshot: dict) -> dict:
        now = datetime.now(timezone.utc)
        ctx = self._readiness_gate(snapshot, now)
        if isinstance(ctx, dict):  # an early "needs input / verification" result
            return ctx

        retrieval = self._find_candidates(ctx)
        result = self._new_result(ctx, retrieval)
        ledger = OutletLedger(
            verification=result["verification"],
            evaluation_blocked=not self.catalog.outlets or not self.catalog.menu_items,
        )
        for outlet in retrieval.outlets:
            self._assess_outlet(ctx, outlet, retrieval, ledger, result)

        result["examined_outlets"] = ledger.examined
        result["_exposure_candidates"] = {
            "eligible": ledger.eligible,
            "rejected": ledger.rejected,
        }
        self._mark_coverage(ledger.candidates, retrieval.own_relevance)
        selected = rank_diverse(ledger.candidates)
        for option in selected:
            for key in PRIVATE_OPTION_KEYS:
                option.pop(key, None)
        result["options"] = selected
        self._set_outcome(result, ledger)
        return result

    # ------------------------------------------------------------------ 1. readiness gate
    def _early_result(self, status: str, explanation: str, now: datetime) -> dict:
        """A result with no options, used when the meal can't be evaluated yet."""
        return {
            "status": status,
            "options": [],
            "verification": [],
            "catalog_id": self.catalog.catalog_id,
            "catalog_version": self.catalog.version,
            "coverage": public_catalog_status(self.catalog, at=now),
            "explanation": explanation,
        }

    def _readiness_gate(self, snapshot: dict, now: datetime) -> MealContext | dict:
        """Return a MealContext, or an early result explaining what is missing.

        Messages never say whose answer is missing or what it is.
        """
        meal, people = snapshot["meal"], snapshot["participants"]
        if len(people) < 2:
            return self._early_result(
                "needs_input", "At least two people must complete a check-in.", now
            )
        latitude, longitude = meal.get("latitude"), meal.get("longitude")
        if latitude is None or longitude is None:
            return self._early_result(
                "needs_input",
                "Add a shared meeting point to compare nearby places.",
                now,
            )
        try:
            at = _parse_aware(meal["meal_at"])
            if at.tzinfo is None:
                raise ValueError("Missing timezone")
        except (KeyError, TypeError, ValueError):
            return self._early_result(
                "needs_input", "Confirm a meal date and time first.", now
            )
        if any(
            not p["profile"].get("requirements_reviewed")
            or not p["response"].get("requirements_confirmed")
            for p in people
        ):
            return self._early_result(
                "needs_verification",
                "Everyone needs to review their private requirements first.",
                now,
            )
        # Unknown allergy or halal answers can never be treated as "no requirement".
        if any(
            p["profile"].get("allergy_status", "unknown")
            in UNDISCLOSED_ALLERGY_STATUSES
            or p["profile"].get("halal_policy", "unknown") in UNREVIEWED_HALAL_POLICIES
            for p in people
        ):
            return self._early_result(
                "needs_verification",
                "Some requirements need private clarification before suitability can be checked.",
                now,
            )
        ctx = MealContext(snapshot, meal, people, now, at, latitude, longitude)
        if ctx.has_allergy_needs and not snapshot.get("preparation_confirmations"):
            return self._early_result(
                "needs_verification",
                "Some private requirements need further verification before options can be shared.",
                now,
            )
        return ctx

    # -------------------------------------------------------------------- 2. area search
    @staticmethod
    def _search_text(person: dict) -> str:
        """What one diner asked for, as a search query ('' when nothing specific)."""
        craving = person.get("response", {}).get("craving")
        cuisines = person.get("profile", {}).get("cuisines", []) + person.get(
            "response", {}
        ).get("cuisines", [])
        if craving and craving.casefold().strip() in NEUTRAL_WORDS:
            craving = None  # "Anything" is a valid answer, not a search query.
        return (craving or (" ".join(cuisines) if cuisines else ""))[:100]

    def _find_candidates(self, ctx: MealContext) -> RetrievalOutcome:
        """Outlets to assess. Without a usable index, every outlet (filtered by radius later)."""
        index = self.embedding_index
        if not (index and index.is_usable_for_catalog(self.catalog)):
            return RetrievalOutcome(outlets=list(self.catalog.outlets))

        # Search only inside the meal's area *before* the top-N cut, so a distant strong
        # match can never crowd out every nearby outlet.
        in_radius = {
            o.outlet_id
            for o in self.catalog.outlets
            if o.latitude is not None
            and o.longitude is not None
            and distance_km(ctx.latitude, ctx.longitude, o.latitude, o.longitude)
            <= ctx.radius_km
        }
        if not in_radius:
            return RetrievalOutcome(
                outlets=[]
            )  # Nothing verified here: no global fallback.

        person_texts = [
            (n, t) for n, p in enumerate(ctx.people) if (t := self._search_text(p))
        ]
        query_texts = [t for _, t in person_texts]
        taste_expressed = bool(query_texts)
        shared = self._shared_cuisines(ctx.people)
        if shared:
            query_texts.append(" ".join(shared)[:100])
        elif not query_texts:
            query_texts.append("dinner food meal")
        queries = [
            RetrievalQuery(query_id=f"query-{n}", text=text, top_k=20)
            for n, text in enumerate(dict.fromkeys(query_texts[:MAX_RETRIEVAL_QUERIES]))
        ]
        found = semantic_candidate_search(
            index,
            self.catalog,
            queries,
            maximum_outlets=MAX_RETRIEVED_OUTLETS,
            maximum_items_per_outlet=2,
            outlet_ids=in_radius,
        )
        outcome = RetrievalOutcome(outlets=list(self.catalog.outlets))
        if found.get("status") == "ok" and found.get("candidates"):
            outcome.status = "semantic"
            if (
                taste_expressed
            ):  # relevance orders outlets only when someone asked for something
                outcome.relevance = {
                    c["outlet_id"]: c["recall_score"] for c in found["candidates"]
                }
            if len(person_texts) > 1:
                outcome.own_relevance = self._own_relevance(person_texts, in_radius)
            by_id = {o.outlet_id: o for o in self.catalog.outlets}
            ordered = [
                by_id[oid]
                for oid in dict.fromkeys(c["outlet_id"] for c in found["candidates"])
                if oid in by_id
            ]
            if ordered:
                outcome.outlets = ordered
        outcome.receipt = {
            "status": found.get("status", "ok"),
            "policy_version": RETRIEVAL_POLICY_VERSION,
            "retrieved_candidate_count": len(outcome.outlets),
        }
        return outcome

    @staticmethod
    def _shared_cuisines(people: list[dict]) -> set[str]:
        """Cuisines the group has in common, for one extra 'group overlap' query.

        Not a strict intersection: a diner with no cuisines doesn't empty the set, and the
        running set restarts from the next diner whenever it becomes empty.
        """
        shared: set[str] = set()
        for person in people:
            cuisines = set(
                person.get("profile", {}).get("cuisines", [])
                + person.get("response", {}).get("cuisines", [])
            )
            shared = cuisines if not shared else shared & cuisines
        return shared

    def _own_relevance(self, person_texts, in_radius) -> dict[int, dict[str, float]]:
        """Each diner's own search relevance per outlet (only used to break coverage ties)."""
        own = {}
        for position, text in person_texts:
            found = semantic_candidate_search(
                self.embedding_index,
                self.catalog,
                [RetrievalQuery(query_id=f"person-{position}", text=text, top_k=20)],
                maximum_outlets=MAX_RETRIEVED_OUTLETS,
                maximum_items_per_outlet=2,
                outlet_ids=in_radius,
            )
            if found.get("status") == "ok":
                own[position] = {
                    c["outlet_id"]: c["recall_score"]
                    for c in found.get("candidates") or []
                }
        return own

    def _new_result(self, ctx: MealContext, retrieval: RetrievalOutcome) -> dict:
        """The result skeleton; options and status are filled in at the end."""
        result = {
            "status": "no_options",
            "options": [],
            "verification": [],
            "_private_routes": {},  # per-user ETAs; split out before the result is shared
            "catalog_id": self.catalog.catalog_id,
            "catalog_version": self.catalog.version,
            "synthetic": self.catalog.synthetic,
            "policy_version": self.scoring_policy,
            "feature_version": FEATURE_VERSION,
            "ontology_version": ONTOLOGY_VERSION,
            "retrieval_policy_version": RETRIEVAL_POLICY_VERSION,
            "embedding_model": (
                getattr(self.embedding_index, "model_name", EMBEDDING_MODEL_NAME)
                if retrieval.status == "semantic"
                else None
            ),
            "retrieval_status": retrieval.status,
            "coverage": public_catalog_status(self.catalog, at=ctx.now),
            "examined_outlets": 0,
            "explanation": "No verified common option was found in the imported catalog.",
        }
        if retrieval.receipt:
            result["retrieval_receipt"] = retrieval.receipt
        return result

    # --------------------------------------------------------------- 3. outlet assessment
    def _assess_outlet(
        self, ctx, outlet, retrieval, ledger: OutletLedger, result: dict
    ) -> None:
        """Run every check for one outlet and record it as a candidate or a rejection."""
        if outlet.latitude is None:
            ledger.evaluation_blocked = True
            return
        distance = distance_km(
            ctx.latitude, ctx.longitude, outlet.latitude, outlet.longitude
        )
        if distance > ctx.radius_km:
            return
        ledger.examined += 1
        if not self._evidence_in_scope(outlet, ctx, ledger):
            return

        opening = service_for(
            outlet, ctx.at, ctx.duration, catalog=self.catalog, checked_at=ctx.now
        )
        if opening.passes is False:
            ledger.reject(outlet, "hours")  # a known closure is excluded, not "unknown"
            return
        issues = self._outlet_issues(ctx, outlet, opening)
        if issues is None:  # known accessibility conflict
            ledger.reject(outlet, "hard_requirement")
            return

        dishes, has_conflicts = self._unique_dishes(outlet)
        if not dishes:
            ledger.evaluation_blocked = True
        if has_conflicts:
            issues.append("Conflicting menu records need review.")
        if ctx.has_allergy_needs and self._preparation_unconfirmed(ctx, outlet):
            issues.append(
                "Preparation safety and cross-contact for declared requirements need confirmation."
            )

        routes = self._route_evidence(ctx, outlet)
        if (travel_issue := self._travel_issue(ctx, outlet, routes)) is not None:
            issues.append(travel_issue)
        best = self._best_dishes(ctx, outlet, dishes, routes, issues)

        if issues:
            ledger.needs_confirmation(outlet)
            return
        if len(best) != len(ctx.people):
            ledger.reject(outlet, "group_menu_fit")  # a known conflict for someone
            return
        fits = [fit.fit for fit, _ in best]
        if min(fits) < MINIMUM_INDIVIDUAL_FIT:
            ledger.reject(outlet, "fit_floor")
            ledger.preference_conflict = True
            return
        option = self._build_option(
            ctx, outlet, distance, opening, best, routes, retrieval, result
        )
        ledger.candidates.append(option)
        ledger.eligible.append(
            {"outlet_id": outlet.outlet_id, "option_id": option["id"]}
        )

    def _evidence_in_scope(self, outlet, ctx, ledger) -> bool:
        """Synthetic data must be fresh; real data must have reviewed dishes."""
        if self.catalog.synthetic:
            if not self.catalog.evidence_usable(outlet.source_ids, at=ctx.now):
                ledger.evaluation_blocked = True
                ledger.reject(outlet, "source_freshness")
                return False
            return True
        if not any(
            i.review_status == "reviewed"
            for i in self.catalog.menu_items
            if i.outlet_id == outlet.outlet_id
        ):
            ledger.evaluation_blocked = True
            ledger.reject(outlet, "unreviewed_scope")
            return False
        return True

    def _outlet_issues(
        self, ctx: MealContext, outlet: Outlet, opening: ServiceCheck
    ) -> list[str] | None:
        """Outlet-level unknowns that send it to "Needs confirmation".

        Returns None for a *known* accessibility conflict, which excludes the outlet.
        """
        issues = []
        if opening.passes is None:
            strict_session = ctx.has_allergy_needs or ctx.needs_certified_halal
            # Published weekly hours that cover the visit are enough for a real catalog;
            # unconfirmed dated exceptions stay a visible trade-off.
            if self.catalog.synthetic or (strict_session and not opening.weekly_open):
                issues.append(
                    "Dated hours or kitchen service for the planned visit need confirmation."
                )
        if self.catalog.synthetic and not self.catalog.evidence_usable(
            outlet.source_ids, at=ctx.at
        ):
            issues.append("Outlet evidence expires before the planned meal.")
        if ctx.needs_certified_halal and (
            outlet.halal.status != "certified"
            or not self.catalog.evidence_usable(outlet.halal.source_ids, at=ctx.now)
            or not self.catalog.evidence_usable(outlet.halal.source_ids, at=ctx.at)
        ):
            issues.append("Current certification for this outlet needs verification.")

        needed = set()
        for p in ctx.people:
            requested = set(p["profile"].get("accessibility_requirements", [])) | set(
                p["response"].get("accessibility_requirements", [])
            )
            needed |= requested & FIRM_ACCESSIBILITY_FEATURES
        for requirement in needed:
            feature = getattr(outlet.accessibility, requirement, None)
            if feature is not None and feature.status == "inaccessible":
                return None  # known conflict (PRD §8.2): excluded, not "needs confirmation"
            if feature is None:
                issues.append("Required accessibility features are not available.")
                break
            if (
                feature.status != "accessible"
                or feature.review_status != "reviewed"
                or not feature.source_ids
            ):
                issues.append("Required accessibility evidence needs verification.")
                break
            if not self.catalog.evidence_usable(
                feature.source_ids, at=ctx.now
            ) or not self.catalog.evidence_usable(feature.source_ids, at=ctx.at):
                issues.append("Accessibility evidence has expired and needs review.")
                break
        return issues

    def _unique_dishes(self, outlet: Outlet) -> tuple[list[MenuItem], bool]:
        """De-duplicated dishes; copies that disagree on facts are dropped and flagged."""
        unique, conflicts = {}, set()
        for item in self.catalog.menu_items:
            if item.outlet_id != outlet.outlet_id:
                continue
            key = _canonical(item)
            if key in unique:
                ignored = {"item_id", "source_ids"}
                if item.model_dump(exclude=ignored) != unique[key].model_dump(
                    exclude=ignored
                ):
                    conflicts.add(key)
            else:
                unique[key] = item
        return [item for key, item in unique.items() if key not in conflicts], bool(
            conflicts
        )

    @staticmethod
    def _preparation_unconfirmed(ctx: MealContext, outlet: Outlet) -> bool:
        """True unless a current kitchen confirmation names every declared allergen/avoid."""
        valid = []
        for confirmation in ctx.snapshot.get("preparation_confirmations", []):
            if confirmation.get("outlet_id") != outlet.outlet_id:
                continue
            try:
                expires = _parse_aware(confirmation["expires_at"])
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires >= ctx.now and expires >= ctx.at:
                    valid.append(confirmation.get("exact_bounded_claim", "").casefold())
            except (KeyError, TypeError, ValueError):
                pass
        for person in ctx.people:
            needs = {s.casefold() for s in person["profile"].get("allergens", [])}
            needs |= {s.casefold() for s in person["response"].get("avoid", [])}
            if any(not any(need in claim for claim in valid) for need in needs):
                return True
        return False

    def _route_evidence(
        self, ctx: MealContext, outlet: Outlet
    ) -> dict[str, RouteEvidence]:
        """Fresh travel evidence for diners who shared a precise origin and consented."""
        routes = {}
        for person in ctx.people:
            origin = person.get("origin")
            if not (
                origin
                and origin.get("origin_mode") == "precise"
                and origin.get("route_consent")
                and origin.get("latitude") is not None
                and origin.get("longitude") is not None
            ):
                continue
            evidence = self.request_route(
                origin=RouteCoordinate(
                    latitude=float(origin["latitude"]),
                    longitude=float(origin["longitude"]),
                ),
                destination=RouteCoordinate(
                    latitude=float(outlet.latitude), longitude=float(outlet.longitude)
                ),
                mobility_mode=person.get("profile", {}).get("mobility_mode", "drive"),
                arrival_time=ctx.at,
                departure_time=ctx.at,
            )
            if evidence.status == RouteStatus.OK and evidence.is_usable(at=ctx.now):
                routes[person.get("user_id", str(id(person)))] = evidence
        return routes

    def _travel_issue(self, ctx: MealContext, outlet: Outlet, routes) -> str | None:
        """First travel problem for any diner with route evidence, or None."""
        for person in ctx.people:
            evidence = routes.get(person.get("user_id", str(id(person))))
            if evidence is None or evidence.duration_minutes is None:
                continue
            travel = evidence.duration_minutes
            arrival = ctx.at + timedelta(minutes=travel)
            if arrival >= ctx.at + timedelta(minutes=ctx.duration):
                return (
                    "Travel time prevents arriving before the planned meal concludes."
                )
            arrival_service = service_for(
                outlet,
                arrival,
                max(15, ctx.duration - travel),
                catalog=self.catalog,
                checked_at=ctx.now,
            )
            if arrival_service.passes is False:
                return "Travel time prevents arriving during open kitchen hours."
            leave_by = self._must_leave_by(person, ctx.at, outlet)
            if leave_by is not None and arrival >= leave_by:
                return "Travel time prevents arriving before required departure or kitchen close."
        return None

    @staticmethod
    def _must_leave_by(person: dict, at: datetime, outlet: Outlet) -> datetime | None:
        """A diner's 'must leave by' as a datetime (ISO timestamp or local HH:MM)."""
        raw = person.get("response", {}).get("must_leave_by")
        if not raw:
            return None
        try:
            leave = _parse_aware(raw)
            return (
                leave if leave.tzinfo is not None else leave.replace(tzinfo=at.tzinfo)
            )
        except (ValueError, TypeError):
            pass
        try:
            parts = raw.strip().split(":")
            if len(parts) != 2:
                return None
            local_at = at.astimezone(ZoneInfo(outlet.timezone))
            leave = datetime.combine(
                local_at.date(),
                local_time(int(parts[0]), int(parts[1])),
                tzinfo=local_at.tzinfo,
            )
            return leave + timedelta(days=1) if leave < local_at else leave
        except (ValueError, TypeError, IndexError):
            return None

    # ------------------------------------------------------------- dishes for each diner
    def _best_dishes(
        self, ctx, outlet, dishes, routes, issues: list[str]
    ) -> list[tuple]:
        """Best eligible (fit, dish) for each diner; diners with none are left out.

        Appends to ``issues`` when a diner's budget is missing, or when the only reason a
        diner has no dish is missing evidence (as opposed to a known conflict).
        """
        best = []
        for person in ctx.people:
            profile, response = person["profile"], person["response"]
            budget = response.get("budget", profile.get("max_budget"))
            # "Flexible" (M03): a comfortable target without a firm cap is a valid answer.
            if budget is None and response.get("soft_budget_target") is None:
                issues.append("A private meal budget needs confirmation.")
                continue
            route = self._route_info(
                routes.get(person.get("user_id", str(id(person)))), ctx.now
            ) or person.get("route_estimates", {}).get(outlet.outlet_id)
            matches, unknown = [], False
            for item in dishes:
                verdict = self._dish_verdict(item, profile, response, budget, ctx)
                if verdict == "unknown":
                    unknown = True
                if verdict != "eligible":
                    continue
                fit = self._scorer.score(
                    item, person, outlet_id=outlet.outlet_id, at=ctx.now, route=route
                )
                matches.append((fit, item))
            if not matches:
                # A known conflict is handled by the group-menu-fit check, not here.
                if unknown:
                    issues.append(
                        "Menu, portion or complete price evidence needs verification."
                    )
                continue
            matches.sort(
                key=lambda pair: (
                    -pair[0].fit,
                    pair[1].meal_role
                    not in CONFIRMED_MEAL_ROLES,  # confirmed meals first
                    -craving_name_overlap(pair[1], response),
                    _listed_price_minor(pair[1])
                    if _listed_price_minor(pair[1]) is not None
                    else 999999,
                    pair[1].name.casefold(),
                )
            )
            best.append(matches[0])
        return best

    @staticmethod
    def _route_info(evidence: RouteEvidence | None, now: datetime) -> dict | None:
        """Route evidence in the shape the scoring policies expect."""
        if evidence is None or evidence.duration_minutes is None:
            return None
        return {
            "eta_minutes": evidence.duration_minutes,
            "source_id": evidence.provider_id,
            "observed_at": (
                evidence.provider_timestamp.isoformat()
                if evidence.provider_timestamp
                else now.isoformat()
            ),
            "expires_at": (
                evidence.evidence_expiry.isoformat()
                if evidence.evidence_expiry
                else (now + timedelta(hours=1)).isoformat()
            ),
        }

    def _dish_verdict(
        self, item: MenuItem, profile: dict, response: dict, budget, ctx: MealContext
    ) -> str:
        """Can this dish be this diner's meal? 'eligible', 'excluded' (known) or 'unknown'."""
        if item.live_availability == "unavailable":
            return "excluded"
        # Review is necessary but never overrides known contradictions.
        if item.review_status != "reviewed" or dietary_claim_conflicts(item):
            return "unknown"
        if item.meal_role in NON_MEAL_ROLES:  # never someone's meal (product decision)
            return "excluded"
        if self.catalog.synthetic and not (
            self.catalog.evidence_usable(item.source_ids, at=ctx.now)
            and self.catalog.evidence_usable(item.source_ids, at=ctx.at)
        ):
            return "unknown"

        restrictions = {s.casefold() for s in profile.get("dietary_requirements", [])}
        if restrictions and not restrictions.issubset(
            {s.casefold() for s in item.dietary_claims}
        ):
            # Complete ingredients naming meat are a known conflict, not unknown.
            if (
                restrictions & PLANT_BASED_DIETS
                and item.ingredients_complete
                and any(mentions_animal(i) for i in item.ingredients)
            ):
                return "excluded"
            return "unknown"
        avoid = {s.casefold() for s in response.get("avoid", [])}
        if avoid:
            if not item.ingredients_complete:
                return "unknown"
            if avoid & {s.casefold() for s in item.ingredients}:
                return "excluded"
        return self._price_verdict(item, budget)

    def _price_verdict(self, item: MenuItem, budget) -> str:
        """Firm-cap check. Shared pots, weights and minimum orders are never divided."""
        price = item.price
        cap = None if budget is None else round(float(budget) * 100)
        if self.catalog.synthetic:
            if (
                price is None
                or price.channel != "dine_in"
                or not price.all_mandatory_charges_known
                or price.unit not in {"portion", "person", "set"}
                or price.minimum_quantity != 1
                or item.serves_min != 1
                or item.serves_max != 1
            ):
                return "unknown"  # the MVP only budgets independent single-person meals
            return (
                "excluded"
                if cap is not None and price.payable_amount_minor > cap
                else "eligible"
            )
        if item.serves_min is not None and item.serves_min > 1:
            return "excluded"
        # Production policy: the listed dine-in price stands in for the per-person price;
        # a dish with no price, or a per-weight price, can never clear a cap.
        payable = _listed_price_minor(item) if price is not None else None
        if price is not None and (
            price.unit in PER_WEIGHT_UNITS
            or PER_WEIGHT.search(f"{item.name} {item.variant}")
        ):
            payable = None
        if cap is None:
            return "eligible"
        if payable is None:
            return "unknown"
        return "excluded" if payable > cap else "eligible"

    # ------------------------------------------------------------------- option building
    def _build_option(
        self, ctx, outlet, distance, opening, best, routes, retrieval, result
    ) -> dict:
        """The shared option card plus private working fields for ranking."""
        fits = [fit.fit for fit, _ in best]
        novelty = sum(
            novelty_feature(p, outlet.outlet_id, at=ctx.now).value for p in ctx.people
        ) / len(ctx.people)
        # No adequately licensed quality signal is connected: use the PRD neutral prior.
        score = base_score(fits, quality=0.5, novelty=novelty)
        low_coverage = any(fit.coverage < LOW_COVERAGE_THRESHOLD for fit, _ in best)
        chosen = {item.item_id: item for _, item in best}
        known_prices = [
            item.price.payable_amount_minor
            for item in chosen.values()
            if item.price and item.price.payable_amount_minor is not None
        ]
        price_range = (
            {"min_minor": min(known_prices), "max_minor": max(known_prices)}
            if known_prices
            else {"min_minor": None, "max_minor": None, "status": "unknown"}
        )
        evidence_ids = (
            set(outlet.source_ids)
            | set(opening.source_ids)
            | {sid for item in chosen.values() for sid in item.source_ids}
        )
        option_id = hashlib.sha256(
            f"{self.scoring_policy}:{FEATURE_VERSION}:{ONTOLOGY_VERSION}:{ctx.meal.get('id', '')}:"
            f"{ctx.snapshot.get('revision', ctx.meal.get('revision'))}:{self.fingerprint}:{outlet.outlet_id}".encode()
        ).hexdigest()[:24]
        for uid, evidence in routes.items():
            result["_private_routes"].setdefault(uid, {})[option_id] = {
                "outlet_id": outlet.outlet_id,
                "eta_minutes": evidence.duration_minutes,
                "distance_km": round(evidence.distance_meters / 1000.0, 1)
                if evidence.distance_meters is not None
                else None,
                "evidence_fresh": evidence.is_usable(at=ctx.now),
                "provider_id": evidence.provider_id,
            }
        return {
            "id": option_id,
            "option_id": option_id,
            "outlet_id": outlet.outlet_id,
            "brand_id": outlet.brand_id,
            "name": outlet.name,
            "area": outlet.city,
            "description": "Suitable menu options were found for everyone’s submitted requirements.",
            "cuisines": list(outlet.cuisine_tags),
            "fit_confidence": "limited" if low_coverage else "supported",
            "distance_km": round(distance, 1),
            "price_range": price_range,
            "travel_aggregate": {
                "evidence_status": "firm" if routes else "unknown",
                "route_checked_count": len(routes),
            },
            "reasons": [
                "A suitable menu choice for each included person",
                "Listed payable meal prices fit all submitted caps",
            ],
            "tradeoffs": self._tradeoffs(chosen.values(), opening, low_coverage),
            "menu_items": [
                {
                    "id": item.item_id,
                    "name": item.name,
                    "price_minor": _listed_price_minor(item),
                }
                for item in chosen.values()
            ],
            "evidence": [
                {"url": source.url, "observed_at": source.observed_at.isoformat()}
                for source in self.catalog.sources
                if source.source_id in evidence_ids
            ],
            # Private working fields (removed before sharing):
            "_score": score,
            "_relevance": retrieval.relevance.get(
                outlet.outlet_id, 0.0
            ),  # beats distance in ties
            "_confirmed_meal": all(
                item.meal_role in CONFIRMED_MEAL_ROLES for _, item in best
            ),
            "_fits": fits,
        }

    @staticmethod
    def _tradeoffs(items, opening: ServiceCheck, low_coverage: bool) -> list[str]:
        """Honest caveats shown on the option card."""
        items = list(items)
        tradeoffs = [
            "Distance is straight-line, not a travel-time estimate.",
            "Menu availability still needs confirmation when ordering.",
        ]
        if any(
            item.price is None or not item.price.all_mandatory_charges_known
            for item in items
        ):
            tradeoffs.append(
                "Complete payable prices are not published; budget needs confirmation."
            )
        if any(
            item.meal_role == "unknown" or item.serves_min is None for item in items
        ):
            tradeoffs.append("Serving size or meal role is unknown.")
        if opening.passes is None:
            tradeoffs.append(
                "Dated opening exceptions or kitchen last order need confirmation before visit."
            )
        if low_coverage:
            tradeoffs.append(
                "Some taste or practical details are missing; fit uses neutral assumptions."
            )
        return tradeoffs

    # ------------------------------------------------------------- 4–5. coverage, outcome
    @staticmethod
    def _mark_coverage(
        candidates: list[dict], own_relevance: dict[int, dict[str, float]]
    ) -> None:
        """Record which diners each candidate best serves (``_serves``).

        A diner's best match is the eligible outlet with *their own* highest fit; their own
        search relevance only breaks ties between equal fits.
        """
        serves: dict[str, set[int]] = {}
        for position, relevance_by_outlet in own_relevance.items():
            keyed = [
                (
                    round(c["_fits"][position], 6),
                    relevance_by_outlet.get(c["outlet_id"], 0.0),
                )
                for c in candidates
            ]
            if keyed:
                top = max(keyed)
                for candidate, key in zip(candidates, keyed, strict=True):
                    if key == top:
                        serves.setdefault(candidate["outlet_id"], set()).add(position)
        for candidate in candidates:
            candidate["_serves"] = sorted(serves.get(candidate["outlet_id"], ()))

    @staticmethod
    def _set_outcome(result: dict, ledger: OutletLedger) -> None:
        """Final status and explanation, in priority order."""
        if result["options"]:
            result.update(
                status="shortlisted",
                explanation=f"Found {len(result['options'])} options in the imported catalog. Everyone can review them privately before a group decision.",
            )
        elif ledger.preference_conflict:
            result.update(
                status="needs_input",
                explanation="The checked options have a low preference fit for part of the group. Review today's cuisine or meal-style choices, then try again.",
            )
        elif result["verification"] or ledger.evaluation_blocked:
            result.update(
                status="needs_verification",
                explanation="Catalog coverage or available evidence is incomplete, so suitability cannot yet be established for this meal.",
            )
