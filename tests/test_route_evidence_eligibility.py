from test_recommendation import ready_catalog, snapshot

from dining.recommendation import Recommender
from dining.routing import (
    FakeRoutingProvider,
    RouteCoordinate,
)


def test_impossible_arrival_blocks_candidate_for_affected_diner():
    catalog = ready_catalog()
    fake = FakeRoutingProvider()
    diner_origin = RouteCoordinate(latitude=3.1390, longitude=101.6869)

    for outlet in catalog.outlets:
        outlet_coord = RouteCoordinate(
            latitude=outlet.latitude, longitude=outlet.longitude
        )
        # Route takes 90 minutes travel time
        fake.set_route(
            origin=diner_origin,
            destination=outlet_coord,
            mobility_mode="drive",
            duration_seconds=90 * 60,
            distance_meters=45000,
            ttl_seconds=3600,
        )

    state = snapshot()
    # Meal is at 12:00, lasts 60m (finishes at 13:00)
    # Diner 1 must leave by 13:00
    state["participants"][0]["origin"] = {
        "origin_mode": "precise",
        "latitude": 3.1390,
        "longitude": 101.6869,
        "route_consent": True,
    }
    state["participants"][0]["response"]["must_leave_by"] = "13:00"

    recommender = Recommender(catalog, routing_provider=fake)
    result = recommender(state)

    # 90 min travel means arrival at 13:30, after must_leave_by 13:00 -> blocked!
    assert not result["options"]
    assert result["status"] in ("no_options", "needs_verification")
    assert len(result["verification"]) > 0


def test_missing_routing_preserves_neutral_ranking_and_does_not_block():
    catalog = ready_catalog()
    state = snapshot()
    # Origin set, but routing provider disabled
    state["participants"][0]["origin"] = {
        "origin_mode": "precise",
        "latitude": 3.1390,
        "longitude": 101.6869,
        "route_consent": True,
    }
    state["participants"][0]["response"]["must_leave_by"] = "14:00"

    # Default disabled provider
    recommender = Recommender(catalog)
    result = recommender(state)

    # Missing routing remains unknown and does not invent zero travel time or block candidate
    assert result["status"] == "shortlisted"
    assert len(result["options"]) > 0


def test_stale_route_evidence_is_rejected():
    catalog = ready_catalog()
    outlet = catalog.outlets[0]
    fake = FakeRoutingProvider()

    diner_origin = RouteCoordinate(latitude=3.1390, longitude=101.6869)
    outlet_coord = RouteCoordinate(latitude=outlet.latitude, longitude=outlet.longitude)

    # TTL is only 1 second, so by meal time it is expired/stale
    fake.set_route(
        origin=diner_origin,
        destination=outlet_coord,
        mobility_mode="drive",
        duration_seconds=120 * 60,
        distance_meters=60000,
        ttl_seconds=-10,  # Already expired
    )

    state = snapshot()
    state["participants"][0]["origin"] = {
        "origin_mode": "precise",
        "latitude": 3.1390,
        "longitude": 101.6869,
        "route_consent": True,
    }
    state["participants"][0]["response"]["must_leave_by"] = "13:00"

    recommender = Recommender(catalog, routing_provider=fake)
    result = recommender(state)

    # Stale evidence is rejected and treated as unknown, not blocking
    assert result["status"] == "shortlisted"
    assert len(result["options"]) > 0


def test_private_origin_never_appears_in_shared_options():
    catalog = ready_catalog()
    fake = FakeRoutingProvider()

    diner_origin = RouteCoordinate(latitude=3.1390, longitude=101.6869)
    for outlet in catalog.outlets:
        outlet_coord = RouteCoordinate(
            latitude=outlet.latitude, longitude=outlet.longitude
        )
        fake.set_route(
            origin=diner_origin,
            destination=outlet_coord,
            mobility_mode="drive",
            duration_seconds=15 * 60,
            distance_meters=8000,
            ttl_seconds=3600,
        )

    state = snapshot()
    state["participants"][0]["user_id"] = "diner-1"
    state["participants"][0]["origin"] = {
        "origin_mode": "precise",
        "latitude": 3.1390,
        "longitude": 101.6869,
        "route_consent": True,
    }

    recommender = Recommender(catalog, routing_provider=fake)
    result = recommender(state)

    assert result["status"] == "shortlisted"
    for option in result["options"]:
        # Verify no diner origin coordinates leak in shared card
        assert "origin" not in option
        assert "3.139" not in str(option)
        assert "101.6869" not in str(option)
        assert "diner_origin" not in option
        assert "travel_aggregate" in option
        assert option["travel_aggregate"]["evidence_status"] == "firm"

    # Private route estimates are populated in _private_routes
    assert "_private_routes" in result
    user_id = state["participants"][0]["user_id"]
    assert user_id in result["_private_routes"]
    option_id = result["options"][0]["id"]
    assert option_id in result["_private_routes"][user_id]
    user_route = result["_private_routes"][user_id][option_id]
    assert user_route["eta_minutes"] == 15
    assert user_route["evidence_fresh"] is True


def test_route_provider_failure_leaves_fallback_usable():
    catalog = ready_catalog()
    fake = FakeRoutingProvider(simulate_error="service_unavailable")

    state = snapshot()
    state["participants"][0]["origin"] = {
        "origin_mode": "precise",
        "latitude": 3.1390,
        "longitude": 101.6869,
        "route_consent": True,
    }

    recommender = Recommender(catalog, routing_provider=fake)
    result = recommender(state)

    # Provider failure leaves normal fallback usable
    assert result["status"] == "shortlisted"
    assert len(result["options"]) > 0
    # Travel aggregate is unknown
    assert result["options"][0]["travel_aggregate"]["evidence_status"] == "unknown"
