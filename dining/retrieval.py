"""Rights-aware catalog embedding index and hybrid candidate search."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Union

from .catalog import Catalog, MenuItem, Outlet
from .ranking import item_search_text, name_term_overlap

RETRIEVAL_POLICY_VERSION = "retrieval-policy-v1"
EMBEDDING_MODEL_NAME = "deterministic-hash-v1"
VECTOR_DIM = 64

SENSITIVE_QUERY_PATTERNS = re.compile(
    r"\b(?:allerg\w*|alahan|anaphyla\w*|halal|haram|pork|babi|kacang|peanuts?|shellfish|gluten|dairy|gps|latitude|longitude|sk-[\w-]+|password|secret|token|system\s*prompt|ignore\s+(?:previous|above)|you\s+are\s+now)\b|[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+|\d{1,3}\.\d{3,}",
    re.IGNORECASE,
)


def sanitize_retrieval_text(text: str) -> str:
    """Strip private sentinels, credentials, coordinates, emails and sensitive terms."""
    if not text:
        return ""
    cleaned = SENSITIVE_QUERY_PATTERNS.sub(" ", text)
    return " ".join(cleaned.split())


def build_embed_text(item: MenuItem, outlet: Outlet | None = None) -> str:
    """Build allowlisted restaurant and menu facts text for embedding."""
    parts = [item.name]
    if item.description:
        parts.append(item.description)
    if item.variant:
        parts.append(item.variant)
    if item.meal_role and item.meal_role != "unknown":
        parts.append(item.meal_role)
    if item.attributes:
        parts.extend(item.attributes)
    # Reviewed translations let one embedding cover English, Malay and Chinese names.
    for language in ("en", "ms", "zh"):
        parts.extend(item.name_translations.get(language, ()))
    if item.cuisine_tags:
        parts.extend(item.cuisine_tags)
    elif outlet and outlet.cuisine_tags:
        parts.extend(outlet.cuisine_tags)
    return sanitize_retrieval_text(" ".join(parts))


@dataclass(frozen=True)
class RetrievalQuery:
    query_id: str
    text: str
    top_k: int = 20

    @property
    def sanitized_text(self) -> str:
        return sanitize_retrieval_text(self.text)


class DeterministicEmbedder:
    """Lightweight, deterministic feature hashing embedder with zero external dependencies."""

    model_name = EMBEDDING_MODEL_NAME
    revision = "v1"

    def __init__(self, dim: int = VECTOR_DIM):
        self.dim = dim

    def embed_text(self, text: str) -> list[float]:
        tokens = re.findall(r"\w+", text.casefold())
        if not tokens:
            return [0.0] * self.dim

        vec = [0.0] * self.dim
        # Subword and word n-grams for multilingual resilience (English, BM, Manglish)
        features = list(tokens)
        for t in tokens:
            if len(t) >= 4:
                features.append(t[:4])
                features.append(t[-4:])

        for feat in features:
            h = int(hashlib.md5(feat.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dim
            val = 1.0 if ((h >> 8) & 1) else -1.0
            vec[idx] += val

        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            return [x / norm for x in vec]
        return vec

    def similarity(self, vec_a: list[float], vec_b: list[float]) -> float:
        dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
        return max(0.0, min(1.0, (dot + 1.0) / 2.0))


class SentenceTransformerEmbedder:
    """Production sentence-transformers embedding model wrapper."""

    def __init__(
        self,
        model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        revision: str = "main",
    ):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.revision = revision
        self.model = SentenceTransformer(model_name, revision=revision)

    def embed_text(self, text: str) -> list[float]:
        tokens = text.strip()
        if not tokens:
            dim = self.model.get_sentence_embedding_dimension() or 384
            return [0.0] * dim
        vec = self.model.encode(tokens, normalize_embeddings=True)
        return vec.tolist()

    def similarity(self, vec_a: list[float], vec_b: list[float]) -> float:
        dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
        return max(0.0, min(1.0, (dot + 1.0) / 2.0))


@dataclass(frozen=True)
class IndexedItem:
    item_id: str
    outlet_id: str
    name: str
    text: str
    vector: list[float]
    attributes: list[str]
    cuisine_tags: list[str]


@dataclass
class CatalogEmbeddingIndex:
    catalog_id: str
    catalog_version: str
    model_name: str
    index_policy_version: str
    created_at: str
    indexed_items: list[IndexedItem] = field(default_factory=list)
    _embedder: Any = field(default_factory=DeterministicEmbedder)

    @classmethod
    def build(
        cls,
        catalog: Catalog,
        embedder: Any | None = None,
        at: datetime | None = None,
    ) -> CatalogEmbeddingIndex:
        at_time = at or datetime.now(timezone.utc)
        emb = embedder or DeterministicEmbedder()

        # Step 1: Identify outlets whose sources have rights.embed == "allowed" and are not expired
        usable_outlet_ids: set[str] = set()
        for outlet in catalog.outlets:
            if catalog.evidence_usable(outlet.source_ids, purpose="embed", at=at_time):
                usable_outlet_ids.add(outlet.outlet_id)

        # Step 2: Index menu items for usable outlets where item sources also permit embedding
        indexed: list[IndexedItem] = []
        for item in catalog.menu_items:
            if item.outlet_id not in usable_outlet_ids:
                continue
            if not catalog.evidence_usable(
                item.source_ids, purpose="embed", at=at_time
            ):
                continue

            text_rep = build_embed_text(item)
            vec = emb.embed_text(text_rep)
            indexed.append(
                IndexedItem(
                    item_id=item.item_id,
                    outlet_id=item.outlet_id,
                    name=item.name,
                    text=text_rep,
                    vector=vec,
                    attributes=list(item.attributes),
                    cuisine_tags=list(item.cuisine_tags),
                )
            )

        return cls(
            catalog_id=catalog.catalog_id,
            catalog_version=catalog.version,
            model_name=getattr(emb, "model_name", EMBEDDING_MODEL_NAME),
            index_policy_version=RETRIEVAL_POLICY_VERSION,
            created_at=at_time.isoformat(),
            indexed_items=indexed,
            _embedder=emb,
        )

    def is_usable_for_catalog(self, catalog: Catalog) -> bool:
        return (
            self.catalog_id == catalog.catalog_id
            and self.catalog_version == catalog.version
            and bool(self.indexed_items)
            and self.index_policy_version == RETRIEVAL_POLICY_VERSION
        )

    def indexed_outlet_ids(self) -> set[str]:
        return {item.outlet_id for item in self.indexed_items}


@dataclass
class PersistentCatalogIndex:
    persist_dir: Path
    catalog_id: str
    catalog_version: str
    collection_name: str
    model_name: str
    index_policy_version: str
    indexed_item_count: int
    indexed_outlet_count: int
    client: Any
    collection: Any
    _embedder: Any

    @property
    def item_count(self) -> int:
        return self.indexed_item_count

    @property
    def outlet_count(self) -> int:
        return self.indexed_outlet_count

    def is_usable_for_catalog(self, catalog: Catalog) -> bool:
        return (
            self.catalog_id == catalog.catalog_id
            and self.catalog_version == catalog.version
            and self.indexed_item_count > 0
            and self.index_policy_version == RETRIEVAL_POLICY_VERSION
        )

    def indexed_outlet_ids(self) -> set[str]:
        try:
            metas = self.collection.get(include=["metadatas"])["metadatas"]
            return {m["outlet_id"] for m in metas if "outlet_id" in m}
        except Exception:
            return set()


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    descriptor, name = tempfile.mkstemp(prefix="active-idx-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_persistent_index(
    persist_dir: Path | str,
    catalog: Catalog | None = None,
    embedder: Any | None = None,
) -> PersistentCatalogIndex | None:
    """Load the active persistent Chroma vector index if present and compatible."""
    persist_p = Path(persist_dir)
    active_path = persist_p / "active_index.json"
    if not active_path.exists():
        return None

    try:
        meta = json.loads(active_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if catalog is not None:
        if (
            catalog.catalog_id != meta.get("catalog_id")
            or catalog.version != meta.get("catalog_version")
        ):
            return None

    if meta.get("index_policy_version") != RETRIEVAL_POLICY_VERSION:
        return None

    import chromadb

    try:
        client = chromadb.PersistentClient(path=str(persist_p))
        collection = client.get_collection(name=meta["collection_name"])
    except Exception:
        return None

    model_name = meta.get("embedding_model", EMBEDDING_MODEL_NAME)
    if embedder is None:
        if model_name in {EMBEDDING_MODEL_NAME, "deterministic", "fake", "deterministic-hash-v1"}:
            embedder = DeterministicEmbedder()
        else:
            embedder = SentenceTransformerEmbedder(model_name)

    return PersistentCatalogIndex(
        persist_dir=persist_p,
        catalog_id=meta["catalog_id"],
        catalog_version=meta["catalog_version"],
        collection_name=meta["collection_name"],
        model_name=model_name,
        index_policy_version=meta.get("index_policy_version", RETRIEVAL_POLICY_VERSION),
        indexed_item_count=meta.get("indexed_item_count", collection.count()),
        indexed_outlet_count=meta.get("indexed_outlet_count", 0),
        client=client,
        collection=collection,
        _embedder=embedder,
    )


def build_persistent_catalog_index(
    catalog: Catalog,
    outlet_manifest_path: Path | str,
    persist_dir: Path | str,
    model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    embedder: Any | None = None,
    rebuild: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build or rebuild a versioned persistent Chroma vector index for reviewed catalog records."""
    manifest_p = Path(outlet_manifest_path)
    if not manifest_p.exists():
        raise FileNotFoundError(f"Activation manifest not found: {manifest_p}")

    manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
    allowed_outlet_ids = set(manifest.get("allowed_outlet_ids", []))
    if len(allowed_outlet_ids) != 58:
        raise ValueError(f"Activation manifest must have exactly 58 outlets, got {len(allowed_outlet_ids)}")

    if catalog.catalog_id != manifest.get("catalog_id"):
        raise ValueError(f"Catalog ID mismatch: {catalog.catalog_id} != {manifest.get('catalog_id')}")
    if catalog.version != manifest.get("catalog_version"):
        raise ValueError(f"Catalog version mismatch: {catalog.version} != {manifest.get('catalog_version')}")

    outlets_by_id = {o.outlet_id: o for o in catalog.outlets}

    # Preflight records before loading model
    eligible_items: list[MenuItem] = []
    quarantined_items: list[MenuItem] = []
    unreviewed_items_in_scope: list[MenuItem] = []
    unreviewed_items_outside_scope: list[MenuItem] = []

    for item in catalog.menu_items:
        if item.outlet_id in allowed_outlet_ids:
            if item.review_status == "reviewed":
                eligible_items.append(item)
            elif item.review_status == "quarantined":
                quarantined_items.append(item)
            else:
                unreviewed_items_in_scope.append(item)
        else:
            unreviewed_items_outside_scope.append(item)

    if len(eligible_items) != 1073:
        raise ValueError(
            f"Preflight assertion failed: expected exactly 1073 reviewed items in scope, got {len(eligible_items)}"
        )
    if len(quarantined_items) != 1:
        raise ValueError(
            f"Preflight assertion failed: expected exactly 1 quarantined item, got {len(quarantined_items)}"
        )

    documents = []
    ids = []
    metadatas = []
    for item in eligible_items:
        outlet = outlets_by_id.get(item.outlet_id)
        doc = build_embed_text(item, outlet)
        documents.append(doc)
        ids.append(item.item_id)
        metadatas.append(
            {
                "catalog_id": catalog.catalog_id,
                "catalog_version": catalog.version,
                "outlet_id": item.outlet_id,
                "item_id": item.item_id,
                "embedding_model": model_name,
                "model_revision": getattr(embedder, "revision", "main"),
                "index_policy_version": RETRIEVAL_POLICY_VERSION,
            }
        )

    if dry_run:
        return {
            "dry_run": True,
            "catalog_id": catalog.catalog_id,
            "catalog_version": catalog.version,
            "allowed_outlets": len(allowed_outlet_ids),
            "eligible_reviewed_items": len(eligible_items),
            "quarantined_items": len(quarantined_items),
            "unreviewed_items_outside_scope": len(unreviewed_items_outside_scope),
            "target_vector_count": len(eligible_items),
        }

    # Load embedder
    if embedder is None:
        if model_name in {EMBEDDING_MODEL_NAME, "deterministic", "fake", "deterministic-hash-v1"}:
            embedder = DeterministicEmbedder()
        else:
            embedder = SentenceTransformerEmbedder(model_name)

    embeddings = [embedder.embed_text(doc) for doc in documents]

    # Initialize Chroma persistent storage
    import chromadb

    persist_p = Path(persist_dir)
    persist_p.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(persist_p))

    from uuid import uuid4

    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    clean_id = catalog.catalog_id.replace("-", "_")
    clean_ver = catalog.version.replace(".", "_").replace("-", "_")
    collection_name = f"catalog_{clean_id}_{clean_ver}_{timestamp_str}_{uuid4().hex[:6]}"

    collection = client.create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    # Insert items
    batch_size = 500
    for idx in range(0, len(ids), batch_size):
        collection.add(
            ids=ids[idx : idx + batch_size],
            embeddings=embeddings[idx : idx + batch_size],
            documents=documents[idx : idx + batch_size],
            metadatas=metadatas[idx : idx + batch_size],
        )

    count = collection.count()
    if count != 1073:
        # Build failed; clean up failed collection and abort without touching active pointer
        try:
            client.delete_collection(name=collection_name)
        except Exception:
            pass
        raise ValueError(f"Collection build validation failed: expected 1073 vectors, got {count}")

    # Determine prior collection to prune after successful atomic switch
    active_manifest_path = persist_p / "active_index.json"
    prior_collection_name: str | None = None
    if active_manifest_path.exists():
        try:
            prev_meta = json.loads(active_manifest_path.read_text(encoding="utf-8"))
            prior_collection_name = prev_meta.get("collection_name")
        except Exception:
            pass

    # Atomic pointer switch
    active_meta = {
        "active_version": "1.0",
        "collection_name": collection_name,
        "catalog_id": catalog.catalog_id,
        "catalog_version": catalog.version,
        "embedding_model": model_name,
        "model_revision": getattr(embedder, "revision", "main"),
        "index_policy_version": RETRIEVAL_POLICY_VERSION,
        "indexed_item_count": count,
        "indexed_outlet_count": len(allowed_outlet_ids),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "status": "active",
    }
    _atomic_write_json(active_manifest_path, active_meta)

    # Remove stale prior collection
    if prior_collection_name and prior_collection_name != collection_name:
        try:
            client.delete_collection(name=prior_collection_name)
        except Exception:
            pass

    return {
        "status": "ok",
        "collection_name": collection_name,
        "indexed_item_count": count,
        "indexed_outlet_count": len(allowed_outlet_ids),
        "embedding_model": model_name,
        "index_policy_version": RETRIEVAL_POLICY_VERSION,
        "active_manifest": str(active_manifest_path),
    }


