"""Rights-aware menu embedding index and hybrid candidate search.

Search finds *candidates*; it never decides eligibility. Pieces:

* **Text** – :func:`build_embed_text` turns a dish into allowlisted text (name, variant,
  role, tags, translations, cuisine); :func:`sanitize_retrieval_text` strips private or
  sensitive words from both dish text and queries.
* **Embedders** – :class:`DeterministicEmbedder` (hashing, no dependencies, for tests) and
  :class:`SentenceTransformerEmbedder` (production multilingual model), built by
  :func:`create_embedder`.
* **Indexes** – :class:`CatalogEmbeddingIndex` (in memory) and
  :class:`PersistentCatalogIndex` (Chroma on disk). Both answer the same question through
  :meth:`item_hits`: how similar is each indexed dish to a query?
* **Search** – :func:`semantic_candidate_search` groups dish hits per outlet and fuses
  semantic similarity, tag overlap and dish-name word matches into a ``recall_score``.
"""

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
from typing import Any, NamedTuple

from dining.catalog.models import Catalog, MenuItem, Outlet
from dining.core.constants import LANGUAGES, RETRIEVAL_POLICY_VERSION
from dining.recommendation.ranking import item_search_text, name_term_overlap

EMBEDDING_MODEL_NAME = "deterministic-hash-v1"
PRODUCTION_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DETERMINISTIC_MODEL_NAMES = frozenset(
    {EMBEDDING_MODEL_NAME, "deterministic", "fake", "deterministic-hash-v1"}
)
VECTOR_DIM = 64
# The activated production scope; a build refuses anything else (see the 58-outlet manifest).
EXPECTED_OUTLETS = 58
EXPECTED_VECTORS = 1073
EXPECTED_QUARANTINED = 1
CHROMA_BATCH_SIZE = 500
# Recall score fusion weights (semantic similarity counts twice: weighted and capped).
SEMANTIC_WEIGHT, TAG_WEIGHT, NAME_WEIGHT, SEMANTIC_CAP_WEIGHT = 0.40, 0.20, 0.30, 0.10

SENSITIVE_QUERY_PATTERNS = re.compile(
    r"\b(?:allerg\w*|alahan|anaphyla\w*|halal|haram|pork|babi|kacang|peanuts?|shellfish|gluten|dairy|gps|latitude|longitude|sk-[\w-]+|password|secret|token|system\s*prompt|ignore\s+(?:previous|above)|you\s+are\s+now)\b|[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+|\d{1,3}\.\d{3,}",
    re.IGNORECASE,
)


# ================================================================================ text
def sanitize_retrieval_text(text: str) -> str:
    """Strip private sentinels, credentials, coordinates, emails and sensitive terms.

    Requirements such as allergies or halal are never sent to the search model; they are
    handled by the hard checks.
    """
    if not text:
        return ""
    return " ".join(SENSITIVE_QUERY_PATTERNS.sub(" ", text).split())


def build_embed_text(item: MenuItem, outlet: Outlet | None = None) -> str:
    """Allowlisted restaurant and menu facts for one dish, ready for embedding."""
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
    for language in LANGUAGES:
        parts.extend(item.name_translations.get(language, ()))
    if item.cuisine_tags:
        parts.extend(item.cuisine_tags)
    elif outlet and outlet.cuisine_tags:
        parts.extend(outlet.cuisine_tags)
    return sanitize_retrieval_text(" ".join(parts))


@dataclass(frozen=True)
class RetrievalQuery:
    """One search query (usually one diner's craving or cuisines)."""

    query_id: str
    text: str
    top_k: int = 20

    @property
    def sanitized_text(self) -> str:
        return sanitize_retrieval_text(self.text)


# =========================================================================== embedders
class BaseEmbedder:
    """Shared behaviour: similarity of two unit vectors, mapped from [-1, 1] to [0, 1]."""

    model_name: str
    revision: str

    def embed_text(self, text: str) -> list[float]:  # pragma: no cover - interface
        raise NotImplementedError

    def similarity(self, vec_a: list[float], vec_b: list[float]) -> float:
        dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
        return max(0.0, min(1.0, (dot + 1.0) / 2.0))


