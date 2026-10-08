from datetime import datetime, timezone

from test_recommendation import ready_catalog, snapshot

from dining.catalog.models import Catalog
from dining.retrieval.index import (
    CatalogEmbeddingIndex,
    RetrievalQuery,
    semantic_candidate_search,
)


def test_source_rights_and_expiry_filtering():
    cat_data = ready_catalog().model_dump(mode="json")
    now = datetime.now(timezone.utc)

    # Source 0 is synthetic fixture with rights.embed = "allowed"
    assert cat_data["sources"][0]["rights"]["embed"] == "allowed"

    # Add a second source with rights.embed = "prohibited"
    second_source = dict(cat_data["sources"][0])
    second_source["source_id"] = "prohibited-source"
    second_source["rights"] = dict(second_source["rights"])
    second_source["rights"]["embed"] = "prohibited"
    cat_data["sources"].append(second_source)

    # Add an outlet associated only with the prohibited source
    second_outlet = dict(cat_data["outlets"][0])
    second_outlet["outlet_id"] = "prohibited-outlet"
    second_outlet["source_ids"] = ["prohibited-source"]
    cat_data["outlets"].append(second_outlet)

    # Add a menu item associated only with the prohibited source
    second_item = dict(cat_data["menu_items"][0])
    second_item["item_id"] = "prohibited-item"
    second_item["outlet_id"] = "prohibited-outlet"
    second_item["source_ids"] = ["prohibited-source"]
    cat_data["menu_items"].append(second_item)

    catalog = Catalog.model_validate(cat_data)
    index = CatalogEmbeddingIndex.build(catalog, at=now)

    # Only records with rights.embed == "allowed" must be indexed
    indexed_outlet_ids = index.indexed_outlet_ids()
    assert "prohibited-outlet" not in indexed_outlet_ids
    assert all("prohibited-item" not in item.item_id for item in index.indexed_items)


def test_index_catalog_version_mismatch_refusal():
    catalog = ready_catalog()
    now = datetime.now(timezone.utc)
    index = CatalogEmbeddingIndex.build(catalog, at=now)

    # Correct version matches
    assert index.is_usable_for_catalog(catalog)

    # Changed catalog version must be refused
    cat_data = catalog.model_dump(mode="json")
    cat_data["version"] = "999.0"
    modified_catalog = Catalog.model_validate(cat_data)
    assert not index.is_usable_for_catalog(modified_catalog)


def test_per_outlet_menu_item_cap_and_duplicate_menu_invariance():
    catalog = ready_catalog()
    now = datetime.now(timezone.utc)
    index = CatalogEmbeddingIndex.build(catalog, at=now)

    query = RetrievalQuery(query_id="test-1", text="chicken noodle soup", top_k=10)
    results_base = semantic_candidate_search(
        index=index,
        catalog=catalog,
        queries=[query],
        maximum_outlets=10,
        maximum_items_per_outlet=2,
    )

    # 1. Per-outlet item cap is strictly respected
    for candidate in results_base["candidates"]:
        assert len(candidate["representative_item_ids"]) <= 2

    # 2. Duplicate menu item invariance: adding duplicate menu items does not inflate semantic score
    cat_data = catalog.model_dump(mode="json")
    # Duplicate existing menu item 5 times
    first_item = dict(cat_data["menu_items"][0])
    for i in range(5):
        dup = dict(first_item)
        dup["item_id"] = f"{first_item['item_id']}-dup-{i}"
        cat_data["menu_items"].append(dup)

    dup_catalog = Catalog.model_validate(cat_data)
    dup_index = CatalogEmbeddingIndex.build(dup_catalog, at=now)

    results_dup = semantic_candidate_search(
        index=dup_index,
        catalog=dup_catalog,
        queries=[query],
        maximum_outlets=10,
        maximum_items_per_outlet=2,
    )

    # The top candidate outlet score should remain bounded and identical
    outlet_id = first_item["outlet_id"]
    base_candidate = next(
        c for c in results_base["candidates"] if c["outlet_id"] == outlet_id
    )
    dup_candidate = next(
        c for c in results_dup["candidates"] if c["outlet_id"] == outlet_id
    )
    assert (
        abs(base_candidate["semantic_score"] - dup_candidate["semantic_score"]) < 1e-4
    )


