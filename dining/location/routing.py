"""Provider-neutral routing abstraction.

Estimates and route feasibility are evidence-backed. Missing or disabled routing
produces explicit unknown results and never guesses travel time from straight-line distance.
Precise origins must never enter shared meal responses or model prompts.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from dining.core.constants import MobilityMode


class RouteStatus(str, enum.Enum):
    """Outcome of a travel-time request."""

    OK = "ok"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True)
class RouteCoordinate:
    """A validated latitude/longitude pair."""

    latitude: float
    longitude: float

    def __post_init__(self):
        if not (-90.0 <= self.latitude <= 90.0):
            raise ValueError(f"Latitude {self.latitude} must be in [-90, 90]")
        if not (-180.0 <= self.longitude <= 180.0):
            raise ValueError(f"Longitude {self.longitude} must be in [-180, 180]")


@dataclass(frozen=True)
class RouteRequest:
    """Origin, destination, travel mode and time (departure defaults to now)."""

    origin: RouteCoordinate
    destination: RouteCoordinate
    mobility_mode: MobilityMode = "drive"
    departure_time: datetime | None = None
    arrival_time: datetime | None = None

    def __post_init__(self):
        if self.departure_time is None and self.arrival_time is None:
            # Default to now if neither is specified
            object.__setattr__(self, "departure_time", datetime.now(timezone.utc))


@dataclass(frozen=True)
class RouteEvidence:
    """Travel time and distance from a provider, with an expiry."""

    status: RouteStatus
    duration_seconds: int | None = None
    distance_meters: int | None = None
    provider_timestamp: datetime | None = None
    evidence_expiry: datetime | None = None
    unavailable_reason: str | None = None
    provider_id: str = "disabled"

    def is_usable(self, at: datetime | None = None) -> bool:
        """True for an OK result with duration and distance that has not expired."""
        if self.status != RouteStatus.OK:
            return False
        if self.duration_seconds is None or self.distance_meters is None:
            return False
        if self.evidence_expiry is not None:
            now = at or datetime.now(timezone.utc)
            if now > self.evidence_expiry:
                return False
        return True

    @property
    def duration_minutes(self) -> int | None:
        """Duration rounded up to whole minutes."""
        if self.duration_seconds is None:
            return None
        return (self.duration_seconds + 59) // 60

    def to_shared_dict(self) -> dict[str, Any]:
        """Group-safe projection: omits precise coordinates and private origins."""
        return {
            "status": self.status.value,
            "duration_minutes": self.duration_minutes,
            "distance_km": (
                round(self.distance_meters / 1000.0, 1)
                if self.distance_meters is not None
                else None
            ),
            "evidence_fresh": self.is_usable(),
            "provider_id": self.provider_id,
            "unavailable_reason": self.unavailable_reason,
        }


class RoutingProvider(ABC):
    """Interface for travel-time providers."""

    @abstractmethod
    def calculate_route(self, request: RouteRequest) -> RouteEvidence:
        """Calculate route evidence for a request."""
        raise NotImplementedError


class DisabledRoutingProvider(RoutingProvider):
    """Default provider: routing is disabled and yields explicit unknown evidence."""

    def calculate_route(self, request: RouteRequest) -> RouteEvidence:
        """Always 'disabled'."""
        now = (
            request.departure_time or request.arrival_time or datetime.now(timezone.utc)
        )
        return RouteEvidence(
            status=RouteStatus.DISABLED,
            duration_seconds=None,
            distance_meters=None,
            provider_timestamp=now,
            evidence_expiry=None,
            unavailable_reason="routing_disabled",
            provider_id="disabled",
        )


class FakeRoutingProvider(RoutingProvider):
    """Deterministic fake provider for test environments."""

    def __init__(
        self,
        *,
        simulate_timeout: bool = False,
        timeout_seconds: float = 5.0,
        simulate_error: str | None = None,
        simulate_invalid: bool = False,
        default_ttl_seconds: int = 3600,
    ):
        self.simulate_timeout = simulate_timeout
        self.timeout_seconds = timeout_seconds
        self.simulate_error = simulate_error
        self.simulate_invalid = simulate_invalid
        self.default_ttl_seconds = default_ttl_seconds
        self._routes: dict[
            tuple[float, float, float, float, str], tuple[int, int, int]
        ] = {}

    def set_route(
        self,
        *,
        origin: RouteCoordinate,
        destination: RouteCoordinate,
        mobility_mode: MobilityMode,
        duration_seconds: int,
        distance_meters: int,
        ttl_seconds: int = 3600,
    ):
        """Register a fixed result for an origin/destination/mode."""
        key = (
            round(origin.latitude, 4),
            round(origin.longitude, 4),
            round(destination.latitude, 4),
            round(destination.longitude, 4),
            mobility_mode,
        )
        self._routes[key] = (duration_seconds, distance_meters, ttl_seconds)

    def calculate_route(self, request: RouteRequest) -> RouteEvidence:
        """A simulated timeout/error/invalid result if configured, else the registered route or unavailable."""
        now = (
            request.departure_time or request.arrival_time or datetime.now(timezone.utc)
        )

        if self.simulate_timeout:
            return RouteEvidence(
                status=RouteStatus.TIMEOUT,
                duration_seconds=None,
                distance_meters=None,
                provider_timestamp=now,
                unavailable_reason="timeout",
                provider_id="fake_provider",
            )

        if self.simulate_error:
            return RouteEvidence(
                status=RouteStatus.ERROR,
                duration_seconds=None,
                distance_meters=None,
                provider_timestamp=now,
                unavailable_reason=self.simulate_error,
                provider_id="fake_provider",
            )

        if self.simulate_invalid:
            return RouteEvidence(
                status=RouteStatus.UNAVAILABLE,
                duration_seconds=None,
                distance_meters=None,
                provider_timestamp=now,
                unavailable_reason="invalid_route_data",
                provider_id="fake_provider",
            )

        key = (
            round(request.origin.latitude, 4),
            round(request.origin.longitude, 4),
            round(request.destination.latitude, 4),
            round(request.destination.longitude, 4),
            request.mobility_mode,
        )

        match = self._routes.get(key)
        if match is not None:
            duration_s, distance_m, ttl_s = match
            return RouteEvidence(
                status=RouteStatus.OK,
                duration_seconds=duration_s,
                distance_meters=distance_m,
                provider_timestamp=now,
                evidence_expiry=now + timedelta(seconds=ttl_s),
                unavailable_reason=None,
                provider_id="fake_provider",
            )

        return RouteEvidence(
            status=RouteStatus.UNAVAILABLE,
            duration_seconds=None,
            distance_meters=None,
            provider_timestamp=now,
            unavailable_reason="route_not_found",
            provider_id="fake_provider",
        )


_CURRENT_ROUTING_PROVIDER: RoutingProvider = DisabledRoutingProvider()


def get_routing_provider() -> RoutingProvider:
    """The process-wide routing provider (disabled by default)."""
    return _CURRENT_ROUTING_PROVIDER


def set_routing_provider(provider: RoutingProvider | None) -> None:
    """Replace the process-wide provider; None restores the disabled one."""
    global _CURRENT_ROUTING_PROVIDER
    if provider is None:
        _CURRENT_ROUTING_PROVIDER = DisabledRoutingProvider()
    else:
        _CURRENT_ROUTING_PROVIDER = provider