class DeterministicEmbedder(BaseEmbedder):
    """Lightweight, deterministic feature-hashing embedder with no external dependencies."""

    model_name = EMBEDDING_MODEL_NAME
    revision = "v1"

    def __init__(self, dim: int = VECTOR_DIM):
        self.dim = dim

    def embed_text(self, text: str) -> list[float]:
        tokens = re.findall(r"\w+", text.casefold())
        if not tokens:
            return [0.0] * self.dim
        # Words plus 4-character prefixes/suffixes for resilience across English,
        # Malay and Manglish spellings.
        features = list(tokens)
        for token in tokens:
            if len(token) >= 4:
                features.extend((token[:4], token[-4:]))
        vector = [0.0] * self.dim
        for feature in features:
            digest = int(hashlib.md5(feature.encode("utf-8")).hexdigest(), 16)
            vector[digest % self.dim] += 1.0 if (digest >> 8) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in vector))
        return [x / norm for x in vector] if norm > 0 else vector


class SentenceTransformerEmbedder(BaseEmbedder):
    """Production sentence-transformers model (loaded lazily on construction)."""

    def __init__(self, model_name: str = PRODUCTION_MODEL_NAME, revision: str = "main"):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.revision = revision
        self.model = SentenceTransformer(model_name, revision=revision)

    def embed_text(self, text: str) -> list[float]:
        tokens = text.strip()
        if not tokens:
            return [0.0] * (self.model.get_sentence_embedding_dimension() or 384)
        return self.model.encode(tokens, normalize_embeddings=True).tolist()


def create_embedder(model_name: str) -> BaseEmbedder:
    """Factory: the deterministic embedder for test names, else the real model."""
    if model_name in DETERMINISTIC_MODEL_NAMES:
        return DeterministicEmbedder()
    return SentenceTransformerEmbedder(model_name)


# ============================================================================= indexes
class ItemHit(NamedTuple):
    """How similar one indexed dish is to one query."""

    similarity: float
    item_id: str
    name: str  # case-folded dish name, used to de-duplicate copies
    attributes: set[str]
    query_terms: set[str]


@dataclass(frozen=True)
class IndexedItem:
    """One dish stored in the in-memory index."""

    item_id: str
    outlet_id: str
    name: str
    text: str
    vector: list[float]
    attributes: list[str]
    cuisine_tags: list[str]


