"""Validate or stage the independently collected JSON catalog; never scrape."""

import argparse
import json
import os
import tempfile
from pathlib import Path

from dining.catalog import load_catalog
from dining.catalog_audit import audit_catalog, public_catalog_status, upgrade_catalog


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix="catalog-", suffix=".json", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--install",
        type=Path,
        help="Atomically write normalized validated catalog here. Restart the app afterwards.",
    )
    parser.add_argument("--allow-synthetic", action="store_true")
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="Upgrade to schema v2, preserve unknowns and quarantine textual dietary conflicts. Does not approve data.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Write an operator audit JSON report, including affected record IDs.",
    )
    args = parser.parse_args()
    if args.report and args.report.resolve() in {
        args.source.resolve(),
        args.install.resolve() if args.install else None,
    }:
        parser.error("--report must differ from the source and installation paths")
    catalog = load_catalog(args.source, allow_synthetic=args.allow_synthetic)
    if args.upgrade:
        catalog = upgrade_catalog(catalog)
    print(
        json.dumps(
            {
                "valid": True,
                **public_catalog_status(catalog),
                "outlets": len(catalog.outlets),
                "menu_items": len(catalog.menu_items),
                "sources": len(catalog.sources),
            },
            indent=2,
        )
    )
    if args.report:
        _atomic_write(args.report, json.dumps(audit_catalog(catalog), indent=2) + "\n")
    if args.install:
        _atomic_write(args.install, catalog.model_dump_json(indent=2) + "\n")
        print(
            "Catalog staged. Restart the app to load this version; prior recommendation sets must be regenerated. Loading does not approve display, embeddings or recommendations."
        )


if __name__ == "__main__":
    main()
