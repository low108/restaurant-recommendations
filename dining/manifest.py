"""Deterministic allowlist manifest and activation verification for the 58 verified outlets."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dining.catalog import Catalog, load_catalog

EXPECTED_ORIGINAL_CATALOG_SHA = (
    "767e25e39957aba4ccb46e49ee136551ebbbc89afe0272282908ce307cf27e77"
)
EXPECTED_PATCH_SHA = (
    "8e6eab3f4cd2872fa5bc68ec810f5bd20931f7a164a6c60458b64393e2e7f1d8"
)
EXPECTED_SOURCES_SHA = (
    "d34ea1e9a9735d05bc5daa5da3fd607198e62098d658d388318bee53845efcd2"
)
EXPECTED_RESOLVED_CATALOG_SHA = (
    "1105d2d6479b2e1a6edae8d31433593de2d76a48c494c8a7e3a00ba7b5aedd70"
)


def file_sha256(path: Path | str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    descriptor, name = tempfile.mkstemp(prefix="manifest-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def generate_activation_manifest(
    patch_path: Path | str = "data/enrichment/location-hours.patch.json",
    sources_path: Path | str = "data/enrichment/location-hours.sources.json",
    catalog_path: Path | str = "var/catalog-import/kl-selangor-real-pilot-0.2.0-reviewed/catalog.v2.json",
    original_catalog_path: Path | str = "/Users/johnathanjohnathan/Documents/restaurant-menu-collection/catalog.real.json",
    output_manifest_path: Path | str = "var/catalog-import/kl-selangor-real-pilot-58/activation-manifest.json",
    output_catalog_path: Path | str | None = "var/catalog-import/kl-selangor-real-pilot-58/catalog.v2.json",
    verify_checksums: bool = True,
) -> dict[str, Any]:
    """Validate 58-outlet scope from patch and catalog, generating the activation manifest."""
    patch_p = Path(patch_path)
    sources_p = Path(sources_path)
    catalog_p = Path(catalog_path)
    orig_p = Path(original_catalog_path)
    out_manifest_p = Path(output_manifest_path)

    # 1. Checksum validation
    orig_sha = file_sha256(orig_p)
    patch_sha = file_sha256(patch_p)
    sources_sha = file_sha256(sources_p)
    catalog_sha = file_sha256(catalog_p)

    if verify_checksums:
        if orig_sha != EXPECTED_ORIGINAL_CATALOG_SHA:
            raise ValueError(
                f"Original catalog SHA mismatch: {orig_sha} != {EXPECTED_ORIGINAL_CATALOG_SHA}"
            )
        if patch_sha != EXPECTED_PATCH_SHA:
            raise ValueError(
                f"Location-hours patch SHA mismatch: {patch_sha} != {EXPECTED_PATCH_SHA}"
            )
        if sources_sha != EXPECTED_SOURCES_SHA:
            raise ValueError(
                f"Source manifest SHA mismatch: {sources_sha} != {EXPECTED_SOURCES_SHA}"
            )
        if catalog_sha != EXPECTED_RESOLVED_CATALOG_SHA:
            raise ValueError(
                f"Resolved catalog SHA mismatch: {catalog_sha} != {EXPECTED_RESOLVED_CATALOG_SHA}"
            )

    # 2. Derive allowlist from patch
    patch_data = json.loads(patch_p.read_text(encoding="utf-8"))
    outlet_updates = patch_data.get("outlet_updates", [])
    patch_outlet_ids = [u["outlet_id"] for u in outlet_updates]
    unique_patch_outlet_ids = set(patch_outlet_ids)

    if len(patch_outlet_ids) != 58 or len(unique_patch_outlet_ids) != 58:
        raise ValueError(
            f"Patch must contain exactly 58 unique outlet IDs, got {len(unique_patch_outlet_ids)} (total {len(patch_outlet_ids)})"
        )

    # 3. Validate against resolved catalog
    catalog = load_catalog(catalog_p, allow_synthetic=False)
    if catalog.catalog_id != "kl-selangor-real-pilot":
        raise ValueError(f"Unexpected catalog_id: {catalog.catalog_id}")
    if catalog.version != "0.2.0-reviewed":
        raise ValueError(f"Unexpected catalog version: {catalog.version}")

    catalog_outlets_by_id = {o.outlet_id: o for o in catalog.outlets}
    catalog_source_ids = {s.source_id for s in catalog.sources}

    for u in outlet_updates:
        oid = u["outlet_id"]
        if oid not in catalog_outlets_by_id:
            raise ValueError(f"Patch outlet {oid} not found in resolved catalog")

        outlet = catalog_outlets_by_id[oid]
        if outlet.latitude is None or outlet.longitude is None:
            raise ValueError(f"Outlet {oid} missing verified coordinates")

        if outlet.hours_status != "published":
            raise ValueError(
                f"Outlet {oid} hours_status is not 'published' (got {outlet.hours_status})"
            )

        if not outlet.opening_hours or len(outlet.opening_hours) < 1:
            raise ValueError(f"Outlet {oid} has no opening hour intervals")

        for sid in u.get("source_ids_to_add", []):
            if sid not in catalog_source_ids:
                raise ValueError(f"Outlet {oid} refers to missing source ID: {sid}")

    # 4. Partition outlets and items
    allowed_outlet_ids = sorted(unique_patch_outlet_ids)
    all_catalog_outlet_ids = set(catalog_outlets_by_id.keys())
    excluded_outlet_ids = sorted(all_catalog_outlet_ids - unique_patch_outlet_ids)

    if len(all_catalog_outlet_ids) != 128:
        raise ValueError(f"Expected 128 total outlets, got {len(all_catalog_outlet_ids)}")
    if len(excluded_outlet_ids) != 70:
        raise ValueError(f"Expected 70 excluded outlets, got {len(excluded_outlet_ids)}")

    reviewed_items: list[str] = []
    quarantined_items: list[dict[str, Any]] = []
    unreviewed_items_in_scope: list[str] = []
    unreviewed_items_outside_scope: list[str] = []

    for item in catalog.menu_items:
        if item.outlet_id in unique_patch_outlet_ids:
            if item.review_status == "reviewed":
                reviewed_items.append(item.item_id)
            elif item.review_status == "quarantined":
                quarantined_items.append(
                    {
                        "item_id": item.item_id,
                        "outlet_id": item.outlet_id,
                        "reasons": list(item.review_reasons),
                    }
                )
            else:
                unreviewed_items_in_scope.append(item.item_id)
        else:
            unreviewed_items_outside_scope.append(item.item_id)

    if len(reviewed_items) != 1073:
        raise ValueError(f"Expected 1073 reviewed items in 58 outlets, got {len(reviewed_items)}")
    if len(quarantined_items) != 1:
        raise ValueError(
            f"Expected exactly 1 quarantined item in 58 outlets, got {len(quarantined_items)}"
        )
    if unreviewed_items_in_scope:
        raise ValueError(
            f"Unexpected unreviewed items in 58-outlet scope: {len(unreviewed_items_in_scope)}"
        )
    if len(unreviewed_items_outside_scope) != 1364:
        raise ValueError(
            f"Expected 1364 unreviewed items outside scope, got {len(unreviewed_items_outside_scope)}"
        )

    # 5. Build manifest
    manifest_data = {
        "manifest_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "catalog_id": catalog.catalog_id,
        "catalog_version": catalog.version,
        "schema_version": catalog.schema_version,
        "input_hashes": {
            "original_catalog_sha256": orig_sha,
            "patch_sha256": patch_sha,
            "sources_sha256": sources_sha,
            "resolved_catalog_sha256": catalog_sha,
        },
        "allowed_outlet_ids": allowed_outlet_ids,
        "counts": {
            "allowed_outlets": len(allowed_outlet_ids),
            "excluded_outlets": len(excluded_outlet_ids),
            "total_outlets": len(all_catalog_outlet_ids),
            "reviewed_items": len(reviewed_items),
            "quarantined_items": len(quarantined_items),
            "unreviewed_items": len(unreviewed_items_outside_scope),
            "total_menu_items": len(catalog.menu_items),
            "target_vector_count": len(reviewed_items),
        },
        "exclusions": {
            "excluded_outlets": [
                {"outlet_id": oid, "reason": "unresolved_location_hours"}
                for oid in excluded_outlet_ids
            ],
            "quarantined_items": quarantined_items,
            "unreviewed_items_outside_scope_count": len(unreviewed_items_outside_scope),
        },
    }

    _atomic_write_json(out_manifest_p, manifest_data)

    if output_catalog_path:
        out_cat_p = Path(output_catalog_path)
        out_cat_p.parent.mkdir(parents=True, exist_ok=True)
        if out_cat_p.resolve() != catalog_p.resolve():
            out_cat_p.write_bytes(catalog_p.read_bytes())

    return manifest_data


def load_activation_manifest(manifest_path: Path | str) -> dict[str, Any]:
    p = Path(manifest_path)
    if not p.exists():
        raise FileNotFoundError(f"Manifest not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))
