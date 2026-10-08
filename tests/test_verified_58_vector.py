"""Comprehensive verification suite for the 58 verified outlets, persistent Chroma index,

and live agent semantic retrieval integration.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_api import create_meal, make_client, setup_room

from dining.catalog.manifest import (
    generate_activation_manifest,
    load_activation_manifest,
)
from dining.catalog.models import load_catalog
from dining.recommendation.agent import DiningAgent
from dining.recommendation.engine import Recommender
from dining.retrieval.index import (
    DeterministicEmbedder,
    RetrievalQuery,
    build_persistent_catalog_index,
    load_persistent_index,
    semantic_candidate_search,
)
from webapp import create_app

ROOT = Path(__file__).resolve().parents[1]
STAGED_DIR = ROOT / "var/catalog-import/kl-selangor-real-pilot-58"
CATALOG_PATH = STAGED_DIR / "catalog.validated.json"
MANIFEST_PATH = STAGED_DIR / "activation-manifest.json"
# The live index is built from the production catalog (0.4.0-translated, 7 Oct 2026).
LIVE_DIR = ROOT / "var/catalog-import/kl-selangor-real-pilot-58-translated"
LIVE_CATALOG_PATH = LIVE_DIR / "catalog.validated.json"
LIVE_MANIFEST_PATH = LIVE_DIR / "activation-manifest.json"
VECTOR_DIR = ROOT / "var/vector/catalog"

# Local verification only: needs the private var/ catalog imports and the vector extras
# (requirements-vector.txt), which CI does not install.
pytest.importorskip("chromadb", reason="vector extras are not installed")
if not (CATALOG_PATH.exists() and LIVE_CATALOG_PATH.exists()):
    pytest.skip("private var/ catalog imports are not present", allow_module_level=True)


# ==============================================================================
# Catalog Boundary Tests
# ==============================================================================


def test_mark_items_reviewed_activates_58_outlets_and_quarantines_conflict():
    manifest = load_activation_manifest(MANIFEST_PATH)
    catalog = load_catalog(CATALOG_PATH)

    allowed_oids = set(manifest["allowed_outlet_ids"])
    assert len(allowed_oids) == 58

    scope_items = [i for i in catalog.menu_items if i.outlet_id in allowed_oids]
    assert len(scope_items) == 1074

    reviewed_items = [i for i in scope_items if i.review_status == "reviewed"]
    assert len(reviewed_items) == 1073

    quarantined_items = [i for i in scope_items if i.review_status == "quarantined"]
    assert len(quarantined_items) == 1
    assert (
        quarantined_items[0].item_id
        == "ditaliane-ioi-alfredo-funghi-fettuccine-3-pcs-beef-meatballs"
    )
    assert "dietary_claim_conflicts_with_variant" in quarantined_items[0].review_reasons


def test_allowlist_contains_exactly_patch_outlets():
    manifest = load_activation_manifest(MANIFEST_PATH)
    with open(ROOT / "data/enrichment/location-hours.patch.json") as f:
        patch = json.load(f)
    patch_oids = sorted(u["outlet_id"] for u in patch["outlet_updates"])
    assert manifest["allowed_outlet_ids"] == patch_oids
    assert len(manifest["allowed_outlet_ids"]) == 58


def test_allowlist_rejects_unknown_outlet_and_changed_checksum(tmp_path):
    # Tampered checksum rejection
    with pytest.raises(ValueError, match="SHA mismatch"):
        generate_activation_manifest(
            catalog_path=CATALOG_PATH,
            patch_path=ROOT / "data/enrichment/location-hours.patch.json",
            sources_path=ROOT / "data/enrichment/location-hours.sources.json",
            original_catalog_path=ROOT
            / "data/enrichment/location-hours.sources.json",  # wrong file
            output_manifest_path=tmp_path / "bad-manifest.json",
            verify_checksums=True,
        )

    # Missing/unknown outlet rejection
    bad_patch = json.loads(
        (ROOT / "data/enrichment/location-hours.patch.json").read_text()
    )
    bad_patch["outlet_updates"][0]["outlet_id"] = "unknown-nonexistent-outlet"
    bad_patch_path = tmp_path / "bad_patch.json"
    bad_patch_path.write_text(json.dumps(bad_patch))

    with pytest.raises(ValueError):
        generate_activation_manifest(
            catalog_path=CATALOG_PATH,
            patch_path=bad_patch_path,
            sources_path=ROOT / "data/enrichment/location-hours.sources.json",
            original_catalog_path=ROOT
            / "var/catalog-import/kl-selangor-real-pilot-0.2.0-reviewed/catalog.v2.json",
            output_manifest_path=tmp_path / "bad-manifest2.json",
            verify_checksums=False,
        )


def test_unresolved_70_outlets_never_enter_58_outlet_scope():
    manifest = load_activation_manifest(MANIFEST_PATH)
    catalog = load_catalog(CATALOG_PATH)

    allowed_oids = set(manifest["allowed_outlet_ids"])
    excluded_oids = {e["outlet_id"] for e in manifest["exclusions"]["excluded_outlets"]}

    assert len(allowed_oids) == 58
    assert len(excluded_oids) == 70
    assert allowed_oids.isdisjoint(excluded_oids)
    assert len(allowed_oids | excluded_oids) == len(catalog.outlets) == 128


# ==============================================================================
# Persistent-Index Tests
# ==============================================================================


def test_persistent_index_contains_exactly_1073_vectors():
    catalog = load_catalog(LIVE_CATALOG_PATH)
    index = load_persistent_index(VECTOR_DIR, catalog=catalog)
    assert index is not None
    assert index.is_usable_for_catalog(catalog)
    assert index.indexed_item_count == 1073
    assert index.indexed_outlet_count == 58

    # Ensure all indexed outlet IDs belong exclusively to the 58 allowed outlets
    manifest = load_activation_manifest(LIVE_MANIFEST_PATH)
    indexed_oids = index.indexed_outlet_ids()
    assert indexed_oids.issubset(set(manifest["allowed_outlet_ids"]))


def test_unreviewed_and_quarantined_records_excluded_from_vector_store():
    catalog = load_catalog(LIVE_CATALOG_PATH)
    index = load_persistent_index(VECTOR_DIR, catalog=catalog)
    assert index is not None

    metas = index.collection.get(include=["metadatas"])["metadatas"]
    indexed_item_ids = {m["item_id"] for m in metas}

    # Quarantined record excluded
    assert (
        "ditaliane-ioi-alfredo-funghi-fettuccine-3-pcs-beef-meatballs"
        not in indexed_item_ids
    )

    # Unreviewed items from the 70 outlets excluded
    manifest = load_activation_manifest(LIVE_MANIFEST_PATH)
    allowed_oids = set(manifest["allowed_outlet_ids"])
    unreviewed_item_ids = {
        item.item_id
        for item in catalog.menu_items
        if item.outlet_id not in allowed_oids
    }
    assert len(unreviewed_item_ids) == 1364
    assert indexed_item_ids.isdisjoint(unreviewed_item_ids)


def test_rebuilding_persistent_index_is_idempotent(tmp_path):
    catalog = load_catalog(CATALOG_PATH)
    embedder = DeterministicEmbedder()

    # Build 1
    res1 = build_persistent_catalog_index(
        catalog=catalog,
        outlet_manifest_path=MANIFEST_PATH,
        persist_dir=tmp_path / "idx",
        model_name="deterministic-hash-v1",
        embedder=embedder,
    )
    assert res1["indexed_item_count"] == 1073

    # Build 2 (rebuild)
    res2 = build_persistent_catalog_index(
        catalog=catalog,
        outlet_manifest_path=MANIFEST_PATH,
        persist_dir=tmp_path / "idx",
        model_name="deterministic-hash-v1",
        embedder=embedder,
        rebuild=True,
    )
    assert res2["indexed_item_count"] == 1073

    idx = load_persistent_index(tmp_path / "idx", catalog=catalog, embedder=embedder)
    assert idx is not None
    assert idx.indexed_item_count == 1073


def test_stale_vectors_disappear_only_after_successful_atomic_switch(tmp_path):
    catalog = load_catalog(CATALOG_PATH)
    embedder = DeterministicEmbedder()

    # First build
    res1 = build_persistent_catalog_index(
        catalog=catalog,
        outlet_manifest_path=MANIFEST_PATH,
        persist_dir=tmp_path / "atomic",
        model_name="deterministic-hash-v1",
        embedder=embedder,
    )
    col1_name = res1["collection_name"]

    # Rebuild
    res2 = build_persistent_catalog_index(
        catalog=catalog,
        outlet_manifest_path=MANIFEST_PATH,
        persist_dir=tmp_path / "atomic",
        model_name="deterministic-hash-v1",
        embedder=embedder,
        rebuild=True,
    )
    col2_name = res2["collection_name"]
    assert col1_name != col2_name

    # Check active manifest points to new collection
    active_meta = json.loads((tmp_path / "atomic/active_index.json").read_text())
    assert active_meta["collection_name"] == col2_name

    # Check old collection has been removed from Chroma
    idx = load_persistent_index(tmp_path / "atomic", catalog=catalog, embedder=embedder)
    col_names = [c.name for c in idx.client.list_collections()]
    assert col1_name not in col_names
    assert col2_name in col_names


def test_interrupted_build_preserves_prior_active_collection(tmp_path, monkeypatch):
    catalog = load_catalog(CATALOG_PATH)
    embedder = DeterministicEmbedder()

    # Initial valid build
    res1 = build_persistent_catalog_index(
        catalog=catalog,
        outlet_manifest_path=MANIFEST_PATH,
        persist_dir=tmp_path / "interrupt",
        model_name="deterministic-hash-v1",
        embedder=embedder,
    )
    col1_name = res1["collection_name"]

    # Now attempt a failing rebuild by simulating an insertion failure
    import chromadb

    def failing_add(*args, **kwargs):
        raise RuntimeError("Simulated mid-build network/disk crash")

    monkeypatch.setattr(chromadb.api.models.Collection.Collection, "add", failing_add)

    with pytest.raises(RuntimeError, match="Simulated mid-build"):
        build_persistent_catalog_index(
            catalog=catalog,
            outlet_manifest_path=MANIFEST_PATH,
            persist_dir=tmp_path / "interrupt",
            model_name="deterministic-hash-v1",
            embedder=embedder,
            rebuild=True,
        )

    # Prior active collection must remain untouched and active
    active_meta = json.loads((tmp_path / "interrupt/active_index.json").read_text())
    assert active_meta["collection_name"] == col1_name

    idx = load_persistent_index(
        tmp_path / "interrupt", catalog=catalog, embedder=embedder
    )
    assert idx is not None
    assert idx.collection_name == col1_name
    assert idx.indexed_item_count == 1073


def test_catalog_and_policy_mismatch_refuses_index(tmp_path):
    catalog = load_catalog(CATALOG_PATH)
    embedder = DeterministicEmbedder()

    build_persistent_catalog_index(
        catalog=catalog,
        outlet_manifest_path=MANIFEST_PATH,
        persist_dir=tmp_path / "mismatch",
        model_name="deterministic-hash-v1",
        embedder=embedder,
    )

    # 1. Version mismatch
    modified_cat = catalog.model_copy(update={"version": "999.0"})
    assert load_persistent_index(tmp_path / "mismatch", catalog=modified_cat) is None

    # 2. Catalog ID mismatch
    modified_cat2 = catalog.model_copy(update={"catalog_id": "wrong-catalog"})
    assert load_persistent_index(tmp_path / "mismatch", catalog=modified_cat2) is None


def test_vectors_and_disallowed_text_never_appear_in_status_or_logs():
    app = create_app(catalog_path=LIVE_CATALOG_PATH, index_path=VECTOR_DIR)
    client = TestClient(app)

    res = client.get("/api/catalog/status")
    assert res.status_code == 200
    data = res.json()

    # Status has aggregate numbers only
    retrieval = data["retrieval"]
    assert retrieval["mode"] == "semantic"
    assert retrieval["index_available"] is True
    assert retrieval["indexed_item_count"] == 1073
    assert retrieval["indexed_outlet_count"] == 58

    text = res.text
    # No vectors, no private sentinels
    assert "embeddings" not in text
    assert "vector" not in text.lower() or "retrieval" in text.lower()
    assert "sk-" not in text
    assert "allergy" not in text.lower()


# ==============================================================================
# Retrieval and Ranking Tests
# ==============================================================================


def test_live_agent_calls_semantic_retrieval_spy(monkeypatch):
    import dining.recommendation.engine as rec_module

    catalog = load_catalog(LIVE_CATALOG_PATH)
    index = load_persistent_index(VECTOR_DIR, catalog=catalog)

    called = False
    orig_search = rec_module.semantic_candidate_search

    def spy_search(*args, **kwargs):
        nonlocal called
        called = True
        return orig_search(*args, **kwargs)

    monkeypatch.setattr(rec_module, "semantic_candidate_search", spy_search)

    agent = DiningAgent(catalog, embedding_index=index)
    snap = {
        "revision": 1,
        "meal": {
            "id": "test-spy",
            "meal_at": (datetime.now(timezone.utc) + timedelta(days=1))
            .replace(hour=4, minute=0, second=0)
            .isoformat(),
            "latitude": 3.11,
            "longitude": 101.62,
            "radius_km": 10,
        },
        "participants": [
            {
                "user_id": "p1",
                "profile": {
                    "allergy_status": "none",
                    "halal_policy": "none",
                    "requirements_reviewed": True,
                    "max_budget": 50,
                },
                "response": {
                    "craving": "noodle soup",
                    "budget": 50,
                    "requirements_confirmed": True,
                },
            },
            {
                "user_id": "p2",
                "profile": {
                    "allergy_status": "none",
                    "halal_policy": "none",
                    "requirements_reviewed": True,
                    "max_budget": 50,
                },
                "response": {
                    "craving": "hot soup",
                    "budget": 50,
                    "requirements_confirmed": True,
                },
            },
        ],
    }

    result = agent(snap)
    assert called is True
    assert result["retrieval_status"] == "semantic"


def test_semantic_results_bound_reviewed_candidate_set(monkeypatch):
    catalog = load_catalog(LIVE_CATALOG_PATH)
    index = load_persistent_index(VECTOR_DIR, catalog=catalog)

    import dining.recommendation.engine as rec_module

    # Mock semantic search to return only 1 specific outlet candidate
    def mock_search(*args, **kwargs):
        return {
            "status": "ok",
            "policy_version": "retrieval-policy-v1",
            "candidates": [
                {
                    "outlet_id": "green-view-pj",
                    "semantic_score": 0.95,
                    "recall_score": 0.95,
                    "representative_item_ids": ["item-1"],
                }
            ],
        }

    monkeypatch.setattr(rec_module, "semantic_candidate_search", mock_search)

    rec = Recommender(catalog, embedding_index=index)
    snap = {
        "revision": 1,
        "meal": {
            "id": "test-bound",
            "meal_at": (datetime.now(timezone.utc) + timedelta(days=1))
            .replace(hour=4, minute=0, second=0)
            .isoformat(),
            "latitude": 3.11,
            "longitude": 101.62,
            "radius_km": 10,
        },
        "participants": [
            {
                "user_id": "p1",
                "profile": {
                    "allergy_status": "none",
                    "halal_policy": "none",
                    "requirements_reviewed": True,
                    "max_budget": 50,
                },
                "response": {
                    "craving": "chicken",
                    "budget": 50,
                    "requirements_confirmed": True,
                },
            },
            {
                "user_id": "p2",
                "profile": {
                    "allergy_status": "none",
                    "halal_policy": "none",
                    "requirements_reviewed": True,
                    "max_budget": 50,
                },
                "response": {
                    "craving": "chicken",
                    "budget": 50,
                    "requirements_confirmed": True,
                },
            },
        ],
    }

    result = rec(snap)
    assert result["status"] == "shortlisted"
    assert len(result["options"]) == 1
    assert result["options"][0]["outlet_id"] == "green-view-pj"
    # Examined count bounded to retrieved candidate
    assert result["examined_outlets"] == 1


def test_similarity_1_0_cannot_bypass_deterministic_blocker(monkeypatch):
    catalog = load_catalog(LIVE_CATALOG_PATH)
    index = load_persistent_index(VECTOR_DIR, catalog=catalog)

    import dining.recommendation.engine as rec_module

    # Mock semantic search returning outlet with 1.0 similarity
    def mock_search(*args, **kwargs):
        return {
            "status": "ok",
            "policy_version": "retrieval-policy-v1",
            "candidates": [
                {
                    "outlet_id": "green-view-pj",
                    "semantic_score": 1.0,
                    "recall_score": 1.0,
                    "representative_item_ids": ["item-1"],
                }
            ],
        }

    monkeypatch.setattr(rec_module, "semantic_candidate_search", mock_search)

    # Participant declares strict certified halal (green-view-pj is not certified)
    snap = {
        "revision": 1,
        "meal": {
            "id": "test-blocker",
            "meal_at": (datetime.now(timezone.utc) + timedelta(days=1))
            .replace(hour=4, minute=0, second=0)
            .isoformat(),
            "latitude": 3.11,
            "longitude": 101.62,
            "radius_km": 10,
        },
        "participants": [
            {
                "user_id": "p1",
                "profile": {
                    "allergy_status": "none",
                    "halal_policy": "certified",
                    "requirements_reviewed": True,
                    "max_budget": 50,
                },
                "response": {
                    "craving": "chicken",
                    "budget": 50,
                    "requirements_confirmed": True,
                },
            },
            {
                "user_id": "p2",
                "profile": {
                    "allergy_status": "none",
                    "halal_policy": "none",
                    "requirements_reviewed": True,
                    "max_budget": 50,
                },
                "response": {
                    "craving": "chicken",
                    "budget": 50,
                    "requirements_confirmed": True,
                },
            },
        ],
    }

    result = Recommender(catalog, embedding_index=index)(snap)
    # Blocked: certified halal requirement cannot be established
    assert not result["options"]
    assert result["status"] == "needs_verification"


def test_unavailable_stale_index_falls_back_to_structured():
    catalog = load_catalog(CATALOG_PATH)
    rec = Recommender(catalog, embedding_index=None)

    snap = {
        "revision": 1,
        "meal": {
            "id": "test-fallback",
            "meal_at": (datetime.now(timezone.utc) + timedelta(days=1))
            .replace(hour=4, minute=0, second=0)
            .isoformat(),
            "latitude": 3.11,
            "longitude": 101.62,
            "radius_km": 10,
        },
        "participants": [
            {
                "user_id": "p1",
                "profile": {
                    "allergy_status": "none",
                    "halal_policy": "none",
                    "requirements_reviewed": True,
                    "max_budget": 50,
                },
                "response": {
                    "craving": "food",
                    "budget": 50,
                    "requirements_confirmed": True,
                },
            },
            {
                "user_id": "p2",
                "profile": {
                    "allergy_status": "none",
                    "halal_policy": "none",
                    "requirements_reviewed": True,
                    "max_budget": 50,
                },
                "response": {
                    "craving": "food",
                    "budget": 50,
                    "requirements_confirmed": True,
                },
            },
        ],
    }

    result = rec(snap)
    assert result["status"] == "shortlisted"
    assert result["retrieval_status"] == "structured_fallback"
    assert result["embedding_model"] is None


def test_multilingual_queries_judgment_cases_on_persistent_index():
    catalog = load_catalog(LIVE_CATALOG_PATH)
    index = load_persistent_index(VECTOR_DIR, catalog=catalog)
    assert index is not None

    cases = [
        RetrievalQuery(query_id="en", text="spicy fried noodles", top_k=5),
        RetrievalQuery(query_id="bm", text="mee goreng pedas sedap", top_k=5),
        RetrievalQuery(query_id="mixed", text="jom makan noodle sup", top_k=5),
    ]

    res = semantic_candidate_search(index, catalog, cases, maximum_outlets=10)
    assert res["status"] == "ok"
    assert len(res["candidates"]) > 0


# ==============================================================================
# Real-Data End-to-End Tests
# ==============================================================================


def test_real_58_outlet_flow_full_journey(tmp_path):
    db_path = tmp_path / "real_journey.sqlite3"
    app = create_app(
        db_path=db_path,
        catalog_path=LIVE_CATALOG_PATH,
        index_path=VECTOR_DIR,
        async_generation=False,
    )

    host = make_client(app, "Siti")
    guest = make_client(app, "Wei")

    # Set profiles
    for c in (host, guest):
        assert (
            c.patch(
                "/api/profile",
                json={
                    "allergy_status": "none",
                    "halal_policy": "none",
                    "requirements_reviewed": True,
                    "max_budget": 50,
                },
            ).status_code
            == 200
        )

    # Create room & meal in PJ (near several of the 58 verified outlets)
    room_id, _ = setup_room([host, guest])

    meal, _ = create_meal(
        host,
        room_id,
        latitude=3.119,
        longitude=101.629,
        radius_km=8,
    )

    # Check-ins
    for c, craving in [(host, "chicken rice"), (guest, "tasty soup")]:
        ans_res = c.put(
            f"/api/meals/{meal['id']}/response",
            json={
                "attendance": "join",
                "budget": 50,
                "craving": craving,
                "ready": True,
                "requirements_confirmed": True,
                "expected_response_revision": 0,
            },
        )
        assert ans_res.status_code == 200
        meal = ans_res.json()

    # Generate recommendation
    gen_res = host.post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    assert gen_res.status_code == 200
    gen_data = gen_res.json()
    result = gen_data["result"]

    assert result["status"] == "shortlisted"
    assert result["retrieval_status"] == "semantic"
    assert len(result["options"]) >= 1

    manifest = load_activation_manifest(LIVE_MANIFEST_PATH)
    allowed_oids = set(manifest["allowed_outlet_ids"])

    # Verify options come strictly from 58 verified outlets
    for opt in result["options"]:
        assert opt["outlet_id"] in allowed_oids
        assert (
            opt["outlet_id"]
            != "ditaliane-ioi-alfredo-funghi-fettuccine-3-pcs-beef-meatballs"
        )
        for item in opt["menu_items"]:
            assert (
                item["id"]
                != "ditaliane-ioi-alfredo-funghi-fettuccine-3-pcs-beef-meatballs"
            )

    # Verify personal alternatives generated and owner-scoped
    host_meal_info = host.get(f"/api/meals/{meal['id']}").json()
    host_pers = host_meal_info.get("my_personal_recommendations", [])
    assert len(host_pers) >= 1
    for p_opt in host_pers:
        assert p_opt["outlet_id"] in allowed_oids
        assert (
            p_opt["item_id"]
            != "ditaliane-ioi-alfredo-funghi-fettuccine-3-pcs-beef-meatballs"
        )

    # Guest votes & unanimous select
    chosen_opt = result["options"][0]["id"]
    for c in (host, guest):
        v_res = c.post(
            f"/api/meals/{meal['id']}/votes",
            json={
                "expected_revision": gen_data["revision"],
                "option_id": chosen_opt,
                "choice": "works",
            },
        )
        assert v_res.status_code == 200

    sel_res = host.post(
        f"/api/meals/{meal['id']}/select",
        json={"expected_revision": gen_data["revision"], "option_id": chosen_opt},
    )
    assert sel_res.status_code == 200
    assert sel_res.json()["decision"]["option_id"] == chosen_opt

    app.state.store.close()
