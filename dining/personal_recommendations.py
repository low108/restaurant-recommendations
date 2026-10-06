"""Owner-scoped personal recommendations from the group-eligible candidate pool."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .catalog import Catalog
from .store import decode, encode

PERSONAL_SCORING_VERSION = "personal-fit-v1"
RETRIEVAL_VERSION = "retrieval-policy-v1"


def score_personal_options(
    diner: dict[str, Any],
    candidate_options: list[dict[str, Any]],
    catalog: Catalog,
) -> list[dict[str, Any]]:
    """Generate up to 3 owner-scoped personal recommendations from group-eligible options."""
    if not candidate_options:
        return []

    profile = diner.get("profile", {})
    response = diner.get("response", {})
    craving = (response.get("craving") or "").casefold().strip()
    preferred_cuisines = {
        c.casefold() for c in profile.get("cuisines", []) + response.get("cuisines", [])
    }
    budget = response.get("budget", profile.get("max_budget")) or 50

    scored_outlets: list[dict[str, Any]] = []

    for option in candidate_options:
        outlet_id = option.get("outlet_id")
        if not outlet_id:
            continue

        outlet = next((o for o in catalog.outlets if o.outlet_id == outlet_id), None)
        if not outlet:
            continue

        items = [item for item in catalog.menu_items if item.outlet_id == outlet_id]
        if not items:
            continue

        # Find best eligible menu item for this diner
        best_item = items[0]
        best_item_score = 0.5
        reason_codes = ["GROUP_ELIGIBLE_POOL"]

        for item in items:
            item_score = 0.5
            codes = ["GROUP_ELIGIBLE_POOL"]

            # Dish match
            item_text = (item.name + " " + " ".join(item.attributes)).casefold()
            if craving and any(
                word in item_text for word in craving.split() if len(word) >= 3
            ):
                item_score += 0.25
                codes.append("CRAVING_MATCH")

            # Cuisine match
            if any(c.casefold() in preferred_cuisines for c in item.cuisine_tags):
                item_score += 0.20
                codes.append("FAVOURITE_CUISINE_MATCH")

            # Budget comfort
            price = item.price
            if price and price.payable_amount_minor <= budget * 100:
                item_score += 0.15
                codes.append("WITHIN_COMFORT_BUDGET")

            # Light meal preference
            if response.get("appetite") == "light" and "light" in item.attributes:
                item_score += 0.10
                codes.append("LIGHT_MEAL_MATCH")

            if item_score > best_item_score:
                best_item = item
                best_item_score = item_score
                reason_codes = codes

        # Bounded personal score between 0.0 and 1.0
        final_score = min(1.0, best_item_score)
        scored_outlets.append(
            {
                "outlet_id": outlet.outlet_id,
                "outlet_name": outlet.name,
                "item_id": best_item.item_id,
                "item_name": best_item.name,
                "price_minor": best_item.price.payable_amount_minor
                if best_item.price
                else 0,
                "score": round(final_score, 4),
                "eligibility_receipt": f"eligible-{outlet.outlet_id}",
                "reason_codes": reason_codes,
                "status": "suggested",
            }
        )

    # Sort descending by score, take up to 3 results
    scored_outlets.sort(key=lambda x: -x["score"])
    top_3 = scored_outlets[:3]

    for idx, opt in enumerate(top_3):
        opt["rank"] = idx + 1

    return top_3


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
) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT * FROM personal_recommendations
        WHERE meal_id=? AND user_id=? AND meal_revision=?
        ORDER BY rank ASC
        """,
        (meal_id, user_id, meal_revision),
    ).fetchall()

    recs = []
    for r in rows:
        recs.append(
            {
                "outlet_id": r["outlet_id"],
                "item_id": r["item_id"],
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
