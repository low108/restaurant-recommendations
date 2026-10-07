#!/usr/bin/env python3
"""Build or rebuild a versioned persistent Chroma vector index for reviewed catalog records.

Usage:
    .venv/bin/python scripts/build_catalog_vector_index.py \
        --catalog var/catalog-import/kl-selangor-real-pilot-58/catalog.validated.json \
        --outlet-manifest var/catalog-import/kl-selangor-real-pilot-58/activation-manifest.json \
        --persist-dir var/vector/catalog \
        --model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
        --rebuild
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dining.catalog import load_catalog
from dining.retrieval import (
    DeterministicEmbedder,
    SentenceTransformerEmbedder,
    build_persistent_catalog_index,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        type=Path,
        required=True,
        help="Path to validated catalog.v2.json or catalog.validated.json",
    )
    parser.add_argument(
        "--outlet-manifest",
        type=Path,
        required=True,
        help="Path to activation-manifest.json containing allowed outlet IDs",
    )
    parser.add_argument(
        "--persist-dir",
        type=Path,
        default=Path("var/vector/catalog"),
        help="Directory for ChromaDB persistence",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        help="Embedding model name or identifier",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Force rebuild and prune old collection for this catalog version",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preflight records without loading model or writing vectors",
    )
    args = parser.parse_args()

    catalog = load_catalog(args.catalog, allow_synthetic=False)

    # Embedder selection
    embedder = None
    if not args.dry_run:
        if args.model in {"deterministic-hash-v1", "deterministic", "fake"}:
            embedder = DeterministicEmbedder()
        else:
            embedder = SentenceTransformerEmbedder(args.model)

    result = build_persistent_catalog_index(
        catalog=catalog,
        outlet_manifest_path=args.outlet_manifest,
        persist_dir=args.persist_dir,
        model_name=args.model,
        embedder=embedder,
        rebuild=args.rebuild,
        dry_run=args.dry_run,
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
