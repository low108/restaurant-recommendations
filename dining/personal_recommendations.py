"""Owner-scoped personal recommendations from the group-eligible candidate pool.

MAKAN-212: the outlet must come from the group's shortlist, and the dish must pass the
diner's own firm checks. Ranking uses the diner's PRD individual fit, with no group-fairness
term. Ties break on whole-word craving matches in the dish name, then price.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .catalog import Catalog, MenuItem
from .catalog_audit import dietary_claim_conflicts
from .ranking import (
    CONFIRMED_MEAL_ROLES,
    NEUTRAL_WORDS,
    NON_MEAL_ROLES,
    PER_WEIGHT,
    craving_match_level,
    craving_name_overlap,
    item_search_text,
    name_term_overlap,
    score_item,
)
from .store import decode, encode

PERSONAL_SCORING_VERSION = "personal-fit-v2"
RETRIEVAL_VERSION = "retrieval-policy-v1"


def _price_minor(item: MenuItem) -> int | None:
    """Per-person meal price; per-weight prices are not a meal price (unknown)."""
    if item.price is None or item.price.unit in {"weight_100g", "weight_kg"}:
        return None
    if PER_WEIGHT.search(f"{item.name} {item.variant}"):
        return None
    if item.price.payable_amount_minor is not None:
        return item.price.payable_amount_minor
    return item.price.amount_minor


def _eligible_for(
    item: MenuItem, profile: dict, response: dict, synthetic: bool
) -> bool:
    """The diner's own firm checks; unknown evidence never passes."""
    if (
        item.review_status != "reviewed"
        or dietary_claim_conflicts(item)
        or item.live_availability == "unavailable"
        or item.meal_role in NON_MEAL_ROLES
        or (item.serves_min is not None and item.serves_min > 1)
    ):
        return False
    restrictions = {s.casefold() for s in profile.get("dietary_requirements", [])}
    if restrictions and not restrictions <= {c.casefold() for c in item.dietary_claims}:
        return False
    avoid = {s.casefold() for s in response.get("avoid", [])}
    if avoid and (
        not item.ingredients_complete
        or avoid & {i.casefold() for i in item.ingredients}
    ):
        return False
    if synthetic and (
        item.price is None
        or item.price.payable_amount_minor is None
        or item.meal_role not in {"main", "set"}
    ):
        return False
    budget = response.get("budget", profile.get("max_budget"))
    if budget is not None:
        price = _price_minor(item)
        if price is None or price > round(float(budget) * 100):
            return False
    return True


def _reason_codes(item: MenuItem, profile: dict, response: dict) -> list[str]:
    codes = ["GROUP_ELIGIBLE_POOL"]
    level = craving_match_level(item, response)
    if level == "exact":
        codes.append("CRAVING_MATCH")
    elif level == "broader":
        codes.append("DISH_FAMILY_MATCH")
    cuisines = {
        c.casefold() for c in profile.get("cuisines", []) + response.get("cuisines", [])
    }
    if cuisines & {c.casefold() for c in item.cuisine_tags}:
        codes.append("FAVOURITE_CUISINE_MATCH")
    budget = response.get("budget", profile.get("max_budget"))
    price = _price_minor(item)
    if budget is not None and price is not None and price <= round(float(budget) * 100):
        codes.append("WITHIN_COMFORT_BUDGET")
    if response.get("appetite") == "light" and "light" in item.attributes:
        codes.append("LIGHT_MEAL_MATCH")
    return codes


