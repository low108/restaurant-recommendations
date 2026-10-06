from datetime import datetime, timedelta, timezone

from test_recommendation import ready_catalog, snapshot

from dining.catalog import (
    Catalog,
)
from dining.recommendation import Recommender


def make_source(source_id, observed_at, expires_at):
    return {
        "source_id": source_id,
        "url": f"https://example.test/{source_id}",
        "kind": "synthetic",
        "evidence_text": "Accessibility audit evidence",
        "observed_at": observed_at,
        "expires_at": expires_at,
        "rights": {"basis": "test", "display": "allowed", "embed": "allowed"},
    }


def test_firm_accessibility_passes_with_reviewed_fresh_evidence():
    catalog_data = ready_catalog().model_dump()
    now_iso = datetime.now(timezone.utc).isoformat()
    future_iso = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

    catalog_data["sources"] = list(catalog_data["sources"]) + [
        make_source("audit-access-1", now_iso, future_iso)
    ]

    # Outlet has reviewed accessible features
    catalog_data["outlets"][0]["accessibility"] = {
        "step_free_entrance": {
            "status": "accessible",
            "source_ids": ["audit-access-1"],
            "review_status": "reviewed",
        },
        "wheelchair_accessible_seating": {
            "status": "accessible",
            "source_ids": ["audit-access-1"],
            "review_status": "reviewed",
        },
    }

    catalog = Catalog.model_validate(catalog_data)
    state = snapshot()
    state["participants"][0]["profile"]["accessibility_requirements"] = [
        "step_free_entrance"
    ]
    state["participants"][0]["response"]["accessibility_requirements"] = [
        "step_free_entrance"
    ]

    recommender = Recommender(catalog)
    result = recommender(state)

    # First outlet should be eligible and pass the accessibility gate
    assert result["status"] == "shortlisted"
    assert any(
        opt["outlet_id"] == catalog_data["outlets"][0]["outlet_id"]
        for opt in result["options"]
    )


def test_missing_accessibility_evidence_is_treated_as_unknown_and_blocks():
    # By default, ready_catalog has unknown accessibility evidence
    catalog = ready_catalog()
    state = snapshot()
    state["participants"][0]["profile"]["accessibility_requirements"] = [
        "wheelchair_accessible_seating"
    ]

    recommender = Recommender(catalog)
    result = recommender(state)

    # Missing evidence must remain unknown and block publication
    assert result["status"] == "needs_verification" or not result["options"]


def test_inaccessible_feature_blocks_candidate():
    catalog_data = ready_catalog().model_dump()
    now_iso = datetime.now(timezone.utc).isoformat()
    future_iso = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    catalog_data["sources"] = list(catalog_data["sources"]) + [
        make_source("audit-access-2", now_iso, future_iso)
    ]
    for outlet in catalog_data["outlets"]:
        outlet["accessibility"] = {
            "step_free_entrance": {
                "status": "inaccessible",
                "source_ids": ["audit-access-2"],
                "review_status": "reviewed",
            }
        }
    catalog = Catalog.model_validate(catalog_data)

    state = snapshot()
    state["participants"][0]["profile"]["accessibility_requirements"] = [
        "step_free_entrance"
    ]

    recommender = Recommender(catalog)
    result = recommender(state)
    assert not result["options"]


def test_stale_accessibility_evidence_is_rejected():
    catalog_data = ready_catalog().model_dump()
    past_iso = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    expired_iso = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    catalog_data["sources"] = list(catalog_data["sources"]) + [
        make_source("stale-access", past_iso, expired_iso)
    ]
    for outlet in catalog_data["outlets"]:
        outlet["accessibility"] = {
            "step_free_entrance": {
                "status": "accessible",
                "source_ids": ["stale-access"],
                "review_status": "reviewed",
            }
        }
    catalog = Catalog.model_validate(catalog_data)

    state = snapshot()
    state["participants"][0]["profile"]["accessibility_requirements"] = [
        "step_free_entrance"
    ]

    recommender = Recommender(catalog)
    result = recommender(state)
    assert not result["options"]


def test_withheld_or_unknown_requirement_does_not_block():
    catalog = ready_catalog()
    state = snapshot()
    state["participants"][0]["profile"]["accessibility_requirements"] = ["withheld"]
    state["participants"][1]["profile"]["accessibility_requirements"] = ["unknown"]

    recommender = Recommender(catalog)
    result = recommender(state)
    assert result["status"] == "shortlisted"
    assert len(result["options"]) > 0


def test_accessibility_information_remains_private():
    catalog = ready_catalog()
    state = snapshot()
    state["participants"][0]["profile"]["accessibility_requirements"] = [
        "accessible_restroom"
    ]

    recommender = Recommender(catalog)
    result = recommender(state)

    # Shared verification or options must not disclose which participant needs accessible restroom
    shared_text = str(result)
    assert (
        "accessible_restroom" not in shared_text.lower()
        or result["status"] != "shortlisted"
    )
    for v in result.get("verification", []):
        for reason in v.get("reasons", []):
            assert "accessible_restroom" not in reason.lower()
