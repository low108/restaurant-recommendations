#!/usr/bin/env python3
"""Reviewable taste-tag and meal-role enrichment for the 58 verified outlets.

Tags come only from the existing ranking ontology (dining/recommendation/ranking.py, ``dining-tags-v1``).
Every proposed tag must cite where on the menu it came from. A tag only changes the
catalog after a human approves it. The source catalog is never modified: ``apply``
writes a new catalog version.

Workflow:
    # 1. Write a draft patch (one entry per in-scope reviewed item, with read-only context)
    .venv/bin/python scripts/taste_tags.py export \
        --catalog var/catalog-import/kl-selangor-real-pilot-58/catalog.validated.json \
        --outlet-manifest var/catalog-import/kl-selangor-real-pilot-58/activation-manifest.json \
        --out data/enrichment/taste-tags.patch.json

    # 2. Fill meal_role / attributes / evidence / confidence; set review.status
    .venv/bin/python scripts/taste_tags.py validate --patch data/enrichment/taste-tags.patch.json \
        --catalog var/catalog-import/kl-selangor-real-pilot-58/catalog.validated.json \
        --outlet-manifest var/catalog-import/kl-selangor-real-pilot-58/activation-manifest.json

    # 3. Coverage report
    .venv/bin/python scripts/taste_tags.py stats --patch data/enrichment/taste-tags.patch.json

    # 4. Apply approved entries to a NEW catalog file
    .venv/bin/python scripts/taste_tags.py apply \
        --catalog var/catalog-import/kl-selangor-real-pilot-58/catalog.validated.json \
        --outlet-manifest var/catalog-import/kl-selangor-real-pilot-58/activation-manifest.json \
        --patch data/enrichment/taste-tags.patch.json \
        --version 0.3.0-tagged \
        --out var/catalog-import/kl-selangor-real-pilot-58-tagged/catalog.validated.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dining.catalog.models import Catalog, MenuItem
from dining.recommendation.ranking import DIMENSIONS, ONTOLOGY_VERSION

PATCH_VERSION = "1"
ALLOWED_TAGS = frozenset().union(*DIMENSIONS.values())
SINGLE_VALUE_DIMENSIONS = ("spice", "portion")
MEAL_ROLES = frozenset(MenuItem.model_fields["meal_role"].annotation.__args__)
NON_MEAL_ROLES = frozenset({"side", "dessert", "beverage", "add_on"})
EVIDENCE_KINDS = frozenset({"name", "variant", "description", "menu_code"})
CONFIDENCE = frozenset({"high", "medium", "low"})
REVIEW_STATUSES = frozenset({"needs_review", "approved", "rejected"})
# Requirements are verified facts with their own evidence workflow, never taste tags.
FORBIDDEN_TAGS = frozenset(
    {
        "vegetarian",
        "vegan",
        "halal",
        "pork_free",
        "no_pork",
        "gluten_free",
        "nut_free",
        "dairy_free",
        "allergen_free",
        "healthy",
        "low_sugar",
    }
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def allowed_outlets(manifest_path: Path) -> set[str]:
    return set(load_json(manifest_path)["allowed_outlet_ids"])


def in_scope_items(raw_catalog: dict, allowed: set[str]) -> list[dict]:
    return [
        item
        for item in raw_catalog["menu_items"]
        if item["outlet_id"] in allowed and item.get("review_status") == "reviewed"
    ]


def tag_dimensions(tag: str) -> list[str]:
    return [name for name, tags in DIMENSIONS.items() if tag in tags]


def export(args) -> int:
    raw = load_json(args.catalog)
    allowed = allowed_outlets(args.outlet_manifest)
    outlets = {o["outlet_id"]: o for o in raw["outlets"]}
    if args.out.exists() and not args.force:
        print(f"Refusing to overwrite {args.out} (use --force)", file=sys.stderr)
        return 2
    entries = []
    for item in sorted(
        in_scope_items(raw, allowed), key=lambda i: (i["outlet_id"], i["item_id"])
    ):
        outlet = outlets[item["outlet_id"]]
        price = item.get("price") or {}
        entries.append(
            {
                "item_id": item["item_id"],
                "meal_role": item.get("meal_role", "unknown"),
                "attributes": list(item.get("attributes", [])),
                "evidence": [],
                "confidence": "low",
                "proposed_new_tags": [],
                "notes": "",
                "review": {"status": "needs_review", "reviewer": None},
                "_context": {
                    "outlet": outlet["name"],
                    "outlet_cuisines": outlet.get("cuisine_tags", []),
                    "name": item["name"],
                    "variant": item.get("variant", ""),
                    "description": item.get("description", ""),
                    "item_cuisines": item.get("cuisine_tags", []),
                    "listed_price_rm": (
                        price["amount_minor"] / 100
                        if price.get("amount_minor") is not None
                        else None
                    ),
                },
            }
        )
    patch = {
        "patch_version": PATCH_VERSION,
        "ontology_version": ONTOLOGY_VERSION,
        "allowed_tags": {k: sorted(v) for k, v in DIMENSIONS.items()},
        "input": {
            "catalog_id": raw["catalog_id"],
            "catalog_version": raw["version"],
            "sha256": sha256(args.catalog),
            "outlet_manifest_sha256": sha256(args.outlet_manifest),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "item_updates": entries,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(patch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Exported {len(entries)} in-scope reviewed items to {args.out}")
    return 0


def validate_patch(patch: dict, raw: dict, allowed: set[str], catalog_sha: str):
    """Return (errors, warnings). Errors block apply; warnings need a reviewer's eye."""
    errors, warnings = [], []
    if patch.get("patch_version") != PATCH_VERSION:
        errors.append(f"patch_version must be {PATCH_VERSION!r}")
    if patch.get("ontology_version") != ONTOLOGY_VERSION:
        errors.append(
            f"ontology_version {patch.get('ontology_version')!r} != {ONTOLOGY_VERSION!r}"
        )
    if patch["input"]["sha256"] != catalog_sha:
        errors.append("input catalog sha256 does not match --catalog")
    items = {item["item_id"]: item for item in raw["menu_items"]}
    seen = set()
    for n, entry in enumerate(patch["item_updates"]):
        iid = entry.get("item_id")
        where = f"[{n}] {iid}"
        if iid in seen:
            errors.append(f"{where}: duplicate entry")
        seen.add(iid)
        item = items.get(iid)
        if item is None:
            errors.append(f"{where}: unknown item_id")
            continue
        if item["outlet_id"] not in allowed:
            errors.append(f"{where}: outlet outside the 58-outlet activation scope")
        if item.get("review_status") != "reviewed":
            errors.append(
                f"{where}: item is {item.get('review_status')!r}; only reviewed items may be tagged"
            )
        status = entry.get("review", {}).get("status")
        if status not in REVIEW_STATUSES:
            errors.append(
                f"{where}: review.status must be one of {sorted(REVIEW_STATUSES)}"
            )
        role = entry.get("meal_role")
        if role not in MEAL_ROLES:
            errors.append(f"{where}: meal_role {role!r} not in {sorted(MEAL_ROLES)}")
        tags = entry.get("attributes", [])
        if len(tags) != len(set(tags)):
            errors.append(f"{where}: repeated attribute")
        for tag in tags:
            if tag in FORBIDDEN_TAGS:
                errors.append(
                    f"{where}: {tag!r} is a requirement claim, not a taste tag"
                )
            elif tag not in ALLOWED_TAGS:
                errors.append(
                    f"{where}: {tag!r} is not in {ONTOLOGY_VERSION}; put it in proposed_new_tags"
                )
        for dimension in SINGLE_VALUE_DIMENSIONS:
            # "light" is both a flavour and a portion, so it never conflicts on its own.
            values = [t for t in tags if t in DIMENSIONS[dimension] and t != "light"]
            if len(values) > 1:
                errors.append(f"{where}: more than one {dimension} tag {values}")
        changed = role != item.get("meal_role", "unknown") or set(tags) != set(
            item.get("attributes", [])
        )
        evidence = set(entry.get("evidence", []))
        if changed and not evidence:
            errors.append(
                f"{where}: changed tags need evidence from {sorted(EVIDENCE_KINDS)}"
            )
        if evidence - EVIDENCE_KINDS:
            errors.append(
                f"{where}: evidence kinds {sorted(evidence - EVIDENCE_KINDS)} not allowed"
            )
        confidence = entry.get("confidence")
        if confidence not in CONFIDENCE:
            errors.append(f"{where}: confidence must be one of {sorted(CONFIDENCE)}")
        if status == "approved" and confidence == "low":
            errors.append(f"{where}: low-confidence entries cannot be approved")
        if status == "approved" and not entry.get("review", {}).get("reviewer"):
            errors.append(f"{where}: approved entries need review.reviewer")
        if role in NON_MEAL_ROLES and set(tags) & DIMENSIONS["dish"]:
            warnings.append(
                f"{where}: {role} carries dish tags {sorted(set(tags) & DIMENSIONS['dish'])}"
            )
        if role in {"main", "set"} and not tags:
            warnings.append(f"{where}: main/set item has no taste tags")
    missing = {i["item_id"] for i in in_scope_items(raw, allowed)} - seen
    if missing:
        warnings.append(f"{len(missing)} in-scope reviewed items have no patch entry")
    return errors, warnings


