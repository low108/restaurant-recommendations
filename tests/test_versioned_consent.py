import pytest
from fastapi import FastAPI
from test_api import make_client, recommendation

from dining.api import build_router
from dining.consent import CURRENT_NOTICES, ConsentPurpose
from dining.store import DiningStore


@pytest.fixture
def pilot(tmp_path):
    store = DiningStore(tmp_path / "consent.sqlite3")
    app = FastAPI()
    app.include_router(build_router(store, recommendation))
    clients = [make_client(app, name) for name in ("ConsentUserA", "ConsentUserB")]
    yield store, app, clients
    store.close()


def test_purpose_specific_consent_records_and_query(pilot):
    _store, _app, clients = pilot
    user = clients[0]

    # 1. Query initial consent status
    res = user.get("/api/consent")
    assert res.status_code == 200
    data = res.json()
    assert "notices" in data
    assert "consents" in data
    assert ConsentPurpose.TERMS.value in data["notices"]
    assert ConsentPurpose.LOCATION_ROUTING.value in data["notices"]

    # 2. Grant explicit consent for location routing
    curr_routing_ver = CURRENT_NOTICES[ConsentPurpose.LOCATION_ROUTING.value]
    grant_res = user.post(
        "/api/consent",
        json={
            "purpose": "location_routing",
            "notice_version": curr_routing_ver,
            "decision": "accepted",
            "source_interface": "profile_privacy_settings",
        },
    )
    assert grant_res.status_code == 200
    record = grant_res.json()
    assert record["purpose"] == "location_routing"
    assert record["decision"] == "accepted"
    assert record["needs_review"] is False

    # 3. Verify status updated
    status_res = user.get("/api/consent").json()
    routing_consent = status_res["consents"]["location_routing"]
    assert routing_consent["active"] is True
    assert routing_consent["decision"] == "accepted"


def test_absence_of_consent_disables_processing(pilot):
    _store, _app, clients = pilot
    user = clients[0]

    # Initially, model_processing is not consented
    status = user.get("/api/consent").json()
    assert status["consents"]["model_processing"]["active"] is False

    # Decline model processing explicitly
    curr_ver = CURRENT_NOTICES[ConsentPurpose.MODEL_PROCESSING.value]
    user.post(
        "/api/consent",
        json={
            "purpose": "model_processing",
            "notice_version": curr_ver,
            "decision": "declined",
            "source_interface": "taste_draft_modal",
        },
    ).raise_for_status()

    updated = user.get("/api/consent").json()
    assert updated["consents"]["model_processing"]["active"] is False
    assert updated["consents"]["model_processing"]["decision"] == "declined"


def test_new_notice_version_prompts_review(pilot):
    _store, _app, clients = pilot
    user = clients[0]

    # User accepted older version
    old_version = "2026-01-01.0"
    user.post(
        "/api/consent",
        json={
            "purpose": "terms",
            "notice_version": old_version,
            "decision": "accepted",
            "source_interface": "web_onboarding",
        },
    ).raise_for_status()

    # Query status - current version is newer, so needs_review must be True
    status = user.get("/api/consent").json()
    terms_status = status["consents"]["terms"]
    assert terms_status["accepted_version"] == old_version
    assert terms_status["needs_review"] is True


def test_withdrawal_stops_future_processing_without_erasing_history(pilot):
    _store, _app, clients = pilot
    user = clients[0]

    curr_ver = CURRENT_NOTICES[ConsentPurpose.LEARNING.value]

    # Accept learning
    user.post(
        "/api/consent",
        json={
            "purpose": "learning",
            "notice_version": curr_ver,
            "decision": "accepted",
            "source_interface": "profile",
        },
    ).raise_for_status()

    # Withdraw learning consent
    user.post(
        "/api/consent",
        json={
            "purpose": "learning",
            "notice_version": curr_ver,
            "decision": "withdrawn",
            "source_interface": "profile",
        },
    ).raise_for_status()

    # Status shows inactive
    status = user.get("/api/consent").json()
    assert status["consents"]["learning"]["active"] is False
    assert status["consents"]["learning"]["decision"] == "withdrawn"

    # Export contains both historical records preserving audit trail
    export_res = user.get("/api/export").json()
    assert "consent_records" in export_res
    learning_records = [
        r for r in export_res["consent_records"] if r["purpose"] == "learning"
    ]
    assert len(learning_records) == 2
    assert {r["decision"] for r in learning_records} == {"accepted", "withdrawn"}


def test_consent_records_deleted_on_account_deletion(pilot):
    store, _app, clients = pilot
    user = clients[0]

    user.post(
        "/api/consent",
        json={
            "purpose": "terms",
            "notice_version": CURRENT_NOTICES["terms"],
            "decision": "accepted",
            "source_interface": "web",
        },
    ).raise_for_status()

    # Delete account
    user.delete("/api/account").raise_for_status()

    # Database has no consent records for deleted user
    with store.transaction() as db:
        rows = db.execute(
            "SELECT * FROM consent_records WHERE user_id=?", (user.user_id,)
        ).fetchall()
        assert len(rows) == 0