def score_personal_options(
    diner: dict[str, Any],
    candidate_options: list[dict[str, Any]],
    catalog: Catalog,
) -> list[dict[str, Any]]:
    """Up to 3 owner-scoped recommendations, each with an individually eligible dish."""
    if not candidate_options:
        return []
    profile = diner.get("profile", {})
    response = diner.get("response", {})
    at = datetime.now(timezone.utc)
    outlets = {o.outlet_id: o for o in catalog.outlets}
    scored: list[tuple[tuple, dict[str, Any]]] = []

    craving = (response.get("craving") or "").strip()
    if craving.casefold() in NEUTRAL_WORDS:
        craving = ""
    for position, option in enumerate(candidate_options):
        outlet = outlets.get(option.get("outlet_id"))
        if outlet is None:
            continue
        # Does anything on this outlet's reviewed menu answer the diner's own wish?
        # (e.g. a dessert craving points to the dessert shop, whose meal is then chosen)
        outlet_match = (
            max(
                (
                    name_term_overlap(craving, item_search_text(i))
                    for i in catalog.menu_items
                    if i.outlet_id == outlet.outlet_id and i.review_status == "reviewed"
                ),
                default=0.0,
            )
            if craving
            else 0.0
        )
        best = None
        for item in catalog.menu_items:
            if item.outlet_id != outlet.outlet_id or not _eligible_for(
                item, profile, response, catalog.synthetic
            ):
                continue
            fit = score_item(
                item,
                profile,
                response,
                outlet_id=outlet.outlet_id,
                at=at,
                observations=diner.get("observations", []),
                venue_preferences=diner.get("venue_preferences", []),
                attribute_preferences=diner.get("attribute_preferences", []),
                route=diner.get("route_estimates", {}).get(outlet.outlet_id),
            )
            key = (
                -round(fit.fit, 9),
                item.meal_role not in CONFIRMED_MEAL_ROLES,
                -craving_name_overlap(item, response),
                _price_minor(item) or 0,
                item.name.casefold(),
            )
            if best is None or key < best[0]:
                best = (key, item, fit)
        if best is None:
            continue  # No dish passes this diner's own checks here.
        key, item, fit = best
        scored.append(
            (
                (key[0], -outlet_match, key[1], key[2], position),
                {
                    "outlet_id": outlet.outlet_id,
                    "outlet_name": outlet.name,
                    "item_id": item.item_id,
                    "item_name": item.name,
                    "price_minor": _price_minor(item) or 0,
                    "score": round(min(1.0, max(0.0, fit.fit)), 4),
                    "eligibility_receipt": f"eligible-{outlet.outlet_id}",
                    "reason_codes": _reason_codes(item, profile, response),
                    "status": "suggested",
                },
            )
        )

    # Equal fits keep the group's order, so ties never reshuffle arbitrarily.
    scored.sort(key=lambda pair: pair[0])
    top = [entry for _, entry in scored[:3]]
    for rank, entry in enumerate(top, 1):
        entry["rank"] = rank
    return top


def save_personal_recommendations(
    db: Any,
    meal_id: str,
    user_id: str,
    meal_revision: int,
    options: list[dict[str, Any]],
) -> None:
    now_stamp = datetime.now(timezone.utc).isoformat()
    for opt in options:
        db.execute(
            """
            INSERT OR REPLACE INTO personal_recommendations (
                meal_id, user_id, meal_revision, outlet_id, item_id,
                rank, score, scoring_version, retrieval_version, eligibility_receipt,
                reason_codes, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                meal_id,
                user_id,
                meal_revision,
                opt["outlet_id"],
                opt["item_id"],
                opt["rank"],
                opt["score"],
                PERSONAL_SCORING_VERSION,
                RETRIEVAL_VERSION,
                opt["eligibility_receipt"],
                encode(opt["reason_codes"]),
                opt.get("status", "suggested"),
                now_stamp,
                now_stamp,
            ),
        )


def get_personal_recommendations(
    db: Any,
    meal_id: str,
    user_id: str,
    meal_revision: int,
    catalog: Catalog | None = None,
) -> list[dict[str, Any]]:
    # Never silently reuse results from another revision (MAKAN-212 "Invalidation").
    target_rev = meal_revision

    rows = db.execute(
        """
        SELECT * FROM personal_recommendations
        WHERE meal_id=? AND user_id=? AND meal_revision=?
        ORDER BY rank ASC
        """,
        (meal_id, user_id, target_rev),
    ).fetchall()

    recs = []
    for r in rows:
        oid = r["outlet_id"]
        iid = r["item_id"]
        outlet_name = oid
        item_name = iid
        price_minor = 0
        if catalog:
            outlet = next((o for o in catalog.outlets if o.outlet_id == oid), None)
            if outlet:
                outlet_name = outlet.name
            item = next((i for i in catalog.menu_items if i.item_id == iid), None)
            if item:
                item_name = item.name
                if item.price and item.price.payable_amount_minor is not None:
                    price_minor = item.price.payable_amount_minor
                elif item.price and item.price.amount_minor is not None:
                    price_minor = item.price.amount_minor

        recs.append(
            {
                "outlet_id": oid,
                "outlet_name": outlet_name,
                "item_id": iid,
                "item_name": item_name,
                "price_minor": price_minor,
                "rank": r["rank"],
                "score": r["score"],
                "scoring_version": r["scoring_version"],
                "eligibility_receipt": r["eligibility_receipt"],
                "reason_codes": decode(r["reason_codes"], []),
                "status": r["status"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
        )
    return recs
