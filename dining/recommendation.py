"""Deterministic outlet-level recommendation. Menu similarity never grants eligibility."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from datetime import time as local_time
from zoneinfo import ZoneInfo

from dining.catalog import Catalog, MenuItem, Outlet
from dining.catalog_audit import dietary_claim_conflicts, public_catalog_status
from dining.ranking import (
    FEATURE_VERSION,
    LOW_COVERAGE_THRESHOLD,
    MINIMUM_INDIVIDUAL_FIT,
    ONTOLOGY_VERSION,
    POLICY_VERSION,
    base_score,
    novelty_feature,
    rank_diverse,
    score_item,
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
    return ServiceCheck(None if uncertain else False)


def _open_for(outlet: Outlet, at: datetime, duration: int) -> bool | None:
    return _service_for(outlet, at, duration).passes


class Recommender:
    def __init__(
        self,
        catalog: Catalog,
        routing_provider: RoutingProvider | None = None,
    ):
        self.catalog = catalog
        self.routing_provider = routing_provider or get_routing_provider()
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
        result = {
            "status": "no_options",
            "options": [],
            "verification": [],
            "_private_routes": {},
            "catalog_id": self.catalog.catalog_id,
            "catalog_version": self.catalog.version,
            "synthetic": self.catalog.synthetic,
            "policy_version": POLICY_VERSION,
            "feature_version": FEATURE_VERSION,
            "ontology_version": ONTOLOGY_VERSION,
            "coverage": public_catalog_status(self.catalog, at=now),
            "examined_outlets": 0,
            "explanation": "No verified common option was found in the imported catalog.",
        }
        if len(people) < 2:
            result.update(
                status="needs_input",
                explanation="At least two people must complete a check-in.",
            )
            return result
        latitude, longitude = meal.get("latitude"), meal.get("longitude")
        if latitude is None or longitude is None:
            result.update(
                status="needs_input",
                explanation="Add a shared meeting point to compare nearby places.",
            )
            return result
        try:
            at = datetime.fromisoformat(meal["meal_at"].replace("Z", "+00:00"))
            if at.tzinfo is None:
                raise ValueError("Missing timezone")
        except (KeyError, TypeError, ValueError):
            result.update(
                status="needs_input", explanation="Confirm a meal date and time first."
            )
            return result
        if any(
            not p["profile"].get("requirements_reviewed")
            or not p["response"].get("requirements_confirmed")
            for p in people
        ):
            result.update(
                status="needs_verification",
                explanation="Everyone needs to review their private requirements first.",
            )
            return result
        if any(
            p["profile"].get("allergy_status", "unknown") in {"unknown", "withheld"}
            or p["profile"].get("halal_policy", "unknown") in {"unknown", "review"}
            for p in people
        ):
            result.update(
                status="needs_verification",
                explanation="Some requirements need private clarification before suitability can be checked.",
            )
            return result
        has_allergy_needs = any(
            p["profile"].get("allergy_status") == "declared"
            or p["profile"].get("allergens")
            or p["response"].get("avoid")
            for p in people
        )
        if has_allergy_needs and not snapshot.get("preparation_confirmations"):
            result.update(
                status="needs_verification",
                explanation="Some private requirements need further verification before options can be shared.",
            )
            return result
        candidates = []
        eligible_candidates = []
        rejected_candidates = []
        preference_conflict = False
        # Coverage failures are distinct from a known lack of a suitable option.
        # Keep this aggregate-only: blocked sources may not publish outlet names.
        evaluation_blocked = not self.catalog.outlets or not self.catalog.menu_items
        for outlet in self.catalog.outlets:
            if outlet.latitude is None:
                evaluation_blocked = True
                continue
            distance = distance_km(
                latitude, longitude, outlet.latitude, outlet.longitude
            )
            if distance > meal.get("radius_km", 5):
                continue
            result["examined_outlets"] += 1
            if not self.catalog.evidence_usable(outlet.source_ids, at=now):
                evaluation_blocked = True
                rejected_candidates.append(
                    {"outlet_id": outlet.outlet_id, "category": "source_freshness"}
                )
                continue  # Permission/freshness failures must not publish source content.
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
                issues.append(
                    "Dated hours or kitchen service for the planned visit need confirmation."
                )
            if not self.catalog.evidence_usable(outlet.source_ids, at=at):
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

            for req in needed_accessibility:
                feature = getattr(outlet.accessibility, req, None)
                if feature is None or feature.status == "inaccessible":
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
                if budget is None:
                    issues.append("A private meal budget needs confirmation.")
                    continue
                for item in dishes:
                    if item.live_availability == "unavailable":
                        continue
                    # Review is necessary but never overrides known contradictions.
                    # A drink, add-on or dessert is not a standalone meal.
                    if (
                        item.review_status != "reviewed"
                        or item.meal_role not in {"main", "set"}
                        or dietary_claim_conflicts(item)
                    ):
                        unknown = True
                        continue
                    if not self.catalog.evidence_usable(
                        item.source_ids, at=now
                    ) or not self.catalog.evidence_usable(item.source_ids, at=at):
                        unknown = True
                        continue
                    restrictions = {
                        s.casefold() for s in profile.get("dietary_requirements", [])
                    }
                    claims = {s.casefold() for s in item.dietary_claims}
                    if not restrictions.issubset(claims):
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
                    if price.payable_amount_minor > round(float(budget) * 100):
                        continue
                    fit = score_item(
                        item,
                        profile,
                        response,
                        outlet_id=outlet.outlet_id,
                        at=now,
                        observations=person.get("observations", []),
                        venue_preferences=person.get("venue_preferences", []),
                        route=route_info
                        or person.get("route_estimates", {}).get(outlet.outlet_id),
                    )
                    matches.append((fit, item))
                if not matches:
                    if unknown:
                        issues.append(
                            "Menu, portion or complete price evidence needs verification."
                        )
                    else:
                        issues.append(
                            "No suitable meal within every participant's requirements was found."
                        )
                else:
                    matches.sort(
                        key=lambda pair: (
                            -pair[0].fit,
                            pair[1].price.payable_amount_minor,
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
            prices = [item.price.payable_amount_minor for item in chosen.values()]
            evidence_ids = (
                set(outlet.source_ids)
                | set(opening.source_ids)
                | {sid for item in chosen.values() for sid in item.source_ids}
            )
            option_id = hashlib.sha256(
                f"{POLICY_VERSION}:{FEATURE_VERSION}:{ONTOLOGY_VERSION}:{meal.get('id', '')}:{snapshot.get('revision', meal.get('revision'))}:{self.fingerprint}:{outlet.outlet_id}".encode()
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
                    "price_range": {"min_minor": min(prices), "max_minor": max(prices)},
                    "travel_aggregate": travel_aggregate,
                    "reasons": [
                        "A suitable menu choice for each included person",
                        "Listed payable meal prices fit all submitted caps",
                    ],
                    "tradeoffs": [
                        "Distance is straight-line, not a travel-time estimate.",
                        "Menu availability still needs confirmation when ordering.",
                    ]
                    + (
                        [
                            "Some taste or practical details are missing; fit uses neutral assumptions."
                        ]
                        if low_coverage
                        else []
                    ),
                    "menu_items": [
                        {
                            "id": item.item_id,
                            "name": item.name,
                            "price_minor": item.price.payable_amount_minor,
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
                }
            )
            eligible_candidates.append(
                {"outlet_id": outlet.outlet_id, "option_id": option_id}
            )
        result["_exposure_candidates"] = {
            "eligible": eligible_candidates,
            "rejected": rejected_candidates,
        }
        selected = rank_diverse(candidates)
        for option in selected:
            option.pop("_score")
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
