"""Provider-neutral geocoding and neighbourhood lookup abstraction.

External geocoding remains disabled by default until credentials and terms are reviewed.
Users can always input location labels and coordinates manually.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class GeocodingStatus(str, enum.Enum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    ERROR = "error"


@dataclass(frozen=True)
class LocationCandidate:
    label: str
    latitude: float
    longitude: float
    neighbourhood: str | None = None
    city: str | None = None
    provider_id: str = "disabled"

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "neighbourhood": self.neighbourhood,
            "city": self.city,
            "provider_id": self.provider_id,
        }


@dataclass(frozen=True)
class GeocodingResponse:
    status: GeocodingStatus
    results: list[LocationCandidate]
    unavailable_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "results": [r.to_dict() for r in self.results],
            "unavailable_reason": self.unavailable_reason,
        }


class GeocodingProvider(ABC):
    @abstractmethod
    def lookup_neighbourhood(self, name: str) -> GeocodingResponse:
        """Search for a neighbourhood or location label."""
        raise NotImplementedError

    @abstractmethod
    def reverse_lookup(self, latitude: float, longitude: float) -> GeocodingResponse:
        """Reverse geocode coordinates to a neighbourhood or label."""
        raise NotImplementedError


class DisabledGeocodingProvider(GeocodingProvider):
    """Default provider: external geocoding is disabled and returns empty results."""

    def lookup_neighbourhood(self, name: str) -> GeocodingResponse:
        return GeocodingResponse(
            status=GeocodingStatus.DISABLED,
            results=[],
            unavailable_reason="geocoding_disabled",
        )

    def reverse_lookup(self, latitude: float, longitude: float) -> GeocodingResponse:
        return GeocodingResponse(
            status=GeocodingStatus.DISABLED,
            results=[],
            unavailable_reason="geocoding_disabled",
        )


class FakeGeocodingProvider(GeocodingProvider):
    """Deterministic fake provider for tests."""

    def __init__(self, *, simulate_error: str | None = None):
        self.simulate_error = simulate_error
        self._neighbourhoods: list[LocationCandidate] = []

    def add_neighbourhood(self, candidate: LocationCandidate) -> None:
        self._neighbourhoods.append(candidate)

    def lookup_neighbourhood(self, name: str) -> GeocodingResponse:
        if self.simulate_error:
            return GeocodingResponse(
                status=GeocodingStatus.ERROR,
                results=[],
                unavailable_reason=self.simulate_error,
            )

        query = name.casefold().strip()
        matches = [
            n
            for n in self._neighbourhoods
            if query in n.label.casefold()
            or (n.neighbourhood and query in n.neighbourhood.casefold())
            or (n.city and query in n.city.casefold())
        ]
        if matches:
            return GeocodingResponse(status=GeocodingStatus.OK, results=matches)
        return GeocodingResponse(
            status=GeocodingStatus.UNAVAILABLE,
            results=[],
            unavailable_reason="location_not_found",
        )

    def reverse_lookup(self, latitude: float, longitude: float) -> GeocodingResponse:
        if self.simulate_error:
            return GeocodingResponse(
                status=GeocodingStatus.ERROR,
                results=[],
                unavailable_reason=self.simulate_error,
            )

        if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
            return GeocodingResponse(
                status=GeocodingStatus.UNAVAILABLE,
                results=[],
                unavailable_reason="invalid_coordinates",
            )

        matches = [
            n
            for n in self._neighbourhoods
            if abs(n.latitude - latitude) < 0.01 and abs(n.longitude - longitude) < 0.01
        ]
        if matches:
            return GeocodingResponse(status=GeocodingStatus.OK, results=matches)
        return GeocodingResponse(
            status=GeocodingStatus.UNAVAILABLE,
            results=[],
            unavailable_reason="location_not_found",
        )


_CURRENT_GEOCODING_PROVIDER: GeocodingProvider = DisabledGeocodingProvider()


def get_geocoding_provider() -> GeocodingProvider:
    return _CURRENT_GEOCODING_PROVIDER


def set_geocoding_provider(provider: GeocodingProvider | None) -> None:
    global _CURRENT_GEOCODING_PROVIDER
    if provider is None:
        _CURRENT_GEOCODING_PROVIDER = DisabledGeocodingProvider()
    else:
        _CURRENT_GEOCODING_PROVIDER = provider