def validate(args) -> int:
    patch = load_json(args.patch)
    raw = load_json(args.catalog)
    errors, warnings = validate_patch(
        patch, raw, allowed_outlets(args.outlet_manifest), sha256(args.catalog)
    )
    for line in warnings[: args.max_lines]:
        print(f"WARN  {line}")
    for line in errors[: args.max_lines]:
        print(f"ERROR {line}")
    print(f"{len(errors)} errors, {len(warnings)} warnings")
    return 1 if errors else 0


def stats(args) -> int:
    patch = load_json(args.patch)
    entries = patch["item_updates"]
    status = Counter(e["review"]["status"] for e in entries)
    roles = Counter(e["meal_role"] for e in entries)
    tagged = sum(1 for e in entries if e["attributes"])
    dims = Counter(
        d for e in entries for t in e["attributes"] for d in tag_dimensions(t)
    )
    proposed = Counter(t for e in entries for t in e.get("proposed_new_tags", []))
    by_outlet = Counter(
        e["_context"]["outlet"] for e in entries if e["meal_role"] == "unknown"
    )
    print(f"Entries: {len(entries)}  review: {dict(status)}")
    print(f"meal_role: {dict(roles)}")
    print(
        f"Items with >=1 taste tag: {tagged}/{len(entries)}  by dimension: {dict(dims)}"
    )
    print(f"Top proposed ontology additions: {proposed.most_common(15)}")
    print(f"Outlets with meal_role still unknown: {by_outlet.most_common(10)}")
    return 0


