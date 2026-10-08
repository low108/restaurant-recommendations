"""Local catalog review and aggregate publication status, without fact inference.

An upgrade only adds unknown defaults and queues obvious textual contradictions.
It never grants usage rights, renews source evidence, or certifies dietary safety.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from dining.catalog.models import Catalog, MenuItem
from dining.core.constants import PLANT_BASED_DIETS

_ANIMAL_TERMS = r"(?:beef|chicken|pork|lamb|bacon|ham|duck|turkey|fish|prawns?|shrimps?|anchov(?:y|ies)|salmon|tuna)"
_ANIMAL_WORD = re.compile(rf"\b{_ANIMAL_TERMS}\b", re.IGNORECASE)
_QUALIFIED_ALTERNATIVE = re.compile(
    rf"\b(?:plant[- ]based|mock|vegan|vegetarian|meatless|no|without)\s+{_ANIMAL_TERMS}\b",
    re.IGNORECASE,
)


def _mentions_animal(text: str) -> bool:
    # Avoid treating explicitly named substitutes or simple negations as meat.
    # This bounded screen is a review queue, never a complete ingredient parser.
    return bool(_ANIMAL_WORD.search(_QUALIFIED_ALTERNATIVE.sub("", text)))


def mentions_animal(text: str) -> bool:
    """Public bounded animal-word screen (same limits as the review queue)."""
    return _mentions_animal(text)


def dietary_claim_conflicts(item: MenuItem) -> tuple[str, ...]:
    """Flag obvious animal-word conflicts in vegetarian/vegan claims for review.

    No flag means only that this limited check found none; it is not an approval.
    Names/ingredients/descriptions are checked separately from variant toppings.
    """
    claims = {claim.casefold().strip() for claim in item.dietary_claims}
    if not claims.intersection(PLANT_BASED_DIETS):
        return ()
    reasons = []
    if _mentions_animal(item.variant):
        reasons.append("dietary_claim_conflicts_with_variant")
    if any(
        _mentions_animal(value)
        for value in (item.name, item.description, *item.ingredients)
    ):
        reasons.append("dietary_claim_conflicts_with_text")
    return tuple(reasons)


def upgrade_catalog(catalog: Catalog) -> Catalog:
    """Return schema v2 with original facts retained and contradictions quarantined.

    Absent review fields default to unreviewed in the loader. Explicit review is
    retained unless this bounded contradiction check requires operator attention.
    No category, source permission, portion size, or price basis is inferred.
    """
    payload = catalog.model_dump(mode="json")
    payload["schema_version"] = "2"
    for item, row in zip(catalog.menu_items, payload["menu_items"], strict=True):
        conflicts = dietary_claim_conflicts(item)
        if conflicts:
            row["review_status"] = "quarantined"
            row["review_reasons"] = list(
                dict.fromkeys((*item.review_reasons, *conflicts))
            )
    return Catalog.model_validate(payload)


def public_catalog_status(catalog: Catalog, *, at: datetime | None = None) -> dict:
    """Content-free coverage counts safe to show before source display approval.

    `reviewed` describes catalog review completeness, never whether a restaurant
    is suitable for an individual, open at a future visit, or free of allergens.
    """
    items = catalog.menu_items
    sources = catalog.sources
    outlets = catalog.outlets
    menus = {item.outlet_id for item in items}
    coordinates = sum(
        outlet.latitude is not None and outlet.longitude is not None
        for outlet in outlets
    )
    hours = sum(
        outlet.hours_status == "published" and bool(outlet.opening_hours)
        for outlet in outlets
    )
    reviewed = sum(item.review_status == "reviewed" for item in items)
    quarantined = sum(item.review_status == "quarantined" for item in items)
    display = sum(source.usable_for("display", at) for source in sources)
    embed = sum(source.usable_for("embed", at) for source in sources)
    issues = []

    def issue(code: str, message: str, count: int) -> None:
        if count:
            issues.append({"code": code, "message": message, "count": count})

    issue(
        "unreviewed_items",
        "Menu records still need review.",
        sum(item.review_status == "unreviewed" for item in items),
    )
    issue(
        "quarantined_items",
        "Menu records are held for corrections or evidence review.",
        quarantined,
    )
    issue(
        "dietary_claim_conflicts",
        "Some dietary claims conflict with menu text and need review.",
        sum(bool(dietary_claim_conflicts(item)) for item in items),
    )
    issue(
        "outlets_without_menus",
        "Outlets have no linked menu records.",
        len(outlets) - len(menus),
    )
    issue(
        "outlets_without_coordinates",
        "Outlets need verified coordinates for distance filtering.",
        len(outlets) - coordinates,
    )
    issue(
        "outlets_without_hours",
        "Outlets need published hours for visit-time checks.",
        len(outlets) - hours,
    )
    issue(
        "unknown_meal_roles",
        "Menu records need a meal category.",
        sum(item.meal_role == "unknown" for item in items),
    )
    issue(
        "unknown_servings",
        "Menu records need a documented serving range.",
        sum(item.serves_min is None for item in items),
    )
    issue(
        "missing_prices",
        "Menu records have no structured price.",
        sum(item.price is None for item in items),
    )
    issue(
        "unknown_price_units",
        "Prices need a documented unit.",
        sum(item.price is not None and item.price.unit == "unknown" for item in items),
    )
    issue(
        "unknown_minimum_quantities",
        "Prices need a documented minimum order quantity.",
        sum(
            item.price is not None and item.price.minimum_quantity is None
            for item in items
        ),
    )
    issue(
        "unknown_channels",
        "Prices need a confirmed ordering channel.",
        sum(
            item.price is not None and item.price.channel == "unknown" for item in items
        ),
    )
    issue(
        "unknown_payable_totals",
        "Prices need known mandatory charges before budget checks.",
        sum(
            item.price is not None and not item.price.all_mandatory_charges_known
            for item in items
        ),
    )
    issue(
        "sources_not_current_for_display",
        "Sources need current evidence and display permission before publication.",
        len(sources) - display,
    )
    issue(
        "sources_not_current_for_embed",
        "Sources need current evidence and embedding permission before indexing.",
        len(sources) - embed,
    )
    if not outlets and not items:
        status = "empty"
    elif catalog.synthetic:
        status = "demo"
    elif issues:
        status = "review_required"
    else:
        status = "reviewed"
    return {
        "catalog_id": catalog.catalog_id,
        "version": catalog.version,
        "schema_version": catalog.schema_version,
        "synthetic": catalog.synthetic,
        "outlet_count": len(outlets),
        "item_count": len(items),
        "source_count": len(sources),
        "outlets_with_menus": len(menus),
        "outlets_with_coordinates": coordinates,
        "outlets_with_hours": hours,
        "reviewed_item_count": reviewed,
        "quarantined_item_count": quarantined,
        "sources_current_for_display": display,
        "sources_current_for_embed": embed,
        "status": status,
        "issues": issues,
    }


def audit_catalog(catalog: Catalog, *, at: datetime | None = None) -> dict:
    """Operator-only report with record IDs; do not return this through public API."""
    item_reviews = []
    for item in catalog.menu_items:
        flags = list(
            dict.fromkeys((*item.review_reasons, *dietary_claim_conflicts(item)))
        )
        if item.review_status != "reviewed" or flags:
            item_reviews.append(
                {
                    "item_id": item.item_id,
                    "outlet_id": item.outlet_id,
                    "review_status": item.review_status,
                    "review_reasons": flags,
                }
            )
    outlet_sizes = Counter(item.outlet_id for item in catalog.menu_items)
    return {
        "summary": public_catalog_status(catalog, at=at),
        "item_reviews": item_reviews,
        "menu_items_by_outlet": dict(sorted(outlet_sizes.items())),
        "note": "Automated flags are review leads, not factual verification. Source rights, freshness and original claims are unchanged.",
    }
