#!/usr/bin/env python3
"""Reviewable dish-name translations for the 58 verified outlets.

English, Malay and Chinese names and common dish words are stored as extra searchable data
next to each dish (``name_translations``). The original name, variant and description are
never changed, and translations never decide eligibility. A translation only changes the
catalog after a human approves it. The source catalog is never modified: ``apply`` writes a
new catalog version.

Workflow:
    # 1. Write a draft patch (one entry per in-scope reviewed item, with read-only context)
    .venv/bin/python scripts/dish_translations.py export \
        --catalog var/catalog-import/kl-selangor-real-pilot-58-tagged/catalog.validated.json \
        --outlet-manifest var/catalog-import/kl-selangor-real-pilot-58-tagged/activation-manifest.json

    # 2. Fill translations / evidence / confidence, then validate and report coverage
    .venv/bin/python scripts/dish_translations.py validate --catalog ... --outlet-manifest ...
    .venv/bin/python scripts/dish_translations.py stats

    # 3. Apply approved entries to a NEW catalog file
    .venv/bin/python scripts/dish_translations.py apply --catalog ... --outlet-manifest ... \
        --version 0.4.0-translated \
        --out var/catalog-import/kl-selangor-real-pilot-58-translated/catalog.validated.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dining.catalog.models import Catalog

PATCH_VERSION = "1"
LANGUAGES = ("en", "ms", "zh")
MAX_TERM_CHARS = 60
MAX_TERMS_PER_LANGUAGE = 6
EVIDENCE_KINDS = frozenset({"name", "variant", "description", "menu_code"})
CONFIDENCE = frozenset({"high", "medium", "low"})
REVIEW_STATUSES = frozenset({"needs_review", "approved", "rejected"})
# Requirements are verified facts with their own evidence workflow, never search words.
FORBIDDEN_WORDS = re.compile(
    r"\b(halal|haram|non[- ]?halal|vegetarian|vegan|no[- ]pork|pork[- ]free|gluten[- ]free|"
    r"nut[- ]free|dairy[- ]free|allergen|allergy|kosher|healthy|diet)\b|素食|纯素|清真|无猪|不含猪",
    re.IGNORECASE,
)
URL_LIKE = re.compile(r"https?://|www\.|\.com\b|@")
PROSE_PUNCTUATION = re.compile(r"[;:!?]|\.(\s|$)")


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
        entries.append(
            {
                "item_id": item["item_id"],
                "translations": {
                    lang: list(item.get("name_translations", {}).get(lang, []))
                    for lang in LANGUAGES
                },
                "evidence": [],
                "confidence": "low",
                "notes": "",
                "review": {"status": "needs_review", "reviewer": None},
                "_context": {
                    "outlet": outlets[item["outlet_id"]]["name"],
                    "name": item["name"],
                    "variant": item.get("variant", ""),
                    "description": item.get("description", ""),
                },
            }
        )
    patch = {
        "patch_version": PATCH_VERSION,
        "languages": list(LANGUAGES),
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


def check_term(term, where: str, language: str) -> list[str]:
    if not isinstance(term, str) or not term.strip():
        return [f"{where}: empty or non-string {language} term"]
    problems = []
    if term != term.strip():
        problems.append(f"{where}: {language} term {term!r} has surrounding whitespace")
    if len(term) > MAX_TERM_CHARS:
        problems.append(f"{where}: {language} term longer than {MAX_TERM_CHARS} characters")
    if URL_LIKE.search(term):
        problems.append(f"{where}: {language} term {term!r} looks like a URL or address")
    if PROSE_PUNCTUATION.search(term):
        problems.append(f"{where}: {language} term {term!r} looks like prose")
    if FORBIDDEN_WORDS.search(term):
        problems.append(
            f"{where}: {language} term {term!r} is a requirement or diet claim, not a dish word"
        )
    return problems


def validate_patch(patch: dict, raw: dict, allowed: set[str], catalog_sha: str):
    """Return (errors, warnings). Errors block apply; warnings need a reviewer's eye."""
    errors, warnings = [], []
    if patch.get("patch_version") != PATCH_VERSION:
        errors.append(f"patch_version must be {PATCH_VERSION!r}")
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
                f"{where}: item is {item.get('review_status')!r}; only reviewed items may be translated"
            )
        status = entry.get("review", {}).get("status")
        if status not in REVIEW_STATUSES:
            errors.append(
                f"{where}: review.status must be one of {sorted(REVIEW_STATUSES)}"
            )
        translations = entry.get("translations", {})
        unknown = set(translations) - set(LANGUAGES)
        if unknown:
            errors.append(f"{where}: languages {sorted(unknown)} not allowed; use {list(LANGUAGES)}")
        total = 0
        for language in LANGUAGES:
            terms = translations.get(language, [])
            if not isinstance(terms, list):
                errors.append(f"{where}: {language} must be a list")
                continue
            if len(terms) > MAX_TERMS_PER_LANGUAGE:
                errors.append(
                    f"{where}: more than {MAX_TERMS_PER_LANGUAGE} {language} terms"
                )
            folded = [t.casefold() for t in terms if isinstance(t, str)]
            if len(folded) != len(set(folded)):
                errors.append(f"{where}: repeated {language} term")
            for term in terms:
                errors.extend(check_term(term, where, language))
            total += len(terms)
        evidence = set(entry.get("evidence", []))
        if total and not evidence:
            errors.append(
                f"{where}: translations need evidence from {sorted(EVIDENCE_KINDS)}"
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
        if not total and not entry.get("notes"):
            warnings.append(f"{where}: left empty without a reason in notes")
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
    confidence = Counter(e["confidence"] for e in entries)
    per_language = {
        lang: sum(1 for e in entries if e["translations"].get(lang)) for lang in LANGUAGES
    }
    empty = [e for e in entries if not any(e["translations"].get(l) for l in LANGUAGES)]
    by_outlet: dict[str, Counter] = {}
    for e in entries:
        counter = by_outlet.setdefault(e["_context"]["outlet"], Counter())
        counter["items"] += 1
        counter["empty"] += not any(e["translations"].get(l) for l in LANGUAGES)
        counter["low"] += e["confidence"] == "low"
    print(f"Entries: {len(entries)}  review: {dict(status)}  confidence: {dict(confidence)}")
    print(f"Items with >=1 term per language: {per_language}")
    print(f"Items left empty: {len(empty)}")
    for outlet, counter in sorted(by_outlet.items()):
        if counter["empty"] or counter["low"]:
            print(
                f"  {outlet}: {counter['items']} items, "
                f"{counter['empty']} empty, {counter['low']} low"
            )
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
        translations = {
            lang: list(entry["translations"].get(lang, []))
            for lang in LANGUAGES
            if entry["translations"].get(lang)
        }
        changed += translations != item.get("name_translations", {})
        item["name_translations"] = translations
    raw["version"] = args.version
    raw["generated_at"] = datetime.now(timezone.utc).isoformat()
    Catalog.model_validate(raw)  # Fail before writing anything invalid.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    receipt = {
        "applied_at": raw["generated_at"],
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
    receipt_path = args.out.with_name("dish-translations.apply-receipt.json")
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(
        f"Applied {len(approved)} approved entries ({changed} items changed) -> {args.out}"
    )
    print(
        "Embedded text includes translations: rebuild the vector index next "
        "(--dry-run first; --rebuild switches the live index):"
    )
    print(
        "  .venv/bin/python scripts/build_catalog_vector_index.py "
        f"--catalog {args.out} --outlet-manifest {args.outlet_manifest} --dry-run"
    )
    return 0


DEFAULT_PATCH = Path("data/enrichment/dish-translations.patch.json")
DEFAULT_CATALOG = Path(
    "var/catalog-import/kl-selangor-real-pilot-58-tagged/catalog.validated.json"
)
DEFAULT_MANIFEST = Path(
    "var/catalog-import/kl-selangor-real-pilot-58-tagged/activation-manifest.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, patch=True, catalog=True):
        if catalog:
            p.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
            p.add_argument("--outlet-manifest", type=Path, default=DEFAULT_MANIFEST)
        if patch:
            p.add_argument("--patch", type=Path, default=DEFAULT_PATCH)

    p = sub.add_parser("export", help="Write a draft patch for every in-scope reviewed item")
    common(p, patch=False)
    p.add_argument("--out", type=Path, default=DEFAULT_PATCH)
    p.add_argument("--force", action="store_true", help="Overwrite an existing draft")
    p.set_defaults(func=export)

    p = sub.add_parser("validate", help="Check a patch against the catalog")
    common(p)
    p.add_argument("--max-lines", type=int, default=200)
    p.set_defaults(func=validate)

    p = sub.add_parser("stats", help="Coverage report for a patch")
    common(p, catalog=False)
    p.set_defaults(func=stats)

    p = sub.add_parser("apply", help="Write a new catalog version with approved entries")
    common(p)
    p.add_argument("--version", required=True, help="e.g. 0.4.0-translated")
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(func=apply)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