def apply(args) -> int:
    patch = load_json(args.patch)
    raw = load_json(args.catalog)
    catalog_sha = sha256(args.catalog)
    errors, _ = validate_patch(
        patch, raw, allowed_outlets(args.outlet_manifest), catalog_sha
    )
    if errors:
        print(f"Patch has {len(errors)} errors; run validate first.", file=sys.stderr)
        return 1
    if args.out.exists():
        print(f"Refusing to overwrite existing {args.out}", file=sys.stderr)
        return 2
    if args.version == raw["version"]:
        print("--version must differ from the input catalog version", file=sys.stderr)
        return 2
    approved = {
        e["item_id"]: e
        for e in patch["item_updates"]
        if e["review"]["status"] == "approved"
    }
    changed = 0
    for item in raw["menu_items"]:
        entry = approved.get(item["item_id"])
        if entry is None:
            continue
        before = (item.get("meal_role", "unknown"), tuple(item.get("attributes", [])))
        item["meal_role"] = entry["meal_role"]
        item["attributes"] = sorted(entry["attributes"])
        changed += before != (item["meal_role"], tuple(item["attributes"]))
    raw["version"] = args.version
    raw["generated_at"] = datetime.now(timezone.utc).isoformat()
    Catalog.model_validate(raw)  # Fail before writing anything invalid.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    receipt = {
        "applied_at": raw["generated_at"],
        "ontology_version": ONTOLOGY_VERSION,
        "input_catalog": {"path": str(args.catalog), "sha256": catalog_sha},
        "patch": {"path": str(args.patch), "sha256": sha256(args.patch)},
        "output_catalog": {
            "path": str(args.out),
            "sha256": sha256(args.out),
            "version": args.version,
        },
        "approved_entries": len(approved),
        "items_changed": changed,
    }
    receipt_path = args.out.with_name("taste-tags.apply-receipt.json")
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(
        f"Applied {len(approved)} approved entries ({changed} items changed) -> {args.out}"
    )
    print(
        "Embedded text includes meal_role and attributes: rebuild the vector index next:"
    )
    print(
        "  .venv/bin/python scripts/build_catalog_vector_index.py "
        f"--catalog {args.out} --outlet-manifest {args.outlet_manifest} --rebuild"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "export", help="Write a draft patch for every in-scope reviewed item"
    )
    p.add_argument("--catalog", type=Path, required=True)
    p.add_argument("--outlet-manifest", type=Path, required=True)
    p.add_argument(
        "--out", type=Path, default=Path("data/enrichment/taste-tags.patch.json")
    )
    p.add_argument("--force", action="store_true", help="Overwrite an existing draft")
    p.set_defaults(func=export)

    p = sub.add_parser(
        "validate", help="Check a patch against the ontology and catalog"
    )
    p.add_argument(
        "--patch", type=Path, default=Path("data/enrichment/taste-tags.patch.json")
    )
    p.add_argument("--catalog", type=Path, required=True)
    p.add_argument("--outlet-manifest", type=Path, required=True)
    p.add_argument("--max-lines", type=int, default=200)
    p.set_defaults(func=validate)

    p = sub.add_parser("stats", help="Coverage report for a patch")
    p.add_argument(
        "--patch", type=Path, default=Path("data/enrichment/taste-tags.patch.json")
    )
    p.set_defaults(func=stats)

    p = sub.add_parser(
        "apply", help="Write a new catalog version with approved entries"
    )
    p.add_argument("--catalog", type=Path, required=True)
    p.add_argument("--outlet-manifest", type=Path, required=True)
    p.add_argument(
        "--patch", type=Path, default=Path("data/enrichment/taste-tags.patch.json")
    )
    p.add_argument(
        "--version", required=True, help="New catalog version, e.g. 0.3.0-tagged"
    )
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(func=apply)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
