"""Deterministic outlet-level recommendation. Menu similarity never grants eligibility."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from datetime import time as local_time
from zoneinfo import ZoneInfo

from dining.catalog import Catalog, MenuItem, Outlet
from dining.catalog_audit import (
    dietary_claim_conflicts,
    mentions_animal,
    public_catalog_status,
)
from dining.content_similarity import POLICY as CONTENT_POLICY
from dining.content_similarity import FeatureSpace, score_item_content
from dining.ranking import (
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
    score_item,
)
from dining.retrieval import (
    EMBEDDING_MODEL_NAME,
    RETRIEVAL_POLICY_VERSION,
    CatalogEmbeddingIndex,
    RetrievalQuery,
    semantic_candidate_search,
)
from dining.routing import (
    RouteCoordinate,
    RouteEvidence,
    RouteRequest,
    RouteStatus,
    RoutingProvider,
    get_routing_provider,
)


def distance_km(a: float, b: float, c: float, d: float) -> float:
    lat1, lat2, dl, dn = map(math.radians, (a, c, c - a, d - b))
    h = math.sin(dl / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dn / 2) ** 2
    return 6371 * 2 * math.asin(min(1, math.sqrt(h)))


def _canonical(item: MenuItem) -> tuple:
    """Duplicate scrape copies and IDs do not provide additional ranking evidence."""
    return (
        item.outlet_id,
        item.name.casefold().strip(),
        item.variant.casefold().strip(),
        item.menu_version,
        item.price.channel if item.price else None,
    )


@dataclass(frozen=True)
class ServiceCheck:
    passes: bool | None
    source_ids: tuple[str, ...] = ()
    # Published weekly hours cover the visit, but dated exceptions are not established.
    weekly_open: bool = False


def _weekly_service(outlet: Outlet, local: datetime, end: datetime, exceptions) -> bool | None:
    """Published weekly hours only: True covers the visit, False is a known-closed slot.

    Used to exclude outlets that are closed by their own published schedule even when
    dated exception coverage is missing. A dated exception owns its date, so any visit
    date with an exception defers to the full check.
    """
    if outlet.hours_status != "published" or not outlet.opening_hours:
        return None
    day = local.date()
    last = (end - timedelta(microseconds=1)).date()
    if any(d in exceptions for d in (day - timedelta(days=1), day, last)):
        return None
    for offset in (-1, 0):
        on = day + timedelta(days=offset)
        for interval in outlet.opening_hours:
            if interval.weekday != on.weekday():
                continue
            start = datetime.combine(on, local_time.fromisoformat(interval.opens), tzinfo=local.tzinfo)
            finish = datetime.combine(on, local_time.fromisoformat(interval.closes), tzinfo=local.tzinfo)
            if interval.closes_next_day:
                finish += timedelta(days=1)
            if start <= local and end <= finish:
                return True
    return False


def _service_for(
    outlet: Outlet,
    at: datetime,
    duration: int,
    *,
    catalog: Catalog | None = None,
    checked_at: datetime | None = None,
) -> ServiceCheck:
    """Check arrival, kitchen deadline and uninterrupted meal time in local dates.

    Dated exceptions replace the entire local date, including prior-night
    spillover. A positive result needs dated exception coverage and explicit
    kitchen evidence. The old undated boolean remains importable, not evidence.
    Without a catalog this checks structure/time only (useful for fixture tests).
    """
    if at.tzinfo is None or duration <= 0:
        return ServiceCheck(None)
    local = at.astimezone(ZoneInfo(outlet.timezone))
    end = local + timedelta(minutes=duration)
    now = checked_at or datetime.now(timezone.utc)
    exceptions = {entry.on_date: entry for entry in outlet.opening_exceptions}
    weekly = _weekly_service(outlet, local, end, exceptions)
    if weekly is False:
        return ServiceCheck(False)

    def usable(ids):
        return bool(ids) and (
            catalog is None
            or (
                catalog.evidence_usable(ids, at=now)
                and catalog.evidence_usable(ids, at=end)
            )
        )

    def day_schedule(day):
        exception = exceptions.get(day)
        if exception is not None:
            if exception.status == "unknown" or not usable(exception.source_ids):
                return None
            return exception.intervals, exception.source_ids
        coverage = outlet.opening_exceptions_coverage
        if (
            outlet.hours_status != "published"
            or not usable(outlet.source_ids)
            or coverage is None
            or not coverage.starts_on <= day <= coverage.ends_on
            or not usable(coverage.source_ids)
        ):
            return None
        return (
            tuple(i for i in outlet.opening_hours if i.weekday == day.weekday()),
            tuple(set(outlet.source_ids) | set(coverage.source_ids)),
        )

    # A meal ending exactly at midnight does not occupy the following date.
    visit_dates = []
    day = local.date()
    last_date = (end - timedelta(microseconds=1)).date()
    while day <= last_date:
        visit_dates.append(day)
        day += timedelta(days=1)
    schedules = {day: day_schedule(day) for day in visit_dates}
    for day, schedule in schedules.items():
        exception = exceptions.get(day)
        if schedule is not None and exception and exception.status == "closed":
            return ServiceCheck(False)
    uncertain = any(schedule is None for schedule in schedules.values())
    date_sources = {
        sid for schedule in schedules.values() if schedule for sid in schedule[1]
    }
    for offset in (-1, 0):
        day = (local + timedelta(days=offset)).date()
        schedule = day_schedule(day)
        if schedule is None:
            uncertain = True
            continue
        intervals, source_ids = schedule
        for interval in intervals:
            start = datetime.combine(
                day, local_time.fromisoformat(interval.opens), tzinfo=local.tzinfo
            )
            finish = datetime.combine(
                day, local_time.fromisoformat(interval.closes), tzinfo=local.tzinfo
            )
            if interval.closes_next_day:
                finish += timedelta(days=1)
            next_day = day + timedelta(days=1)
            if next_day in exceptions:
                # An exception owns its whole date; never inherit yesterday's hours.
                finish = min(
                    finish,
                    datetime.combine(next_day, local_time.min, tzinfo=local.tzinfo),
                )
            if start <= local and end <= finish:
                if interval.last_order is None or not usable(
                    interval.last_order_source_ids
                ):
                    uncertain = True
                    continue
                last_order = datetime.combine(
                    day,
                    local_time.fromisoformat(interval.last_order),
                    tzinfo=local.tzinfo,
                ) + timedelta(days=int(interval.last_order_next_day))
                if local > last_order:
                    continue
                if all(schedule is not None for schedule in schedules.values()):
                    return ServiceCheck(
                        True,
                        tuple(
                            sorted(
                                date_sources
                                | set(source_ids)
                                | set(interval.last_order_source_ids)
                            )
                        ),
                    )
    return ServiceCheck(None if uncertain else False, weekly_open=bool(weekly))


def _open_for(outlet: Outlet, at: datetime, duration: int) -> bool | None:
    return _service_for(outlet, at, duration).passes


class Recommender:
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
        if scoring_policy not in {POLICY_VERSION, CONTENT_POLICY}:
            raise ValueError(f"Unknown scoring policy: {scoring_policy}")
        self.scoring_policy = scoring_policy
        self._feature_space = None
        self._items_by_id = None
        self.fingerprint = hashlib.sha256(
            catalog.model_dump_json().encode()
        ).hexdigest()

    @property
    def feature_space(self) -> FeatureSpace:
        if self._feature_space is None:
            self._feature_space = FeatureSpace(self.catalog)
        return self._feature_space

    @property
    def items_by_id(self) -> dict[str, MenuItem]:
        if self._items_by_id is None:
            self._items_by_id = {i.item_id: i for i in self.catalog.menu_items}
        return self._items_by_id

    def request_route(
        self,
        origin: RouteCoordinate,
        destination: RouteCoordinate,
        mobility_mode: str = "drive",
        departure_time: datetime | None = None,
        arrival_time: datetime | None = None,
    ) -> RouteEvidence:
        request = RouteRequest(
            origin=origin,
            destination=destination,
            mobility_mode=mobility_mode,
            departure_time=departure_time,
            arrival_time=arrival_time,
        )
        return self.routing_provider.calculate_route(request)

    def __call__(self, snapshot: dict) -> dict:
        now = datetime.now(timezone.utc)
        meal = snapshot["meal"]
        people = snapshot["participants"]

        if len(people) < 2:
            return {
                "status": "needs_input",
                "options": [],
                "verification": [],
                "catalog_id": self.catalog.catalog_id,
                "catalog_version": self.catalog.version,
                "coverage": public_catalog_status(self.catalog, at=now),
                "explanation": "At least two people must complete a check-in.",
            }
        latitude, longitude = meal.get("latitude"), meal.get("longitude")
        if latitude is None or longitude is None:
            return {
                "status": "needs_input",
                "options": [],
                "verification": [],
                "catalog_id": self.catalog.catalog_id,
                "catalog_version": self.catalog.version,
                "coverage": public_catalog_status(self.catalog, at=now),
                "explanation": "Add a shared meeting point to compare nearby places.",
            }
        try:
            at = datetime.fromisoformat(meal["meal_at"].replace("Z", "+00:00"))
            if at.tzinfo is None:
                raise ValueError("Missing timezone")
        except (KeyError, TypeError, ValueError):
            return {
                "status": "needs_input",
                "options": [],
                "verification": [],
                "catalog_id": self.catalog.catalog_id,
                "catalog_version": self.catalog.version,
                "coverage": public_catalog_status(self.catalog, at=now),
                "explanation": "Confirm a meal date and time first.",
            }
        if any(
            not p["profile"].get("requirements_reviewed")
            or not p["response"].get("requirements_confirmed")
            for p in people
        ):
            return {
                "status": "needs_verification",
                "options": [],
                "verification": [],
                "catalog_id": self.catalog.catalog_id,
                "catalog_version": self.catalog.version,
                "coverage": public_catalog_status(self.catalog, at=now),
                "explanation": "Everyone needs to review their private requirements first.",
            }
        if any(
            p["profile"].get("allergy_status", "unknown") in {"unknown", "withheld"}
            or p["profile"].get("halal_policy", "unknown") in {"unknown", "review"}
            for p in people
        ):
            return {
                "status": "needs_verification",
                "options": [],
                "verification": [],
                "catalog_id": self.catalog.catalog_id,
                "catalog_version": self.catalog.version,
                "coverage": public_catalog_status(self.catalog, at=now),
                "explanation": "Some requirements need private clarification before suitability can be checked.",
            }
        has_allergy_needs = any(
            p["profile"].get("allergy_status") == "declared"
            or p["profile"].get("allergens")
            or p["response"].get("avoid")
            for p in people
        )
        if has_allergy_needs and not snapshot.get("preparation_confirmations"):
            return {
                "status": "needs_verification",
                "options": [],
                "verification": [],
                "catalog_id": self.catalog.catalog_id,
                "catalog_version": self.catalog.version,
                "coverage": public_catalog_status(self.catalog, at=now),
                "explanation": "Some private requirements need further verification before options can be shared.",
            }

        retrieval_status = "structured_fallback"
        candidate_outlets = list(self.catalog.outlets)
        retrieval_receipt = None
        relevance: dict[str, float] = {}
        # Retrieval is bounded to the meal's search area before any top-N cut, so a
        # distant strong match can never crowd out every nearby outlet.
        in_radius = {
            outlet.outlet_id
            for outlet in self.catalog.outlets
            if outlet.latitude is not None
            and outlet.longitude is not None
            and distance_km(latitude, longitude, outlet.latitude, outlet.longitude)
            <= meal.get("radius_km", 5)
        }

        serves: dict[str, set[int]] = {}
        # Each diner's own search relevance per outlet; only breaks ties in coverage.
        own_relevance: dict[int, dict[str, float]] = {}
        index_usable = bool(
            self.embedding_index and self.embedding_index.is_usable_for_catalog(self.catalog)
        )
        if index_usable and not in_radius:
            candidate_outlets = []  # Nothing verified in this area: no global fallback.
        elif index_usable:
            query_texts = []
            person_texts: list[tuple[int, str]] = []
            for position, p in enumerate(people):
                craving = p.get("response", {}).get("craving")
                cuisines = p.get("profile", {}).get("cuisines", []) + p.get("response", {}).get("cuisines", [])
                if craving and craving.casefold().strip() in NEUTRAL_WORDS:
                    craving = None  # "Anything" is a valid answer, not a search query.
                text = craving or (" ".join(cuisines) if cuisines else "")
                if text:
                    query_texts.append(text[:100])
                    person_texts.append((position, text[:100]))
            # Relevance only orders outlets when someone actually expressed a taste.
            taste_expressed = bool(query_texts)

            # Group overlap query
            shared_cuisines = set()
            for p in people:
                c_set = set(p.get("profile", {}).get("cuisines", []) + p.get("response", {}).get("cuisines", []))
                if not shared_cuisines:
                    shared_cuisines = c_set
                else:
                    shared_cuisines &= c_set
            if shared_cuisines:
                query_texts.append(" ".join(shared_cuisines)[:100])
            elif not query_texts:
                query_texts.append("dinner food meal")

            retrieval_queries = [
                RetrievalQuery(query_id=f"query-{idx}", text=qt, top_k=20)
                for idx, qt in enumerate(dict.fromkeys(query_texts[:8]))
            ]

            retrieval_result = semantic_candidate_search(
                self.embedding_index,
                self.catalog,
                retrieval_queries,
                maximum_outlets=30,
                maximum_items_per_outlet=2,
                outlet_ids=in_radius,
            )

            if retrieval_result.get("status") == "ok" and retrieval_result.get("candidates"):
                retrieval_status = "semantic"
                if taste_expressed:
                    relevance = {
                        c["outlet_id"]: c["recall_score"]
                        for c in retrieval_result["candidates"]
                    }
                # Which outlet best serves each diner's own wish (shortlist coverage).
                for position, text in person_texts if len(person_texts) > 1 else []:
                    own = semantic_candidate_search(
                        self.embedding_index,
                        self.catalog,
                        [RetrievalQuery(query_id=f"person-{position}", text=text, top_k=20)],
                        maximum_outlets=30,
                        maximum_items_per_outlet=2,
                        outlet_ids=in_radius,
                    )
                    if own.get("status") == "ok":
                        own_relevance[position] = {
                            c["outlet_id"]: c["recall_score"]
                            for c in own.get("candidates") or []
                        }
                seen_oids = set()
                retrieved_outlet_ids = [
                    c["outlet_id"]
                    for c in retrieval_result["candidates"]
                    if not (c["outlet_id"] in seen_oids or seen_oids.add(c["outlet_id"]))
                ]
                retrieved_pool = [
                    o for oid in retrieved_outlet_ids
                    for o in [next((x for x in self.catalog.outlets if x.outlet_id == oid), None)]
                    if o is not None
                ]
                if retrieved_pool:
                    candidate_outlets = retrieved_pool

            retrieval_receipt = {
                "status": retrieval_result.get("status", "ok"),
                "policy_version": RETRIEVAL_POLICY_VERSION,
                "retrieved_candidate_count": len(candidate_outlets),
            }

        result = {
            "status": "no_options",
            "options": [],
            "verification": [],
            "_private_routes": {},
            "catalog_id": self.catalog.catalog_id,
            "catalog_version": self.catalog.version,
            "synthetic": self.catalog.synthetic,
            "policy_version": self.scoring_policy,
            "feature_version": FEATURE_VERSION,
            "ontology_version": ONTOLOGY_VERSION,
            "retrieval_policy_version": RETRIEVAL_POLICY_VERSION,
            "embedding_model": (
                getattr(self.embedding_index, "model_name", EMBEDDING_MODEL_NAME)
                if retrieval_status == "semantic"
                else None
            ),
            "retrieval_status": retrieval_status,
            "coverage": public_catalog_status(self.catalog, at=now),
            "examined_outlets": 0,
            "explanation": "No verified common option was found in the imported catalog.",
        }
        if retrieval_receipt:
            result["retrieval_receipt"] = retrieval_receipt

        candidates = []
        eligible_candidates = []
        rejected_candidates = []
        preference_conflict = False
        evaluation_blocked = not self.catalog.outlets or not self.catalog.menu_items
        for outlet in candidate_outlets:
            if outlet.latitude is None:
                evaluation_blocked = True
                continue
            distance = distance_km(
                latitude, longitude, outlet.latitude, outlet.longitude
            )
            if distance > meal.get("radius_km", 5):
                continue
            result["examined_outlets"] += 1

            has_reviewed_items = any(
                item.review_status == "reviewed"
                for item in self.catalog.menu_items
                if item.outlet_id == outlet.outlet_id
            )

            if self.catalog.synthetic:
                if not self.catalog.evidence_usable(outlet.source_ids, at=now):
                    evaluation_blocked = True
                    rejected_candidates.append(
                        {"outlet_id": outlet.outlet_id, "category": "source_freshness"}
                    )
                    continue
            else:
                if not has_reviewed_items:
                    evaluation_blocked = True
                    rejected_candidates.append(
                        {"outlet_id": outlet.outlet_id, "category": "unreviewed_scope"}
                    )
                    continue

            issues = []
            opening = _service_for(
                outlet,
                at,
                meal.get("duration_minutes", 60),
                catalog=self.catalog,
                checked_at=now,
            )
            if opening.passes is False:
                rejected_candidates.append(
                    {"outlet_id": outlet.outlet_id, "category": "hours"}
                )
                continue
            if opening.passes is None:
                strict_session = has_allergy_needs or any(
                    p["profile"].get("halal_policy") == "certified" for p in people
                )
                # Published weekly hours that cover the visit are enough for a real
                # catalog; unconfirmed dated exceptions stay a visible trade-off.
                if self.catalog.synthetic or (strict_session and not opening.weekly_open):
                    issues.append(
                        "Dated hours or kitchen service for the planned visit need confirmation."
                    )
            if self.catalog.synthetic and not self.catalog.evidence_usable(outlet.source_ids, at=at):
                issues.append("Outlet evidence expires before the planned meal.")
            if any(
                p["profile"].get("halal_policy") == "certified" for p in people
            ) and (
                outlet.halal.status != "certified"
                or not self.catalog.evidence_usable(outlet.halal.source_ids, at=now)
                or not self.catalog.evidence_usable(outlet.halal.source_ids, at=at)
            ):
                issues.append(
                    "Current certification for this outlet needs verification."
                )

            firm_accessibility_features = {
                "step_free_entrance",
                "wheelchair_accessible_seating",
                "accessible_restroom",
                "low_noise_seating",
            }
            needed_accessibility = set()
            for p in people:
                reqs = set(p["profile"].get("accessibility_requirements", [])) | set(
                    p["response"].get("accessibility_requirements", [])
                )
                needed_accessibility |= reqs & firm_accessibility_features

            access_conflict = False
            for req in needed_accessibility:
                feature = getattr(outlet.accessibility, req, None)
                if feature is not None and feature.status == "inaccessible":
                    # A known conflict is excluded, not sent to Needs confirmation (PRD §8.2).
                    access_conflict = True
                    break
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
                    feature.source_ids, at=now
                ) or not self.catalog.evidence_usable(feature.source_ids, at=at):
                    issues.append(
                        "Accessibility evidence has expired and needs review."
                    )
                    break
            if access_conflict:
                rejected_candidates.append(
                    {"outlet_id": outlet.outlet_id, "category": "hard_requirement"}
                )
                continue
            # Entire local outlet pool is considered; no global dish top-K gate.
            unique, conflicts = {}, set()
            for item in self.catalog.menu_items:
                if item.outlet_id == outlet.outlet_id:
                    key = _canonical(item)
                    if key in unique:
                        fields = {"item_id", "source_ids"}
                        if item.model_dump(exclude=fields) != unique[key].model_dump(
                            exclude=fields
                        ):
                            conflicts.add(key)
                    else:
                        unique[key] = item
            dishes = [item for key, item in unique.items() if key not in conflicts]
            if not dishes:
                evaluation_blocked = True
            if conflicts:
                issues.append("Conflicting menu records need review.")

            if has_allergy_needs:
                outlet_confs = [
                    c
                    for c in snapshot.get("preparation_confirmations", [])
                    if c.get("outlet_id") == outlet.outlet_id
                ]
                valid_confs = []
                for c in outlet_confs:
                    try:
                        exp = datetime.fromisoformat(
                            c["expires_at"].replace("Z", "+00:00")
                        )
                        if exp.tzinfo is None:
                            exp = exp.replace(tzinfo=timezone.utc)
                        if exp >= now and exp >= at:
                            valid_confs.append(c)
                    except (KeyError, TypeError, ValueError):
                        pass

                allergy_unconfirmed = False
                for p in people:
                    allergens = {
                        s.casefold() for s in p["profile"].get("allergens", [])
                    }
                    for a in allergens:
                        if not any(
                            a in c.get("exact_bounded_claim", "").casefold()
                            for c in valid_confs
                        ):
                            allergy_unconfirmed = True
                            break
                    avoids = {s.casefold() for s in p["response"].get("avoid", [])}
                    for av in avoids:
                        if not any(
                            av in c.get("exact_bounded_claim", "").casefold()
                            for c in valid_confs
                        ):
                            allergy_unconfirmed = True
                            break
                    if allergy_unconfirmed:
                        break

                if allergy_unconfirmed:
                    issues.append(
                        "Preparation safety and cross-contact for declared requirements need confirmation."
                    )

            person_routes = {}
            for person in people:
                uid = person.get("user_id", str(id(person)))
                origin = person.get("origin")
                if (
                    origin
                    and origin.get("origin_mode") == "precise"
                    and origin.get("route_consent")
                    and origin.get("latitude") is not None
                    and origin.get("longitude") is not None
                    and outlet.latitude is not None
                    and outlet.longitude is not None
                ):
                    origin_coord = RouteCoordinate(
                        latitude=float(origin["latitude"]),
                        longitude=float(origin["longitude"]),
                    )
                    dest_coord = RouteCoordinate(
                        latitude=float(outlet.latitude),
                        longitude=float(outlet.longitude),
                    )
                    mobility_mode = person.get("profile", {}).get(
                        "mobility_mode", "drive"
                    )
                    evidence = self.request_route(
                        origin=origin_coord,
                        destination=dest_coord,
                        mobility_mode=mobility_mode,
                        arrival_time=at,
                        departure_time=at,
                    )
                    if evidence.status == RouteStatus.OK and evidence.is_usable(at=now):
                        person_routes[uid] = evidence

            for person in people:
                uid = person.get("user_id", str(id(person)))
                evidence = person_routes.get(uid)
                if evidence is not None and evidence.duration_minutes is not None:
                    travel_min = evidence.duration_minutes
                    arrival_dt = at + timedelta(minutes=travel_min)

                    meal_end = at + timedelta(minutes=meal.get("duration_minutes", 60))
                    if arrival_dt >= meal_end:
                        issues.append(
                            "Travel time prevents arriving before the planned meal concludes."
                        )
                        break

                    arrival_service = _service_for(
                        outlet,
                        arrival_dt,
                        max(15, meal.get("duration_minutes", 60) - travel_min),
                        catalog=self.catalog,
                        checked_at=now,
                    )
                    if arrival_service.passes is False:
                        issues.append(
                            "Travel time prevents arriving during open kitchen hours."
                        )
                        break

                    must_leave_by_str = person.get("response", {}).get("must_leave_by")
                    if must_leave_by_str:
                        leave_dt = None
                        try:
                            leave_dt = datetime.fromisoformat(
                                must_leave_by_str.replace("Z", "+00:00")
                            )
                            if leave_dt.tzinfo is None:
                                leave_dt = leave_dt.replace(tzinfo=at.tzinfo)
                        except (ValueError, TypeError):
                            try:
                                parts = must_leave_by_str.strip().split(":")
                                if len(parts) == 2:
                                    hh, mm = int(parts[0]), int(parts[1])
                                    local_at = at.astimezone(ZoneInfo(outlet.timezone))
                                    leave_dt = datetime.combine(
                                        local_at.date(),
                                        local_time(hh, mm),
                                        tzinfo=local_at.tzinfo,
                                    )
                                    if leave_dt < local_at:
                                        leave_dt += timedelta(days=1)
                            except (ValueError, TypeError, IndexError):
                                leave_dt = None
                        if leave_dt is not None and arrival_dt >= leave_dt:
                            issues.append(
                                "Travel time prevents arriving before required departure or kitchen close."
                            )
                            break

            best = []
            for person in people:
                profile, response = person["profile"], person["response"]
                uid = person.get("user_id", str(id(person)))
                evidence = person_routes.get(uid)
                route_info = None
                if evidence is not None and evidence.duration_minutes is not None:
                    route_info = {
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
                matches = []
                unknown = False
                budget = response.get("budget", profile.get("max_budget"))
                # "Flexible" (M03): a comfortable target without a firm cap is a valid answer.
                if budget is None and response.get("soft_budget_target") is None:
                    issues.append("A private meal budget needs confirmation.")
                    continue
                for item in dishes:
                    if item.live_availability == "unavailable":
                        continue
                    # Review is necessary but never overrides known contradictions.
                    if (
                        item.review_status != "reviewed"
                        or dietary_claim_conflicts(item)
                    ):
                        unknown = True
                        continue
                    if item.meal_role in NON_MEAL_ROLES:
                        continue
                    if self.catalog.synthetic:
                        if not self.catalog.evidence_usable(
                            item.source_ids, at=now
                        ) or not self.catalog.evidence_usable(item.source_ids, at=at):
                            unknown = True
                            continue
                    restrictions = {
                        s.casefold() for s in profile.get("dietary_requirements", [])
                    }
                    claims = {s.casefold() for s in item.dietary_claims}
                    if restrictions and not restrictions.issubset(claims):
                        # Complete ingredients naming meat are a known conflict, not unknown.
                        if (
                            restrictions & {"vegetarian", "vegan"}
                            and item.ingredients_complete
                            and any(mentions_animal(i) for i in item.ingredients)
                        ):
                            continue
                        unknown = True
                        continue
                    avoid = {s.casefold() for s in response.get("avoid", [])}
                    if avoid:
                        if not item.ingredients_complete:
                            unknown = True
                            continue
                        if avoid & {s.casefold() for s in item.ingredients}:
                            continue
                    price = item.price
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
                            # The MVP only budgets independent single-person meals.
                            # Never divide a shared pot, weight price or minimum order.
                            unknown = True
                            continue
                        if budget is not None and price.payable_amount_minor > round(
                            float(budget) * 100
                        ):
                            continue
                    else:
                        if item.serves_min is not None and item.serves_min > 1:
                            continue
                        # Production policy: the listed dine-in price stands in for the
                        # per-person price; a dish with no price can never clear a cap.
                        payable = (
                            price.payable_amount_minor
                            if price is not None and price.payable_amount_minor is not None
                            else price.amount_minor if price is not None else None
                        )
                        # A per-100 g / per-kg price is not the price of a meal.
                        if price is not None and (
                            price.unit in {"weight_100g", "weight_kg"}
                            or PER_WEIGHT.search(f"{item.name} {item.variant}")
                        ):
                            payable = None
                        if budget is not None:
                            if payable is None:
                                unknown = True
                                continue
                            if payable > round(float(budget) * 100):
                                continue

                    route = route_info or person.get("route_estimates", {}).get(
                        outlet.outlet_id
                    )
                    if self.scoring_policy == CONTENT_POLICY:
                        fit = score_item_content(
                            item,
                            profile,
                            response,
                            space=self.feature_space,
                            catalog_items=self.items_by_id,
                            outlet_id=outlet.outlet_id,
                            at=now,
                            observations=person.get("observations", []),
                            attribute_preferences=person.get("attribute_preferences", []),
                            route=route,
                        )
                    else:
                        fit = score_item(
                            item,
                            profile,
                            response,
                            outlet_id=outlet.outlet_id,
                            at=now,
                            observations=person.get("observations", []),
                            venue_preferences=person.get("venue_preferences", []),
                            attribute_preferences=person.get("attribute_preferences", []),
                            route=route,
                        )
                    matches.append((fit, item))
                if not matches:
                    # A known conflict (every dish fails a known requirement) is excluded
                    # through the group-menu-fit check below, not sent to Needs confirmation.
                    if unknown:
                        issues.append(
                            "Menu, portion or complete price evidence needs verification."
                        )
                else:
                    def _price_order(pair):
                        m_item = pair[1]
                        if m_item.price and m_item.price.payable_amount_minor is not None:
                            return m_item.price.payable_amount_minor
                        if m_item.price and m_item.price.amount_minor is not None:
                            return m_item.price.amount_minor
                        return 999999

                    matches.sort(
                        key=lambda pair: (
                            -pair[0].fit,
                            pair[1].meal_role not in CONFIRMED_MEAL_ROLES,
                            -craving_name_overlap(pair[1], response),
                            _price_order(pair),
                            pair[1].name.casefold(),
                        )
                    )
                    best.append(matches[0])
            if issues:
                rejected_candidates.append(
                    {"outlet_id": outlet.outlet_id, "category": "hard_requirement"}
                )
                result["verification"].append(
                    {
                        "outlet_id": outlet.outlet_id,
                        "name": outlet.name,
                        "reasons": [
                            "Additional evidence or a private requirement review is needed before this place can be shortlisted."
                        ],
                    }
                )
                continue
            if len(best) != len(people):
                rejected_candidates.append(
                    {"outlet_id": outlet.outlet_id, "category": "group_menu_fit"}
                )
                continue
            fits = [fit.fit for fit, item in best]
            if min(fits) < MINIMUM_INDIVIDUAL_FIT:
                rejected_candidates.append(
                    {"outlet_id": outlet.outlet_id, "category": "fit_floor"}
                )
                preference_conflict = True
                continue
            novelty = sum(
                novelty_feature(person, outlet.outlet_id, at=now).value
                for person in people
            ) / len(people)
            # No adequately licensed quality signal is connected: use the PRD neutral prior.
            score = base_score(fits, quality=0.5, novelty=novelty)
            low_coverage = any(
                fit.coverage < LOW_COVERAGE_THRESHOLD for fit, item in best
            )
            chosen = {item.item_id: item for _, item in best}
            known_prices = [
                item.price.payable_amount_minor
                for item in chosen.values()
                if item.price and item.price.payable_amount_minor is not None
            ]
            if known_prices:
                price_range = {"min_minor": min(known_prices), "max_minor": max(known_prices)}
            else:
                price_range = {"min_minor": None, "max_minor": None, "status": "unknown"}
            evidence_ids = (
                set(outlet.source_ids)
                | set(opening.source_ids)
                | {sid for item in chosen.values() for sid in item.source_ids}
            )
            option_id = hashlib.sha256(
                f"{self.scoring_policy}:{FEATURE_VERSION}:{ONTOLOGY_VERSION}:{meal.get('id', '')}:{snapshot.get('revision', meal.get('revision'))}:{self.fingerprint}:{outlet.outlet_id}".encode()
            ).hexdigest()[:24]
            travel_aggregate = {
                "evidence_status": "firm" if person_routes else "unknown",
                "route_checked_count": len(person_routes),
            }
            for uid, ev in person_routes.items():
                if uid not in result["_private_routes"]:
                    result["_private_routes"][uid] = {}
                result["_private_routes"][uid][option_id] = {
                    "outlet_id": outlet.outlet_id,
                    "eta_minutes": ev.duration_minutes,
                    "distance_km": (
                        round(ev.distance_meters / 1000.0, 1)
                        if ev.distance_meters is not None
                        else None
                    ),
                    "evidence_fresh": ev.is_usable(at=now),
                    "provider_id": ev.provider_id,
                }

            tradeoffs = [
                "Distance is straight-line, not a travel-time estimate.",
                "Menu availability still needs confirmation when ordering.",
            ]
            if any(item.price is None or not item.price.all_mandatory_charges_known for item in chosen.values()):
                tradeoffs.append("Complete payable prices are not published; budget needs confirmation.")
            if any(item.meal_role == "unknown" or item.serves_min is None for item in chosen.values()):
                tradeoffs.append("Serving size or meal role is unknown.")
            if opening.passes is None:
                tradeoffs.append("Dated opening exceptions or kitchen last order need confirmation before visit.")
            if low_coverage:
                tradeoffs.append("Some taste or practical details are missing; fit uses neutral assumptions.")

            candidates.append(
                {
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
                    "travel_aggregate": travel_aggregate,
                    "reasons": [
                        "A suitable menu choice for each included person",
                        "Listed payable meal prices fit all submitted caps",
                    ],
                    "tradeoffs": tradeoffs,
                    "menu_items": [
                        {
                            "id": item.item_id,
                            "name": item.name,
                            "price_minor": (
                                item.price.payable_amount_minor
                                if (item.price and item.price.payable_amount_minor is not None)
                                else (item.price.amount_minor if (item.price and item.price.amount_minor is not None) else None)
                            ),
                        }
                        for item in chosen.values()
                    ],
                    "evidence": [
                        {
                            "url": source.url,
                            "observed_at": source.observed_at.isoformat(),
                        }
                        for source in self.catalog.sources
                        if source.source_id in evidence_ids
                    ],
                    "_score": score,
                    # Retrieval relevance breaks ties ahead of distance (decision 3).
                    "_relevance": relevance.get(outlet.outlet_id, 0.0),
                    # Every diner's dish is a confirmed main/set, not an unknown-role item.
                    "_confirmed_meal": all(
                        item.meal_role in CONFIRMED_MEAL_ROLES for _, item in best
                    ),
                    "_fits": fits,
                }
            )
            eligible_candidates.append(
                {"outlet_id": outlet.outlet_id, "option_id": option_id}
            )
        result["_exposure_candidates"] = {
            "eligible": eligible_candidates,
            "rejected": rejected_candidates,
        }
        # Coverage: a diner's best match is the eligible outlet where *their own* PRD fit
        # is highest; their own search relevance only breaks ties between equal fits.
        for position, relevance_by_outlet in own_relevance.items():
            keyed = [
                (round(c["_fits"][position], 6), relevance_by_outlet.get(c["outlet_id"], 0.0))
                for c in candidates
            ]
            if keyed:
                top = max(keyed)
                for c, key in zip(candidates, keyed):
                    if key == top:
                        serves.setdefault(c["outlet_id"], set()).add(position)
        for c in candidates:
            c["_serves"] = sorted(serves.get(c["outlet_id"], ()))
        selected = rank_diverse(candidates)
        for option in selected:
            option.pop("_score")
            option.pop("_relevance", None)
            option.pop("_serves", None)
            option.pop("_fits", None)
            option.pop("_confirmed_meal", None)
        result["options"] = selected
        if selected:
            result.update(
                status="shortlisted",
                explanation=f"Found {len(selected)} options in the imported catalog. Everyone can review them privately before a group decision.",
            )
        elif preference_conflict:
            result.update(
                status="needs_input",
                explanation="The checked options have a low preference fit for part of the group. Review today's cuisine or meal-style choices, then try again.",
            )
        elif result["verification"] or evaluation_blocked:
            result.update(
                status="needs_verification",
                explanation="Catalog coverage or available evidence is incomplete, so suitability cannot yet be established for this meal.",
            )
        return result
