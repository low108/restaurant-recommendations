from datetime import datetime, timedelta, timezone

from dining.routing import (
    DisabledRoutingProvider,
    FakeRoutingProvider,
    RouteCoordinate,
    RouteEvidence,
    RouteRequest,
    RouteStatus,
    get_routing_provider,
    set_routing_provider,
)


def test_disabled_routing_provider_returns_unknown():
    provider = DisabledRoutingProvider()
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    request = RouteRequest(
        origin=RouteCoordinate(latitude=3.1390, longitude=101.6869),
        destination=RouteCoordinate(latitude=3.1500, longitude=101.7000),
        mobility_mode="drive",
        departure_time=now,
    )
    response = provider.calculate_route(request)
    assert response.status == RouteStatus.DISABLED
    assert response.duration_seconds is None
    assert response.distance_meters is None
    assert response.unavailable_reason == "routing_disabled"
    assert not response.is_usable(at=now)


def test_no_route_result_is_guessed_from_straight_line_distance():
    provider = DisabledRoutingProvider()
    request = RouteRequest(
        origin=RouteCoordinate(latitude=3.1390, longitude=101.6869),
        destination=RouteCoordinate(latitude=3.1500, longitude=101.7000),
        mobility_mode="walk",
        departure_time=datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc),
    )
    response = provider.calculate_route(request)
    assert response.duration_seconds is None
    assert response.distance_meters is None


def test_fake_routing_provider_success():
    provider = FakeRoutingProvider()
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    origin = RouteCoordinate(latitude=3.1390, longitude=101.6869)
    destination = RouteCoordinate(latitude=3.1500, longitude=101.7000)

    provider.set_route(
        origin=origin,
        destination=destination,
        mobility_mode="drive",
        duration_seconds=900,
        distance_meters=5400,
        ttl_seconds=3600,
    )

    request = RouteRequest(
        origin=origin,
        destination=destination,
        mobility_mode="drive",
        departure_time=now,
    )
    evidence = provider.calculate_route(request)

    assert evidence.status == RouteStatus.OK
    assert evidence.duration_seconds == 900
    assert evidence.distance_meters == 5400
    assert evidence.provider_timestamp is not None
    assert evidence.evidence_expiry is not None
    assert evidence.is_usable(at=now)
    assert not evidence.is_usable(at=now + timedelta(hours=2))


def test_fake_routing_provider_timeout():
    provider = FakeRoutingProvider(simulate_timeout=True, timeout_seconds=2.0)
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    request = RouteRequest(
        origin=RouteCoordinate(latitude=3.1390, longitude=101.6869),
        destination=RouteCoordinate(latitude=3.1500, longitude=101.7000),
        mobility_mode="drive",
        departure_time=now,
    )
    evidence = provider.calculate_route(request)
    assert evidence.status == RouteStatus.TIMEOUT
    assert evidence.duration_seconds is None
    assert evidence.unavailable_reason == "timeout"


def test_fake_routing_provider_failure():
    provider = FakeRoutingProvider(simulate_error="provider_unavailable")
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    request = RouteRequest(
        origin=RouteCoordinate(latitude=3.1390, longitude=101.6869),
        destination=RouteCoordinate(latitude=3.1500, longitude=101.7000),
        mobility_mode="drive",
        departure_time=now,
    )
    evidence = provider.calculate_route(request)
    assert evidence.status == RouteStatus.ERROR
    assert evidence.duration_seconds is None
    assert evidence.unavailable_reason == "provider_unavailable"


def test_invalid_result_handled():
    provider = FakeRoutingProvider(simulate_invalid=True)
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    request = RouteRequest(
        origin=RouteCoordinate(latitude=3.1390, longitude=101.6869),
        destination=RouteCoordinate(latitude=3.1500, longitude=101.7000),
        mobility_mode="drive",
        departure_time=now,
    )
    evidence = provider.calculate_route(request)
    assert evidence.status == RouteStatus.UNAVAILABLE
    assert evidence.duration_seconds is None
    assert evidence.unavailable_reason == "invalid_route_data"


def test_route_evidence_shared_projection_omits_precise_origin():
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    evidence = RouteEvidence(
        status=RouteStatus.OK,
        duration_seconds=1200,
        distance_meters=8000,
        provider_timestamp=now,
        evidence_expiry=now + timedelta(hours=1),
        provider_id="fake_provider",
    )
    shared = evidence.to_shared_dict()
    assert "origin" not in shared
    assert "latitude" not in shared
    assert "longitude" not in shared
    assert shared["status"] == "ok"
    assert shared["duration_minutes"] == 20


def test_global_routing_provider_registry():
    default_provider = get_routing_provider()
    assert isinstance(default_provider, DisabledRoutingProvider)

    fake = FakeRoutingProvider()
    set_routing_provider(fake)
    assert get_routing_provider() is fake

    set_routing_provider(None)
    assert isinstance(get_routing_provider(), DisabledRoutingProvider)


def test_recommender_can_request_route_evidence():
    from test_recommendation import ready_catalog

    from dining.recommendation import Recommender

    fake = FakeRoutingProvider()
    origin = RouteCoordinate(latitude=3.1390, longitude=101.6869)
    destination = RouteCoordinate(latitude=3.1500, longitude=101.7000)
    fake.set_route(
        origin=origin,
        destination=destination,
        mobility_mode="drive",
        duration_seconds=600,
        distance_meters=4000,
    )

    recommender = Recommender(
        ready_catalog(),
        routing_provider=fake,
    )
    evidence = recommender.request_route(
        origin=origin,
        destination=destination,
        mobility_mode="drive",
    )
    assert evidence.status == RouteStatus.OK
    assert evidence.duration_seconds == 600
    assert evidence.distance_meters == 4000