@dataclass
class CatalogEmbeddingIndex:
    """In-memory index built from a catalog (used in tests and small deployments)."""

    catalog_id: str
    catalog_version: str
    model_name: str
    index_policy_version: str
    created_at: str
    indexed_items: list[IndexedItem] = field(default_factory=list)
    _embedder: Any = field(default_factory=DeterministicEmbedder)

    @classmethod
    def build(
        cls, catalog: Catalog, embedder: Any | None = None, at: datetime | None = None
    ) -> CatalogEmbeddingIndex:
        """Index dishes whose outlet *and* dish sources currently permit embedding."""
        at_time = at or datetime.now(timezone.utc)
        embedder = embedder or DeterministicEmbedder()
        usable_outlets = {
            o.outlet_id
            for o in catalog.outlets
            if catalog.evidence_usable(o.source_ids, purpose="embed", at=at_time)
        }
        indexed = []
        for item in catalog.menu_items:
            if item.outlet_id not in usable_outlets:
                continue
            if not catalog.evidence_usable(
                item.source_ids, purpose="embed", at=at_time
            ):
                continue
            text = build_embed_text(item)
            indexed.append(
                IndexedItem(
                    item_id=item.item_id,
                    outlet_id=item.outlet_id,
                    name=item.name,
                    text=text,
                    vector=embedder.embed_text(text),
                    attributes=list(item.attributes),
                    cuisine_tags=list(item.cuisine_tags),
                )
            )
        return cls(
            catalog_id=catalog.catalog_id,
            catalog_version=catalog.version,
            model_name=getattr(embedder, "model_name", EMBEDDING_MODEL_NAME),
            index_policy_version=RETRIEVAL_POLICY_VERSION,
            created_at=at_time.isoformat(),
            indexed_items=indexed,
            _embedder=embedder,
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

    def item_hits(
        self, query_text: str, items_by_id, outlet_ids
    ) -> dict[str, list[ItemHit]]:
        """Similarity of every indexed dish to the query, grouped by outlet."""
        vector = self._embedder.embed_text(query_text)
        terms = set(query_text.casefold().split())
        hits: dict[str, list[ItemHit]] = {}
        for item in self.indexed_items:
            similarity = self._embedder.similarity(vector, item.vector)
            hits.setdefault(item.outlet_id, []).append(
                ItemHit(
                    similarity,
                    item.item_id,
                    item.name.casefold().strip(),
                    set(item.attributes),
                    terms,
                )
            )
        return hits


@dataclass
class PersistentCatalogIndex:
    """Chroma collection on disk, selected by ``active_index.json``."""

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
        except Exception:  # noqa: BLE001 - a broken collection reads as empty, never crashes
            return set()

    def item_hits(
        self, query_text: str, items_by_id, outlet_ids
    ) -> dict[str, list[ItemHit]]:
        """Nearest dishes from Chroma, grouped by outlet.

        A bounded area (``outlet_ids``) scores every dish, not a global top 100, so
        nearby outlets are never cut off.
        """
        vector = self._embedder.embed_text(query_text)
        terms = set(query_text.casefold().split())
        limit = (
            self.indexed_item_count
            if outlet_ids is not None
            else min(100, self.indexed_item_count)
        )
        found = self.collection.query(query_embeddings=[vector], n_results=limit)
        if not found or not found.get("ids") or not found["ids"][0]:
            return {}
        ids = found["ids"][0]
        distances = (
            found["distances"][0] if found.get("distances") else [0.0] * len(ids)
        )
        metas = found["metadatas"][0] if found.get("metadatas") else [{}] * len(ids)
        hits: dict[str, list[ItemHit]] = {}
        for item_id, distance, meta in zip(ids, distances, metas, strict=False):
            similarity = max(
                0.0, min(1.0, 1.0 - distance / 2.0)
            )  # cosine distance → [0, 1]
            item = items_by_id.get(item_id)
            hits.setdefault(meta.get("outlet_id", ""), []).append(
                ItemHit(
                    similarity,
                    item_id,
                    item.name.casefold().strip() if item else item_id,
                    set(item.attributes) if item else set(),
                    terms,
                )
            )
        return hits


# ======================================================================= persistence
def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON via a temp file and rename, so readers never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    descriptor, name = tempfile.mkstemp(
        prefix="active-idx-", suffix=".json", dir=path.parent
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


def load_persistent_index(
    persist_dir: Path | str, catalog: Catalog | None = None, embedder: Any | None = None
) -> PersistentCatalogIndex | None:
    """Load the active Chroma index if present and built for this catalog version."""
    persist_path = Path(persist_dir)
    active_path = persist_path / "active_index.json"
    if not active_path.exists():
        return None
    try:
        meta = json.loads(active_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - an unreadable pointer means "no index"
        return None
    if catalog is not None and (
        catalog.catalog_id != meta.get("catalog_id")
        or catalog.version != meta.get("catalog_version")
    ):
        return None
    if meta.get("index_policy_version") != RETRIEVAL_POLICY_VERSION:
        return None

    import chromadb

    try:
        client = chromadb.PersistentClient(path=str(persist_path))
        collection = client.get_collection(name=meta["collection_name"])
    except Exception:  # noqa: BLE001 - a missing collection means "no index"
        return None
    model_name = meta.get("embedding_model", EMBEDDING_MODEL_NAME)
    return PersistentCatalogIndex(
        persist_dir=persist_path,
        catalog_id=meta["catalog_id"],
        catalog_version=meta["catalog_version"],
        collection_name=meta["collection_name"],
        model_name=model_name,
        index_policy_version=meta.get("index_policy_version", RETRIEVAL_POLICY_VERSION),
        indexed_item_count=meta.get("indexed_item_count", collection.count()),
        indexed_outlet_count=meta.get("indexed_outlet_count", 0),
        client=client,
        collection=collection,
        _embedder=embedder or create_embedder(model_name),
    )


def _load_manifest_scope(catalog: Catalog, manifest_path: Path) -> set[str]:
    """Allowed outlet IDs from the activation manifest, checked against the catalog."""
    if not manifest_path.exists():
        raise FileNotFoundError(f"Activation manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    allowed = set(manifest.get("allowed_outlet_ids", []))
    if len(allowed) != EXPECTED_OUTLETS:
        raise ValueError(
            f"Activation manifest must have exactly {EXPECTED_OUTLETS} outlets, got {len(allowed)}"
        )
    if catalog.catalog_id != manifest.get("catalog_id"):
        raise ValueError(
            f"Catalog ID mismatch: {catalog.catalog_id} != {manifest.get('catalog_id')}"
        )
    if catalog.version != manifest.get("catalog_version"):
        raise ValueError(
            f"Catalog version mismatch: {catalog.version} != {manifest.get('catalog_version')}"
        )
    return allowed


def build_persistent_catalog_index(
    catalog: Catalog,
    outlet_manifest_path: Path | str,
    persist_dir: Path | str,
    model_name: str = PRODUCTION_MODEL_NAME,
    embedder: Any | None = None,
    rebuild: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build a versioned Chroma index for the activated reviewed dishes, then switch to it.

    Steps: preflight the scope (refuse unexpected counts) → embed → write a *new*
    collection → validate its size → atomically repoint ``active_index.json`` → delete the
    previous collection. A failed build never touches the active pointer.
    """
    allowed = _load_manifest_scope(catalog, Path(outlet_manifest_path))
    outlets_by_id = {o.outlet_id: o for o in catalog.outlets}

    # Preflight before loading the model.
    eligible, quarantined, outside = [], [], []
    for item in catalog.menu_items:
        if item.outlet_id not in allowed:
            outside.append(item)
        elif item.review_status == "reviewed":
            eligible.append(item)
        elif item.review_status == "quarantined":
            quarantined.append(item)
    if len(eligible) != EXPECTED_VECTORS:
        raise ValueError(
            f"Preflight assertion failed: expected exactly {EXPECTED_VECTORS} reviewed items in scope, got {len(eligible)}"
        )
    if len(quarantined) != EXPECTED_QUARANTINED:
        raise ValueError(
            f"Preflight assertion failed: expected exactly {EXPECTED_QUARANTINED} quarantined item, got {len(quarantined)}"
        )

    documents = [
        build_embed_text(item, outlets_by_id.get(item.outlet_id)) for item in eligible
    ]
    ids = [item.item_id for item in eligible]
    metadatas = [
        {
            "catalog_id": catalog.catalog_id,
            "catalog_version": catalog.version,
            "outlet_id": item.outlet_id,
            "item_id": item.item_id,
            "embedding_model": model_name,
            "model_revision": getattr(embedder, "revision", "main"),
            "index_policy_version": RETRIEVAL_POLICY_VERSION,
        }
        for item in eligible
    ]
    if dry_run:
        return {
            "dry_run": True,
            "catalog_id": catalog.catalog_id,
            "catalog_version": catalog.version,
            "allowed_outlets": len(allowed),
            "eligible_reviewed_items": len(eligible),
            "quarantined_items": len(quarantined),
            "unreviewed_items_outside_scope": len(outside),
            "target_vector_count": len(eligible),
        }

    embedder = embedder or create_embedder(model_name)
    embeddings = [embedder.embed_text(doc) for doc in documents]

    from uuid import uuid4

    import chromadb

    persist_path = Path(persist_dir)
    persist_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(persist_path))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    clean_id = catalog.catalog_id.replace("-", "_")
    clean_version = catalog.version.replace(".", "_").replace("-", "_")
    collection_name = f"catalog_{clean_id}_{clean_version}_{stamp}_{uuid4().hex[:6]}"
    collection = client.create_collection(
        name=collection_name, metadata={"hnsw:space": "cosine"}
    )
    for start in range(0, len(ids), CHROMA_BATCH_SIZE):
        end = start + CHROMA_BATCH_SIZE
        collection.add(
            ids=ids[start:end],
            embeddings=embeddings[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end],
        )

    count = collection.count()
    if count != EXPECTED_VECTORS:
        # Abort without touching the active pointer; remove the partial collection.
        try:
            client.delete_collection(name=collection_name)
        except Exception:  # noqa: BLE001, S110 - best-effort cleanup only
            pass
        raise ValueError(
            f"Collection build validation failed: expected {EXPECTED_VECTORS} vectors, got {count}"
        )

    active_path = persist_path / "active_index.json"
    previous = None
    if active_path.exists():
        try:
            previous = json.loads(active_path.read_text(encoding="utf-8")).get(
                "collection_name"
            )
        except Exception:  # noqa: BLE001, S110 - an unreadable old pointer just isn't pruned
            pass
    _atomic_write_json(
        active_path,
        {
            "active_version": "1.0",
            "collection_name": collection_name,
            "catalog_id": catalog.catalog_id,
            "catalog_version": catalog.version,
            "embedding_model": model_name,
            "model_revision": getattr(embedder, "revision", "main"),
            "index_policy_version": RETRIEVAL_POLICY_VERSION,
            "indexed_item_count": count,
            "indexed_outlet_count": len(allowed),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "status": "active",
        },
    )
    if previous and previous != collection_name:
        try:
            client.delete_collection(name=previous)
        except Exception:  # noqa: BLE001, S110 - stale collections are harmless if left
            pass
    return {
        "status": "ok",
        "collection_name": collection_name,
        "indexed_item_count": count,
        "indexed_outlet_count": len(allowed),
        "embedding_model": model_name,
        "index_policy_version": RETRIEVAL_POLICY_VERSION,
        "active_manifest": str(active_path),
    }


# ============================================================================== search
def _fallback_structured_search(
    catalog: Catalog,
    queries: list[RetrievalQuery],
    maximum_outlets: int = 30,
    maximum_items_per_outlet: int = 2,
) -> dict[str, Any]:
    """Without a usable index: every outlet with a menu, in catalog order, at a neutral 0.5."""
    candidates = []
    for outlet in catalog.outlets:
        items = [i for i in catalog.menu_items if i.outlet_id == outlet.outlet_id]
        if not items or any(c["outlet_id"] == outlet.outlet_id for c in candidates):
            continue
        candidates.append(
            {
                "outlet_id": outlet.outlet_id,
                "semantic_score": 0.5,
                "recall_score": 0.5,
                "representative_item_ids": [
                    i.item_id for i in items[:maximum_items_per_outlet]
                ],
            }
        )
        if len(candidates) >= maximum_outlets:
            break
    return {
        "status": "fallback",
        "fallback_reason": "index_unavailable",
        "policy_version": RETRIEVAL_POLICY_VERSION,
        "candidates": candidates,
    }


def _score_outlet(
    outlet_id: str, hits: list[ItemHit], items_by_id, max_items: int
) -> dict[str, Any]:
    """Fuse one outlet's dish hits into a candidate with a ``recall_score``."""
    # Duplicate copies of the same dish (same name) count once, at their best similarity.
    distinct: dict[str, ItemHit] = {}
    for hit in hits:
        if hit.name not in distinct or hit.similarity > distinct[hit.name].similarity:
            distinct[hit.name] = hit
    ranked = sorted(distinct.values(), key=lambda h: -h.similarity)

    # Semantic: mean of the outlet's top-2 distinct dishes.
    top = [h.similarity for h in ranked[:2]]
    semantic = sum(top) / len(top) if top else 0.0
    # Tags: share of query words that are curated tags on the best dish.
    best = ranked[0]
    tag_match = (
        min(
            1.0, len(best.attributes & best.query_terms) / max(1, len(best.query_terms))
        )
        if best.query_terms
        else 0.0
    )
    # Names: whole-word dish-name match, using reviewed translations. The multilingual
    # embedder is weak on local dish names (laksa, nasi lemak), so an exact word counts.
    name_match = max(
        (
            name_term_overlap(
                " ".join(sorted(h.query_terms)),
                item_search_text(items_by_id[h.item_id])
                if h.item_id in items_by_id
                else h.name,
            )
            for h in ranked
        ),
        default=0.0,
    )
    recall = (
        SEMANTIC_WEIGHT * semantic
        + TAG_WEIGHT * tag_match
        + NAME_WEIGHT * name_match
        + SEMANTIC_CAP_WEIGHT * min(1.0, semantic)
    )
    representatives: list[str] = []
    for hit in ranked:
        if hit.item_id not in representatives:
            representatives.append(hit.item_id)
        if len(representatives) >= max_items:
            break
    return {
        "outlet_id": outlet_id,
        "semantic_score": round(semantic, 4),
        "recall_score": round(recall, 4),
        "representative_item_ids": representatives,
    }


def semantic_candidate_search(
    index: CatalogEmbeddingIndex | PersistentCatalogIndex | None,
    catalog: Catalog,
    queries: list[RetrievalQuery],
    maximum_outlets: int = 30,
    maximum_items_per_outlet: int = 2,
    outlet_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Outlet candidates ranked by hybrid relevance to the queries.

    ``outlet_ids`` restricts candidates (e.g. to the meal's search radius) *before* the
    top-N cut, so nearby outlets are never crowded out by distant strong matches.
    """
    if index is None or not index.is_usable_for_catalog(catalog):
        return _fallback_structured_search(
            catalog, queries, maximum_outlets, maximum_items_per_outlet
        )

    items_by_id = {item.item_id: item for item in catalog.menu_items}
    hits_by_outlet: dict[str, list[ItemHit]] = {}
    for query in queries:
        text = query.sanitized_text
        if not text:
            continue
        for outlet_id, hits in index.item_hits(text, items_by_id, outlet_ids).items():
            hits_by_outlet.setdefault(outlet_id, []).extend(hits)
    if outlet_ids is not None:
        hits_by_outlet = {
            oid: hits for oid, hits in hits_by_outlet.items() if oid in outlet_ids
        }
    if not hits_by_outlet:
        return _fallback_structured_search(
            catalog, queries, maximum_outlets, maximum_items_per_outlet
        )

    scored = [
        _score_outlet(outlet_id, hits, items_by_id, maximum_items_per_outlet)
        for outlet_id, hits in hits_by_outlet.items()
    ]
    scored.sort(key=lambda c: (-c["recall_score"], c["outlet_id"]))
    return {
        "status": "ok",
        "policy_version": RETRIEVAL_POLICY_VERSION,
        "model_name": index.model_name,
        "candidates": scored[:maximum_outlets],
    }