def _fallback_structured_search(
    catalog: Catalog,
    queries: list[RetrievalQuery],
    maximum_outlets: int = 30,
    maximum_items_per_outlet: int = 2,
) -> dict[str, Any]:
    """Fallback to transparent structured candidate retrieval when embeddings unavailable."""
    outlet_candidates = []
    seen: set[str] = set()

    all_query_terms = set()
    for q in queries:
        all_query_terms.update(q.sanitized_text.casefold().split())

    for outlet in catalog.outlets:
        if outlet.outlet_id in seen:
            continue
        items = [i for i in catalog.menu_items if i.outlet_id == outlet.outlet_id]
        if not items:
            continue

        rep_items = [i.item_id for i in items[:maximum_items_per_outlet]]
        outlet_candidates.append(
            {
                "outlet_id": outlet.outlet_id,
                "semantic_score": 0.5,
                "recall_score": 0.5,
                "representative_item_ids": rep_items,
            }
        )
        seen.add(outlet.outlet_id)
        if len(outlet_candidates) >= maximum_outlets:
            break

    return {
        "status": "fallback",
        "fallback_reason": "index_unavailable",
        "policy_version": RETRIEVAL_POLICY_VERSION,
        "candidates": outlet_candidates,
    }


def semantic_candidate_search(
    index: CatalogEmbeddingIndex | PersistentCatalogIndex | None,
    catalog: Catalog,
    queries: list[RetrievalQuery],
    maximum_outlets: int = 30,
    maximum_items_per_outlet: int = 2,
    outlet_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Retrieve unique outlet candidates through hybrid semantic and structured matching.

    ``outlet_ids`` restricts candidates (e.g. to the meal's search radius) *before* the
    top-N cut, so nearby outlets are never crowded out by distant strong matches.
    """
    if index is None or not index.is_usable_for_catalog(catalog):
        return _fallback_structured_search(
            catalog, queries, maximum_outlets, maximum_items_per_outlet
        )

    embedder = index._embedder
    outlet_similarities: dict[str, list[tuple[float, str, str, set[str], set[str]]]] = {}
    catalog_items_by_id = {item.item_id: item for item in catalog.menu_items}

    if isinstance(index, PersistentCatalogIndex):
        for q in queries:
            query_text = q.sanitized_text
            if not query_text:
                continue
            q_vec = embedder.embed_text(query_text)
            q_terms = set(query_text.casefold().split())

            # A bounded area needs every in-area item scored, not a global top 100.
            query_top = (
                index.indexed_item_count
                if outlet_ids is not None
                else min(100, index.indexed_item_count)
            )
            chroma_res = index.collection.query(query_embeddings=[q_vec], n_results=query_top)

            if not chroma_res or not chroma_res.get("ids") or not chroma_res["ids"][0]:
                continue

            ids = chroma_res["ids"][0]
            distances = chroma_res["distances"][0] if chroma_res.get("distances") else [0.0] * len(ids)
            metas = chroma_res["metadatas"][0] if chroma_res.get("metadatas") else [{}] * len(ids)

            for item_id, dist, meta in zip(ids, distances, metas, strict=False):
                # In Chroma with cosine distance d = 1 - cos, cos = 1 - d
                sim = max(0.0, min(1.0, 1.0 - dist / 2.0))
                outlet_id = meta.get("outlet_id", "")
                cat_item = catalog_items_by_id.get(item_id)
                item_name = cat_item.name.casefold().strip() if cat_item else item_id
                attrs = set(cat_item.attributes) if cat_item else set()

                if outlet_id not in outlet_similarities:
                    outlet_similarities[outlet_id] = []
                outlet_similarities[outlet_id].append(
                    (sim, item_id, item_name, attrs, q_terms)
                )

    else:
        for q in queries:
            query_text = q.sanitized_text
            if not query_text:
                continue
            q_vec = embedder.embed_text(query_text)
            q_terms = set(query_text.casefold().split())

            for item in index.indexed_items:
                sim = embedder.similarity(q_vec, item.vector)
                if item.outlet_id not in outlet_similarities:
                    outlet_similarities[item.outlet_id] = []
                outlet_similarities[item.outlet_id].append(
                    (
                        sim,
                        item.item_id,
                        item.name.casefold().strip(),
                        set(item.attributes),
                        q_terms,
                    )
                )

    if outlet_ids is not None:
        outlet_similarities = {
            oid: recs for oid, recs in outlet_similarities.items() if oid in outlet_ids
        }

    if not outlet_similarities:
        return _fallback_structured_search(
            catalog, queries, maximum_outlets, maximum_items_per_outlet
        )

    outlet_scores: list[dict[str, Any]] = []

    for outlet_id, item_records in outlet_similarities.items():
        # Deduplicate identical menu items per outlet so duplicate records cannot inflate score
        distinct_by_name: dict[str, tuple[float, str, str, set[str], set[str]]] = {}
        for rec in item_records:
            name_key = rec[2]
            if (
                name_key not in distinct_by_name
                or rec[0] > distinct_by_name[name_key][0]
            ):
                distinct_by_name[name_key] = rec

        distinct_records = list(distinct_by_name.values())
        distinct_records.sort(key=lambda x: -x[0])

        # Enforce per-outlet aggregation rule: mean of top 2 distinct menu-item similarities
        top_sims = [rec[0] for rec in distinct_records[:2]]
        semantic_score = sum(top_sims) / len(top_sims) if top_sims else 0.0

        # Structured tag match and keyword match
        best_tags = distinct_records[0][3]
        query_terms = distinct_records[0][4]
        tag_match = (
            len(best_tags & query_terms) / max(1, len(query_terms))
            if query_terms
            else 0.0
        )
        tag_match = min(1.0, tag_match)

        # Whole-word dish-name match: the multilingual embedder is weak on local dish
        # names (laksa, nasi lemak), so an exact menu word is strong evidence.
        name_match = max(
            (
                name_term_overlap(
                    " ".join(sorted(rec[4])),
                    item_search_text(catalog_items_by_id[rec[1]])
                    if rec[1] in catalog_items_by_id
                    else rec[2],
                )
                for rec in distinct_records
            ),
            default=0.0,
        )

        # Hybrid fusion formula
        recall_score = (
            0.40 * semantic_score
            + 0.20 * tag_match
            + 0.30 * name_match
            + 0.10 * min(1.0, semantic_score)
        )

        # Select unique representative item IDs (capped at maximum_items_per_outlet)
        rep_ids: list[str] = []
        for rec in distinct_records:
            if rec[1] not in rep_ids:
                rep_ids.append(rec[1])
            if len(rep_ids) >= maximum_items_per_outlet:
                break

        outlet_scores.append(
            {
                "outlet_id": outlet_id,
                "semantic_score": round(semantic_score, 4),
                "recall_score": round(recall_score, 4),
                "representative_item_ids": rep_ids,
            }
        )

    # Sort candidates by recall score descending, limit to maximum_outlets
    outlet_scores.sort(key=lambda c: (-c["recall_score"], c["outlet_id"]))
    selected_candidates = outlet_scores[:maximum_outlets]

    return {
        "status": "ok",
        "policy_version": RETRIEVAL_POLICY_VERSION,
        "model_name": index.model_name,
        "candidates": selected_candidates,
    }