def test_sensitive_text_and_allergies_rejected_from_retrieval_queries():
    catalog = ready_catalog()
    index = CatalogEmbeddingIndex.build(catalog)

    SENTINEL_ALLERGY = "severe peanut allergy anaphylaxis"
    SENTINEL_GPS = "3.123456,101.987654"
    SENTINEL_SECRET = "sk-secret-token-key-123456"

    # Query with private sentinels must be sanitized
    query = RetrievalQuery(
        query_id="safe-test",
        text=f"want tasty soup but I have {SENTINEL_ALLERGY} at {SENTINEL_GPS} with key {SENTINEL_SECRET}",
        top_k=5,
    )
    assert query.sanitized_text != query.text
    assert SENTINEL_ALLERGY not in query.sanitized_text
    assert SENTINEL_GPS not in query.sanitized_text
    assert SENTINEL_SECRET not in query.sanitized_text

    # Search should run safely on sanitized text
    res = semantic_candidate_search(
        index=index,
        catalog=catalog,
        queries=[query],
    )
    dump = str(res)
    assert SENTINEL_ALLERGY not in dump
    assert SENTINEL_GPS not in dump
    assert SENTINEL_SECRET not in dump


def test_multilingual_queries_retrieve_candidates_deterministically():
    catalog = ready_catalog()
    index = CatalogEmbeddingIndex.build(catalog)

    queries = [
        RetrievalQuery(query_id="en", text="spicy noodles", top_k=5),
        RetrievalQuery(query_id="bm", text="mee pedas sedap", top_k=5),
        RetrievalQuery(query_id="manglish", text="jom tapau noodle soup", top_k=5),
    ]

    res = semantic_candidate_search(
        index=index,
        catalog=catalog,
        queries=queries,
    )

    assert len(res["candidates"]) > 0
    assert res["policy_version"] == "retrieval-policy-v1"
    # Deterministic across multiple calls
    res_repeat = semantic_candidate_search(
        index=index,
        catalog=catalog,
        queries=queries,
    )
    assert res["candidates"] == res_repeat["candidates"]


def test_unavailable_index_falls_back_to_structured_retrieval():
    catalog = ready_catalog()
    queries = [RetrievalQuery(query_id="fallback-test", text="soup", top_k=5)]

    # Pass index=None to simulate unavailable index
    res = semantic_candidate_search(
        index=None,
        catalog=catalog,
        queries=queries,
    )
    assert res["status"] == "fallback"
    assert res["fallback_reason"] == "index_unavailable"
    assert len(res["candidates"]) > 0  # Fallback successfully produces candidates


def test_multilingual_judgment_set_evaluation():
    from dining.retrieval.evaluation import evaluate_retrieval

    catalog = ready_catalog()
    report = evaluate_retrieval(catalog)

    assert report["total_cases"] >= 8
    assert report["mean_recall_at_10"] > 0.7
    assert report["mean_ndcg_at_10"] > 0.6
    assert report["unique_outlets_retrieved"] > 0
    assert "english" in report["language_recall"]
    assert "bahasa_melayu" in report["language_recall"]
    assert "mixed" in report["language_recall"]


def test_recommender_integration_with_semantic_retrieval():
    from dining.recommendation.engine import Recommender

    catalog = ready_catalog()
    index = CatalogEmbeddingIndex.build(catalog)

    snap = snapshot()
    # 1. With semantic index
    rec_semantic = Recommender(catalog, embedding_index=index)
    res_semantic = rec_semantic(snap)
    assert res_semantic["status"] == "shortlisted"
    assert res_semantic["retrieval_status"] == "semantic"
    assert res_semantic["retrieval_policy_version"] == "retrieval-policy-v1"
    assert res_semantic["embedding_model"] == "deterministic-hash-v1"

    # 2. With fallback (no index)
    rec_fallback = Recommender(catalog, embedding_index=None)
    res_fallback = rec_fallback(snap)
    assert res_fallback["status"] == "shortlisted"
    assert res_fallback["retrieval_status"] == "structured_fallback"
    assert res_fallback["embedding_model"] is None
