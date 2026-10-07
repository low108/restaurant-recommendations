"""Generate and validate location and operating-hours enrichment data under approved policy.

Policy updates applied:
1. Accept official social media announcements (e.g. verified Facebook/Instagram profiles)
   as authoritative weekly hours sources.
2. Accept building and street-block coordinates where exact individual storefront
   unit polygons are unmapped.

Produces:
1. data/enrichment/location-hours.patch.json (58 verified outlets)
2. data/enrichment/location-hours.review.json (70 unresolved outlets)
3. data/enrichment/location-hours.sources.json (55 new verified sources)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from dining.catalog import OpeningInterval, Source

CATALOG_PATH = Path("/Users/johnathanjohnathan/Documents/restaurant-menu-collection/catalog.real.json")
ENRICHMENT_DIR = Path("data/enrichment")

EXPECTED_SHA256 = "767e25e39957aba4ccb46e49ee136551ebbbc89afe0272282908ce307cf27e77"
CATALOG_ID = "kl-selangor-real-pilot"
CATALOG_VERSION = "0.1.0-partial"
GENERATED_AT = "2026-10-06T13:00:00Z"
OBSERVED_AT = "2026-10-06T10:40:01+08:00"

# Import complete definitions from companion module
from scripts.enrichment_data import NEW_SOURCES, OUTLET_UPDATES, SPECIFIC_REVIEW_NOTES


def build_review_entry(outlet: dict) -> dict:
    oid = outlet["outlet_id"]
    if oid in SPECIFIC_REVIEW_NOTES:
        details = SPECIFIC_REVIEW_NOTES[oid]
        return {
            "outlet_id": oid,
            "status": "needs_review",
            "reason_codes": details["reason_codes"],
            "candidate_urls": details["candidate_urls"],
            "notes": details["notes"],
        }
    
    # Generic scan outlet: scan-derived placeholder identity, approximate/missing coordinates, missing hours
    return {
        "outlet_id": oid,
        "status": "needs_review",
        "reason_codes": ["coordinates_approximate", "hours_missing"],
        "candidate_urls": [f"https://scanmenu.my/{oid[5:-7]}/" if oid.startswith("scan-") and oid.endswith(tuple("0123456789abcdef")) else "https://scanmenu.my/"],
        "notes": "ScanMenu-derived placeholder record; physical building unmapped and full weekly operating schedule unverified.",
    }


def main():
    with open(CATALOG_PATH, encoding="utf-8") as f:
        catalog_data = json.load(f)

    catalog_outlets = catalog_data["outlets"]
    catalog_outlet_ids = {o["outlet_id"] for o in catalog_outlets}
    existing_source_ids = {s["source_id"] for s in catalog_data["sources"]}

    print(f"Loaded catalog with {len(catalog_outlets)} outlets and {len(existing_source_ids)} sources.")

    # 1. Validate all new sources
    for s in NEW_SOURCES:
        Source.model_validate(s)
    new_sids = {s["source_id"] for s in NEW_SOURCES}
    assert len(new_sids) == len(NEW_SOURCES), "Duplicate source_ids in NEW_SOURCES"
    assert not (new_sids & existing_source_ids), "Collision between NEW_SOURCES and existing catalog sources"

    # 2. Validate patch updates
    patched_outlet_ids = {u["outlet_id"] for u in OUTLET_UPDATES}
    assert len(patched_outlet_ids) == len(OUTLET_UPDATES), "Duplicate outlet_id in OUTLET_UPDATES"
    for u in OUTLET_UPDATES:
        assert u["outlet_id"] in catalog_outlet_ids, f"Outlet {u['outlet_id']} not in input catalog"
        assert u["coordinate_precision"] in ("storefront", "building"), f"Invalid precision {u['coordinate_precision']}"
        assert -90 <= u["latitude"] <= 90
        assert -180 <= u["longitude"] <= 180
        assert 2.5 <= u["latitude"] <= 3.8, f"Implausible latitude {u['latitude']} for KL/Selangor"
        assert 101.0 <= u["longitude"] <= 102.2, f"Implausible longitude {u['longitude']} for KL/Selangor"
        
        # Validate all opening hours intervals
        for interval in u["opening_hours"]:
            OpeningInterval.model_validate(interval)
            
        # Check source references
        all_referenced_sids = set(u["source_ids_to_add"]) | set(u["field_evidence"]["coordinates"]) | set(u["field_evidence"]["opening_hours"]) | set(u["field_evidence"]["last_order"])
        for interval in u["opening_hours"]:
            all_referenced_sids |= set(interval.get("last_order_source_ids", []))
        for sid in all_referenced_sids:
            assert sid in new_sids or sid in existing_source_ids, f"Referenced source {sid} does not exist"

    # 3. Build review entries for all remaining outlets
    review_entries = []
    for o in catalog_outlets:
        oid = o["outlet_id"]
        if oid in patched_outlet_ids:
            continue
        entry = build_review_entry(o)
        review_entries.append(entry)

    reviewed_outlet_ids = {r["outlet_id"] for r in review_entries}
    assert len(reviewed_outlet_ids) == len(review_entries), "Duplicate outlet_id in review_entries"
    assert not (patched_outlet_ids & reviewed_outlet_ids), "Overlap between patched and reviewed outlets"
    assert len(patched_outlet_ids) + len(reviewed_outlet_ids) == len(catalog_outlets), "Total outlets must equal input catalog outlets"

    print(f"Patched outlets: {len(OUTLET_UPDATES)}")
    print(f"Reviewed outlets: {len(review_entries)}")
    print(f"Total outlets processed: {len(OUTLET_UPDATES) + len(review_entries)} / {len(catalog_outlets)}")

    # 4. Checkpoints validation (max 20 outlets per checkpoint)
    checkpoints = [
        ("CP1 (0-19)", 0, 20),
        ("CP2 (20-39)", 20, 40),
        ("CP3 (40-59)", 40, 60),
        ("CP4 (60-79)", 60, 80),
        ("CP5 (80-99)", 80, 100),
        ("CP6 (100-119)", 100, 120),
        ("CP7 (120-127)", 120, 128),
    ]

    for cp_name, start_idx, end_idx in checkpoints:
        cp_outlets = catalog_outlets[start_idx:end_idx]
        cp_oids = {o["outlet_id"] for o in cp_outlets}
        cp_patched = [u for u in OUTLET_UPDATES if u["outlet_id"] in cp_oids]
        cp_reviewed = [r for r in review_entries if r["outlet_id"] in cp_oids]
        assert len(cp_patched) + len(cp_reviewed) == len(cp_outlets), f"Discrepancy in checkpoint {cp_name}"
        print(f"Checkpoint {cp_name}: {len(cp_outlets)} outlets ({len(cp_patched)} patched, {len(cp_reviewed)} reviewed)")

    # 5. Write deliverables
    patch_doc = {
        "patch_version": "1",
        "input": {
            "catalog_id": CATALOG_ID,
            "catalog_version": CATALOG_VERSION,
            "sha256": EXPECTED_SHA256,
        },
        "generated_at": GENERATED_AT,
        "outlet_updates": OUTLET_UPDATES,
    }

    review_doc = {
        "review_version": "1",
        "input": {
            "catalog_id": CATALOG_ID,
            "catalog_version": CATALOG_VERSION,
            "sha256": EXPECTED_SHA256,
        },
        "generated_at": GENERATED_AT,
        "review_entries": review_entries,
    }

    sources_doc = {
        "sources_version": "1",
        "input": {
            "catalog_id": CATALOG_ID,
            "catalog_version": CATALOG_VERSION,
            "sha256": EXPECTED_SHA256,
        },
        "generated_at": GENERATED_AT,
        "sources": NEW_SOURCES,
    }

    ENRICHMENT_DIR.mkdir(parents=True, exist_ok=True)
    with open(ENRICHMENT_DIR / "location-hours.patch.json", "w", encoding="utf-8") as f:
        json.dump(patch_doc, f, indent=2, ensure_ascii=False)
    with open(ENRICHMENT_DIR / "location-hours.review.json", "w", encoding="utf-8") as f:
        json.dump(review_doc, f, indent=2, ensure_ascii=False)
    with open(ENRICHMENT_DIR / "location-hours.sources.json", "w", encoding="utf-8") as f:
        json.dump(sources_doc, f, indent=2, ensure_ascii=False)

    print("Successfully generated and validated all 3 enrichment JSON deliverables!")


if __name__ == "__main__":
    main()
