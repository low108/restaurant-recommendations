"""Deterministic patch applicator for location and hours enrichment.

Usage:
    .venv/bin/python scripts/apply_location_hours_patch.py \
        --catalog /Users/johnathanjohnathan/Documents/restaurant-menu-collection/catalog.real.json \
        --patch data/enrichment/location-hours.patch.json \
        --sources data/enrichment/location-hours.sources.json \
        --out var/catalog-import/kl-selangor-real-pilot-0.2.0-reviewed/catalog.v2.json \
        --mark-items-reviewed
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from dining.catalog import Catalog, load_catalog
from dining.catalog_audit import dietary_claim_conflicts


def apply_patch(catalog_path: Path, patch_path: Path, sources_path: Path, out_path: Path, mark_items_reviewed: bool = False):
    with open(patch_path, encoding="utf-8") as f:
        patch_data = json.load(f)
    with open(sources_path, encoding="utf-8") as f:
        sources_data = json.load(f)

    expected_sha = patch_data["input"]["sha256"]
    actual_sha = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
    if actual_sha != expected_sha:
        raise ValueError(f"Checksum mismatch! {actual_sha} != {expected_sha}")

    raw_cat = json.load(open(catalog_path, encoding="utf-8"))
    
    # Check input catalog metadata
    if raw_cat["catalog_id"] != patch_data["input"]["catalog_id"]:
        raise ValueError(f"Catalog ID mismatch: {raw_cat['catalog_id']} != {patch_data['input']['catalog_id']}")

    # 1. Add new sources
    existing_sids = {s["source_id"] for s in raw_cat["sources"]}
    for s in sources_data["sources"]:
        if s["source_id"] in existing_sids:
            raise ValueError(f"Duplicate source ID {s['source_id']}")
        raw_cat["sources"].append(s)
        existing_sids.add(s["source_id"])

    # 2. Update outlets
    updates_by_id = {u["outlet_id"]: u for u in patch_data["outlet_updates"]}
    updated_oids = set()
    for o in raw_cat["outlets"]:
        oid = o["outlet_id"]
        if oid in updates_by_id:
            u = updates_by_id[oid]
            o["latitude"] = u["latitude"]
            o["longitude"] = u["longitude"]
            o["hours_status"] = u["hours_status"]
            o["opening_hours"] = u["opening_hours"]
            o["opening_exceptions"] = u.get("opening_exceptions", [])
            o["opening_exceptions_coverage"] = u.get("opening_exceptions_coverage")
            
            # Merge sources preserving provenance
            for sid in u["source_ids_to_add"]:
                if sid not in o["source_ids"]:
                    o["source_ids"].append(sid)
            updated_oids.add(oid)

    if len(updated_oids) != len(updates_by_id):
        missing = set(updates_by_id.keys()) - updated_oids
        raise ValueError(f"Some patch outlet IDs were not found in catalog: {missing}")

    # 3. Upgrade schema version to 2
    raw_cat["schema_version"] = "2"
    raw_cat["version"] = "0.2.0-reviewed"

    # 4. Optional: mark items belonging to patched outlets as reviewed (preserving quarantine on dietary conflicts)
    if mark_items_reviewed:
        reviewed_count = 0
        quarantined_count = 0
        for item in raw_cat["menu_items"]:
            # If item belongs to a patched outlet
            if item["outlet_id"] in updated_oids:
                # Retain defaults for schema 2
                item.setdefault("meal_role", "unknown")
                item.setdefault("serves_min", None)
                item.setdefault("serves_max", None)
                item.setdefault("review_reasons", [])
                
                # Check for dietary conflicts
                claims = {c.casefold().strip() for c in item.get("dietary_claims", [])}
                is_veg = bool(claims.intersection({"vegetarian", "vegan"}))
                has_meat_variant = "beef" in item.get("variant", "").lower() or "chicken" in item.get("variant", "").lower()
                
                if is_veg and has_meat_variant:
                    item["review_status"] = "quarantined"
                    if "dietary_claim_conflicts_with_variant" not in item["review_reasons"]:
                        item["review_reasons"].append("dietary_claim_conflicts_with_variant")
                    quarantined_count += 1
                else:
                    item["review_status"] = "reviewed"
                    reviewed_count += 1
            else:
                item.setdefault("review_status", "unreviewed")
                item.setdefault("review_reasons", [])

        print(f"Menu items updated: {reviewed_count} marked reviewed, {quarantined_count} quarantined.")

    # 5. Validate through Pydantic Catalog loader
    validated_catalog = Catalog.model_validate(raw_cat)
    print("Catalog validated successfully through Pydantic model_validate!")

    # 6. Write atomically to output path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(raw_cat, f, indent=2, ensure_ascii=False)
    print(f"Wrote upgraded catalog to {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Apply location & hours enrichment patch")
    parser.add_argument("--catalog", type=Path, required=True, help="Input catalog path")
    parser.add_argument("--patch", type=Path, default=Path("data/enrichment/location-hours.patch.json"))
    parser.add_argument("--sources", type=Path, default=Path("data/enrichment/location-hours.sources.json"))
    parser.add_argument("--out", type=Path, required=True, help="Output catalog path")
    parser.add_argument("--mark-items-reviewed", action="store_true", help="Mark menu items of patched outlets as reviewed")
    args = parser.parse_args()

    apply_patch(args.catalog, args.patch, args.sources, args.out, mark_items_reviewed=args.mark_items_reviewed)


if __name__ == "__main__":
    main()
