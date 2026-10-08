"""Private "Best fit for you" recommendations (MAKAN-212).

For each diner, separately:

* only outlets from the **group's shortlist** are considered;
* the dish must pass **that diner's own** firm checks (diet, avoid list, firm cap with a
  known price, a real meal, reviewed and available);
* outlets are ranked by the diner's PRD individual fit, with no group-fairness term.

Ties break, in order, on: an outlet whose menu answers the diner's own wish, a confirmed
main/set, whole-word craving words in the dish name, then the group's order.

Two classes keep scoring and storage apart:

* :class:`PersonalRecommender` – pure scoring over a catalog.
* :class:`PersonalRecommendationRepository` – reading/writing the owner-scoped table.

The module-level functions are the stable interface the API and tests use.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dining.catalog.audit import dietary_claim_conflicts
from dining.catalog.models import Catalog, MenuItem, Outlet
from dining.core.constants import PER_WEIGHT_UNITS, RETRIEVAL_POLICY_VERSION
from dining.core.store import decode, encode
from dining.recommendation.ranking import (
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

PERSONAL_SCORING_VERSION = "personal-fit-v2"
RETRIEVAL_VERSION = RETRIEVAL_POLICY_VERSION
MAX_PERSONAL_RESULTS = 3


def _price_minor(item: MenuItem) -> int | None:
    """Per-person meal price in sen; per-weight prices are not a meal price (None)."""
    if item.price is None or item.price.unit in PER_WEIGHT_UNITS:
        return None
    if PER_WEIGHT.search(f"{item.name} {item.variant}"):
        return None
    if item.price.payable_amount_minor is not None:
        return item.price.payable_amount_minor
    return item.price.amount_minor


class PersonalRecommender:
    """Scores the group's shortlist for one diner at a time."""

    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self._outlets = {o.outlet_id: o for o in catalog.outlets}

    # ------------------------------------------------------------------ public interface
    def recommend(
        self, diner: dict[str, Any], candidate_options: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Up to three owner-scoped picks, each with a dish this diner can actually have."""
        if not candidate_options:
            return []
        profile = diner.get("profile", {})
        response = diner.get("response", {})
        craving = self._search_craving(response)
        at = datetime.now(timezone.utc)

        scored: list[tuple[tuple, dict[str, Any]]] = []
        for position, option in enumerate(candidate_options):
            outlet = self._outlets.get(option.get("outlet_id"))
            if outlet is None:
                continue
            best = self._best_dish(diner, outlet, profile, response, at)
            if best is None:
                continue  # no dish passes this diner's own checks here
            (fit_key, role_key, words_key), item, fit = best
            ranking_key = (
                fit_key,
                -self._outlet_answers_wish(outlet, craving),
                role_key,
                words_key,
                position,
            )
            scored.append(
                (ranking_key, self._card(outlet, item, fit, profile, response))
            )

        # Equal fits keep the group's order, so ties never reshuffle arbitrarily.
        scored.sort(key=lambda pair: pair[0])
        top = [card for _, card in scored[:MAX_PERSONAL_RESULTS]]
        for rank, card in enumerate(top, 1):
            card["rank"] = rank
        return top

    # ------------------------------------------------------------------------- helpers
    @staticmethod
    def _search_craving(response: dict) -> str:
        """Today's craving text, or '' when it is a neutral answer like 'anything'."""
        craving = (response.get("craving") or "").strip()
        return "" if craving.casefold() in NEUTRAL_WORDS else craving

    def _outlet_answers_wish(self, outlet: Outlet, craving: str) -> float:
        """Best word overlap between the craving and *any* reviewed item at the outlet.

        A dessert craving points to the dessert shop, whose real meal is then chosen.
        """
        if not craving:
            return 0.0
        return max(
            (
                name_term_overlap(craving, item_search_text(i))
                for i in self.catalog.menu_items
                if i.outlet_id == outlet.outlet_id and i.review_status == "reviewed"
            ),
            default=0.0,
        )

    def _best_dish(self, diner, outlet, profile, response, at):
        """The diner's best eligible dish at one outlet: ((sort keys), item, fit) or None."""
        best = None
        for item in self.catalog.menu_items:
            if item.outlet_id != outlet.outlet_id or not self.is_eligible(
                item, profile, response
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
            return None
        key, item, fit = best
        return (key[0], key[1], key[2]), item, fit

    def is_eligible(self, item: MenuItem, profile: dict, response: dict) -> bool:
        """The diner's own firm checks; unknown evidence never passes."""
        if (
            item.review_status != "reviewed"
            or dietary_claim_conflicts(item)
            or item.live_availability == "unavailable"
            or item.meal_role in NON_MEAL_ROLES
            or (item.serves_min is not None and item.serves_min > 1)  # sharing sizes
        ):
            return False
        restrictions = {s.casefold() for s in profile.get("dietary_requirements", [])}
        if restrictions and not restrictions <= {
            c.casefold() for c in item.dietary_claims
        }:
            return False
        avoid = {s.casefold() for s in response.get("avoid", [])}
        if avoid and (
            not item.ingredients_complete
            or avoid & {i.casefold() for i in item.ingredients}
        ):
            return False
        if self.catalog.synthetic and (
            item.price is None
            or item.price.payable_amount_minor is None
            or item.meal_role not in CONFIRMED_MEAL_ROLES
        ):
            return False
        budget = response.get("budget", profile.get("max_budget"))
        if budget is not None:
            price = _price_minor(item)
            if price is None or price > round(float(budget) * 100):
                return False
        return True

    @staticmethod
    def reason_codes(item: MenuItem, profile: dict, response: dict) -> list[str]:
        """Allowlisted, truthful reasons shown on the private card."""
        codes = ["GROUP_ELIGIBLE_POOL"]
        level = craving_match_level(item, response)
        if level == "exact":
            codes.append("CRAVING_MATCH")
        elif level == "broader":
            codes.append("DISH_FAMILY_MATCH")
        cuisines = {
            c.casefold()
            for c in profile.get("cuisines", []) + response.get("cuisines", [])
        }
        if cuisines & {c.casefold() for c in item.cuisine_tags}:
            codes.append("FAVOURITE_CUISINE_MATCH")
        budget = response.get("budget", profile.get("max_budget"))
        price = _price_minor(item)
        if (
            budget is not None
            and price is not None
            and price <= round(float(budget) * 100)
        ):
            codes.append("WITHIN_COMFORT_BUDGET")
        if response.get("appetite") == "light" and "light" in item.attributes:
            codes.append("LIGHT_MEAL_MATCH")
        return codes

    def _card(self, outlet, item, fit, profile, response) -> dict[str, Any]:
        """The private recommendation record (rank is added after sorting)."""
        return {
            "outlet_id": outlet.outlet_id,
            "outlet_name": outlet.name,
            "item_id": item.item_id,
            "item_name": item.name,
            "price_minor": _price_minor(item) or 0,
            "score": round(min(1.0, max(0.0, fit.fit)), 4),
            "eligibility_receipt": f"eligible-{outlet.outlet_id}",
            "reason_codes": self.reason_codes(item, profile, response),
            "status": "suggested",
        }


class PersonalRecommendationRepository:
    """Owner-scoped storage in the ``personal_recommendations`` table."""

    def __init__(self, db: Any):
        self.db = db

    def save(
        self,
        meal_id: str,
        user_id: str,
        meal_revision: int,
        options: list[dict[str, Any]],
    ) -> None:
        """Store one diner's picks for one meal revision (replacing the same ranks)."""
        stamp = datetime.now(timezone.utc).isoformat()
        for option in options:
            self.db.execute(
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
                    option["outlet_id"],
                    option["item_id"],
                    option["rank"],
                    option["score"],
                    PERSONAL_SCORING_VERSION,
                    RETRIEVAL_VERSION,
                    option["eligibility_receipt"],
                    encode(option["reason_codes"]),
                    option.get("status", "suggested"),
                    stamp,
                    stamp,
                ),
            )

    def load(
        self,
        meal_id: str,
        user_id: str,
        meal_revision: int,
        catalog: Catalog | None = None,
    ) -> list[dict]:
        """One diner's picks for exactly this revision (never an older one).

        MAKAN-212 "Invalidation": results from another revision are never reused.
        """
        rows = self.db.execute(
            """
            SELECT * FROM personal_recommendations
            WHERE meal_id=? AND user_id=? AND meal_revision=?
            ORDER BY rank ASC
            """,
            (meal_id, user_id, meal_revision),
        ).fetchall()
        return [self._view(row, catalog) for row in rows]

    @staticmethod
    def _view(row, catalog: Catalog | None) -> dict[str, Any]:
        """A stored row with current outlet/dish names and price from the catalog."""
        outlet_name, item_name, price_minor = row["outlet_id"], row["item_id"], 0
        if catalog:
            outlet = next(
                (o for o in catalog.outlets if o.outlet_id == row["outlet_id"]), None
            )
            if outlet:
                outlet_name = outlet.name
            item = next(
                (i for i in catalog.menu_items if i.item_id == row["item_id"]), None
            )
            if item:
                item_name = item.name
                if item.price and item.price.payable_amount_minor is not None:
                    price_minor = item.price.payable_amount_minor
                elif item.price and item.price.amount_minor is not None:
                    price_minor = item.price.amount_minor
        return {
            "outlet_id": row["outlet_id"],
            "outlet_name": outlet_name,
            "item_id": row["item_id"],
            "item_name": item_name,
            "price_minor": price_minor,
            "rank": row["rank"],
            "score": row["score"],
            "scoring_version": row["scoring_version"],
            "eligibility_receipt": row["eligibility_receipt"],
            "reason_codes": decode(row["reason_codes"], []),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


# ------------------------------------------------------------------ stable function API
def score_personal_options(
    diner: dict[str, Any], candidate_options: list[dict[str, Any]], catalog: Catalog
) -> list[dict[str, Any]]:
    """Up to 3 owner-scoped recommendations, each with an individually eligible dish."""
    return PersonalRecommender(catalog).recommend(diner, candidate_options)


def save_personal_recommendations(
    db: Any,
    meal_id: str,
    user_id: str,
    meal_revision: int,
    options: list[dict[str, Any]],
) -> None:
    PersonalRecommendationRepository(db).save(meal_id, user_id, meal_revision, options)


def get_personal_recommendations(
    db: Any,
    meal_id: str,
    user_id: str,
    meal_revision: int,
    catalog: Catalog | None = None,
) -> list[dict[str, Any]]:
    return PersonalRecommendationRepository(db).load(
        meal_id, user_id, meal_revision, catalog
    )
