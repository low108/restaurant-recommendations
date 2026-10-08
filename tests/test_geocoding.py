from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from test_api import make_client, recommendation, setup_room

from dining.api import build_router
from dining.core.store import DiningStore
from dining.location.geocoding import (
    DisabledGeocodingProvider,
    FakeGeocodingProvider,
    GeocodingStatus,
    LocationCandidate,
    get_geocoding_provider,
    set_geocoding_provider,
)

# Fixed calendar dates go stale and the API rejects meals in the past, so test meals are
# scheduled two days ahead (12:00 and 19:00 Kuala Lumpur time).
_MEAL_DAY = datetime.now(timezone.utc) + timedelta(days=2)
LUNCH_AT = _MEAL_DAY.replace(hour=4, minute=0, second=0, microsecond=0).isoformat()
DINNER_AT = _MEAL_DAY.replace(hour=11, minute=0, second=0, microsecond=0).isoformat()


@pytest.fixture
def pilot(tmp_path):
    store = DiningStore(tmp_path / "geo.sqlite3")
    app = FastAPI()
    app.include_router(build_router(store, recommendation))
    clients = [make_client(app, name) for name in ("GeoUserA", "GeoUserB")]
    yield store, app, clients
    store.close()


def test_disabled_geocoding_provider_default():
    provider = get_geocoding_provider()
    assert isinstance(provider, DisabledGeocodingProvider)

    res = provider.lookup_neighbourhood("Bangsar")
    assert res.status == GeocodingStatus.DISABLED
    assert res.results == []
    assert res.unavailable_reason == "geocoding_disabled"


def test_fake_geocoding_neighbourhood_and_reverse():
    fake = FakeGeocodingProvider()
    fake.add_neighbourhood(
        LocationCandidate(
            label="SS2, Petaling Jaya",
            latitude=3.118,
            longitude=101.622,
            neighbourhood="SS2",
            city="Petaling Jaya",
            provider_id="fake",
        )
    )

    res = fake.lookup_neighbourhood("SS2")
    assert res.status == GeocodingStatus.OK
    assert len(res.results) == 1
    assert res.results[0].latitude == 3.118
    assert res.results[0].longitude == 101.622

    rev = fake.reverse_lookup(latitude=3.118, longitude=101.622)
    assert rev.status == GeocodingStatus.OK
    assert len(rev.results) == 1
    assert rev.results[0].neighbourhood == "SS2"


def test_provider_failure_does_not_block_meal_creation(pilot):
    _store, _app, clients = pilot
    owner, _friend = clients[:2]
    room_id, _ = setup_room(clients)

    # Configure a failing geocoding provider
    fake = FakeGeocodingProvider(simulate_error="geocoding_service_down")
    set_geocoding_provider(fake)
    try:
        # User looks up location via API - returns gracefully without crashing
        res = owner.get("/api/locations/neighbourhoods?query=SS2")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] in ("error", "unavailable")
        assert data["results"] == []

        # User types location manually and creates meal successfully
        meal_res = owner.post(
            f"/api/rooms/{room_id}/meals",
            json={
                "kind": "lunch",
                "meal_at": LUNCH_AT,
                "location_label": "Manual SS2 Meeting Point",
                "latitude": 3.118,
                "longitude": 101.622,
                "idempotency_key": "manual-geo-test-1",
            },
        )
        assert meal_res.status_code == 201
        created = meal_res.json()
        assert created["location_label"] == "Manual SS2 Meeting Point"
        assert created["latitude"] == 3.118
        assert created["longitude"] == 101.622
    finally:
        set_geocoding_provider(None)


def test_invalid_coordinates_handled_safely():
    provider = FakeGeocodingProvider()
    # Invalid latitude/longitude out of range
    res = provider.reverse_lookup(latitude=120.0, longitude=300.0)
    assert res.status == GeocodingStatus.UNAVAILABLE
    assert res.unavailable_reason == "invalid_coordinates"


def test_public_meal_location_stored_separately_from_private_origins(pilot):
    _store, _app, clients = pilot
    owner, friend = clients[:2]
    room_id, _ = setup_room(clients)

    # Create meal with public normalized meeting location
    meal_res = owner.post(
        f"/api/rooms/{room_id}/meals",
        json={
            "kind": "dinner",
            "meal_at": DINNER_AT,
            "location_label": "Public Mall Meeting Point",
            "latitude": 3.1500,
            "longitude": 101.7100,
            "idempotency_key": "separate-location-test-1",
        },
    )
    assert meal_res.status_code == 201
    meal_id = meal_res.json()["id"]

    # Owner sets private origin
    owner.put(
        f"/api/meals/{meal_id}/origin",
        json={
            "origin_mode": "precise",
            "latitude": 3.0500,
            "longitude": 101.5500,
            "route_consent": True,
        },
    ).raise_for_status()

    # Meal shared view shows public location, not private origin
    shared_view = friend.get(f"/api/meals/{meal_id}").json()
    assert shared_view["location_label"] == "Public Mall Meeting Point"
    assert shared_view["latitude"] == 3.1500
    assert shared_view["longitude"] == 101.7100
    assert shared_view["my_origin"] is None
