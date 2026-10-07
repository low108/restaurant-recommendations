"""Versioned restaurant data contract shared with an independent data collector.

Loading validates claims, identity and provenance; it does not verify restaurant facts.
No fetching, embeddings or application storage writes happen in this module.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Permission = Literal["allowed", "prohibited", "unknown"]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Rights(Record):
    display: Permission = "unknown"
    embed: Permission = "unknown"
    basis: str = Field(min_length=1)


class Source(Record):
    source_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    kind: Literal[
        "synthetic",
        "official_website",
        "restaurant_supplied",
        "certification_registry",
        "other",
    ]
    observed_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    publisher_updated_at: AwareDatetime | None = None
    evidence_text: str = Field(min_length=1)
    rights: Rights

    @model_validator(mode="after")
    def valid_dates(self) -> Source:
        if self.expires_at is not None and self.expires_at <= self.observed_at:
            raise ValueError("source expires_at must follow observed_at")
        if self.kind != "synthetic" and not self.url.startswith(
            ("https://", "http://")
        ):
            raise ValueError("real source URL must be an HTTP(S) reference")
        return self

    def usable_for(
        self, purpose: Literal["display", "embed"], at: datetime | None = None
    ) -> bool:
        """Conservative publication gate: unknown expiry/permission is not current evidence."""
        now = at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("at must include a timezone")
        return (
            getattr(self.rights, purpose) == "allowed"
            and self.observed_at <= now
            and self.expires_at is not None
            and now < self.expires_at
        )


class Brand(Record):
    brand_id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class ServiceInterval(Record):
    opens: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    closes: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    closes_next_day: bool = False
    last_order: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    last_order_next_day: bool = False
    last_order_source_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid_interval(self) -> ServiceInterval:
        if not self.closes_next_day and self.closes <= self.opens:
            raise ValueError("same-day opening interval must end after it starts")
        if self.last_order is None:
            if self.last_order_next_day or self.last_order_source_ids:
                raise ValueError(
                    "unknown last order cannot have a day or evidence claim"
                )
        else:
            if not self.last_order_source_ids:
                raise ValueError("last order requires source_ids")
            # Clock strings have fixed width, so (day offset, clock) sorts correctly.
            opening = (0, self.opens)
            closing = (int(self.closes_next_day), self.closes)
            last_order = (int(self.last_order_next_day), self.last_order)
            if not opening <= last_order <= closing:
                raise ValueError("last order must fall within its opening interval")
        return self


class OpeningInterval(ServiceInterval):
    weekday: int = Field(ge=0, le=6, description="Monday=0, Sunday=6")


class OpeningException(Record):
    on_date: date
    status: Literal["closed", "published", "unknown"]
    intervals: tuple[ServiceInterval, ...] = ()
    source_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_override(self) -> OpeningException:
        if (self.status == "published") != bool(self.intervals):
            raise ValueError("only a published opening exception requires intervals")
        return self


class OpeningExceptionCoverage(Record):
    starts_on: date
    ends_on: date
    source_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_range(self) -> OpeningExceptionCoverage:
        if self.ends_on < self.starts_on:
            raise ValueError("exception coverage ends_on must not precede starts_on")
        return self


class HalalEvidence(Record):
    status: Literal["unknown", "certified", "restaurant_claim", "not_halal"] = "unknown"
    certificate_id: str | None = None
    authority: str | None = None
    source_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def support_claim(self) -> HalalEvidence:
        if self.status != "unknown" and not self.source_ids:
            raise ValueError("halal claim requires evidence source_ids")
        if self.status == "certified" and (
            not self.certificate_id or not self.authority
        ):
            raise ValueError("certified status requires certificate_id and authority")
        if self.status != "certified" and (self.certificate_id or self.authority):
            raise ValueError("certificate fields apply only to certified status")
        return self


class AccessibilityFeature(Record):
    status: Literal["unknown", "accessible", "inaccessible"] = "unknown"
    source_ids: tuple[str, ...] = ()
    review_status: Literal["unreviewed", "reviewed"] = "unreviewed"

    @model_validator(mode="after")
    def support_claim(self) -> AccessibilityFeature:
        if self.status != "unknown" and not self.source_ids:
            raise ValueError("accessibility claim requires evidence source_ids")
        return self


class AccessibilityEvidence(Record):
    step_free_entrance: AccessibilityFeature = Field(
        default_factory=AccessibilityFeature
    )
    wheelchair_accessible_seating: AccessibilityFeature = Field(
        default_factory=AccessibilityFeature
    )
    accessible_restroom: AccessibilityFeature = Field(
        default_factory=AccessibilityFeature
    )
    low_noise_seating: AccessibilityFeature = Field(
        default_factory=AccessibilityFeature
    )


class Outlet(Record):
    outlet_id: str = Field(min_length=1)
    brand_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    address: str = Field(min_length=1)
    city: str = Field(min_length=1)
    state: str = Field(min_length=1)
    country: Literal["MY"] = "MY"
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    timezone: str = "Asia/Kuala_Lumpur"
    cuisine_tags: tuple[str, ...] = ()
    opening_hours: tuple[OpeningInterval, ...] = ()
    hours_status: Literal["unknown", "published"] = "unknown"
    holiday_exceptions_known: bool = False
    opening_exceptions: tuple[OpeningException, ...] = ()
    opening_exceptions_coverage: OpeningExceptionCoverage | None = None
    halal: HalalEvidence = Field(default_factory=HalalEvidence)
    accessibility: AccessibilityEvidence = Field(default_factory=AccessibilityEvidence)
    source_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_outlet(self) -> Outlet:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError(
                "latitude and longitude must both be present or both unknown"
            )
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        if self.hours_status == "unknown" and self.opening_hours:
            raise ValueError("unknown hours cannot contain published intervals")
        if self.hours_status == "published" and not self.opening_hours:
            raise ValueError("published hours require at least one interval")
        if len({entry.on_date for entry in self.opening_exceptions}) != len(
            self.opening_exceptions
        ):
            raise ValueError("duplicate opening exception date")
        return self


class Price(Record):
    amount_minor: int = Field(
        ge=0, strict=True, description="Listed amount in integer sen"
    )
    currency: Literal["MYR"] = "MYR"
    channel: Literal["dine_in", "takeaway", "delivery", "unknown"]
    unit: Literal[
        "portion",
        "piece",
        "person",
        "set",
        "bottle",
        "glass",
        "pot",
        "weight_100g",
        "weight_kg",
        "unknown",
    ] = "unknown"
    minimum_quantity: int | None = Field(default=None, ge=1, strict=True)
    mandatory_charges_included: bool | None = None
    all_mandatory_charges_known: bool = False
    payable_amount_minor: int | None = Field(
        default=None,
        ge=0,
        strict=True,
        description="Payable amount per listed unit, including known mandatory charges; not the minimum-order total",
    )

    @model_validator(mode="after")
    def complete_total(self) -> Price:
        if self.all_mandatory_charges_known != (self.payable_amount_minor is not None):
            raise ValueError(
                "payable total is required exactly when all mandatory charges are known"
            )
        if (
            self.payable_amount_minor is not None
            and self.payable_amount_minor < self.amount_minor
        ):
            raise ValueError("payable total cannot be below the listed amount")
        if self.mandatory_charges_included is True and (
            not self.all_mandatory_charges_known
            or self.payable_amount_minor != self.amount_minor
        ):
            raise ValueError(
                "inclusive price must have a known total equal to listed amount"
            )
        return self


class MenuItem(Record):
    item_id: str = Field(min_length=1)
    outlet_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    variant: str = "Regular"
    language: str = "en"
    menu_version: str = Field(min_length=1)
    cuisine_tags: tuple[str, ...] = ()
    attributes: tuple[str, ...] = ()
    # Reviewed English/Malay/Chinese names and dish words (search and tie-breaks only;
    # never eligibility). The original name, variant and description are unchanged.
    name_translations: dict[Literal["en", "ms", "zh"], tuple[str, ...]] = Field(
        default_factory=dict
    )
    price: Price | None = None
    meal_role: Literal[
        "main", "set", "side", "dessert", "beverage", "add_on", "unknown"
    ] = "unknown"
    serves_min: int | None = Field(default=None, ge=1, strict=True)
    serves_max: int | None = Field(default=None, ge=1, strict=True)
    review_status: Literal["unreviewed", "reviewed", "quarantined"] = "unreviewed"
    review_reasons: tuple[str, ...] = ()
    ingredients: tuple[str, ...] = ()
    ingredients_complete: bool = False
    allergens_present: tuple[str, ...] = ()
    allergen_assessment: Literal["unknown", "published"] = "unknown"
    cross_contact_status: Literal[
        "unknown", "possible", "restaurant_confirmed_controlled"
    ] = "unknown"
    dietary_claims: tuple[str, ...] = ()
    live_availability: Literal["unknown", "available", "unavailable"] = "unknown"
    source_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_servings(self) -> MenuItem:
        if (self.serves_min is None) != (self.serves_max is None):
            raise ValueError(
                "serves_min and serves_max must both be known or both unknown"
            )
        if self.serves_min is not None and self.serves_max < self.serves_min:
            raise ValueError("serves_max cannot be less than serves_min")
        return self


class Catalog(Record):
    schema_version: Literal["1", "2"]
    catalog_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    generated_at: AwareDatetime
    synthetic: bool
    brands: tuple[Brand, ...]
    outlets: tuple[Outlet, ...]
    menu_items: tuple[MenuItem, ...]
    sources: tuple[Source, ...]

    @model_validator(mode="after")
    def referential_integrity(self) -> Catalog:
        def index(records: tuple, key: str) -> dict:
            result = {getattr(record, key): record for record in records}
            if len(result) != len(records):
                raise ValueError(f"duplicate {key}")
            return result

        brands = index(self.brands, "brand_id")
        outlets = index(self.outlets, "outlet_id")
        index(self.menu_items, "item_id")
        sources = index(self.sources, "source_id")
        if not self.synthetic and any(
            source.kind == "synthetic" for source in self.sources
        ):
            raise ValueError("real catalogs cannot include synthetic sources")
        if self.synthetic and any(
            source.kind != "synthetic" for source in self.sources
        ):
            raise ValueError("keep real and synthetic sources in separate catalogs")
        for record in (*self.outlets, *self.menu_items):
            for source_id in record.source_ids:
                if source_id not in sources:
                    raise ValueError(f"unknown source_id: {source_id}")
        for outlet in self.outlets:
            if outlet.brand_id not in brands:
                raise ValueError(f"unknown brand_id: {outlet.brand_id}")
            for source_id in outlet.halal.source_ids:
                if source_id not in sources:
                    raise ValueError(f"unknown halal source_id: {source_id}")
            service_ids = {
                sid
                for interval in outlet.opening_hours
                for sid in interval.last_order_source_ids
            }
            if outlet.opening_exceptions_coverage:
                service_ids.update(outlet.opening_exceptions_coverage.source_ids)
            for exception in outlet.opening_exceptions:
                service_ids.update(exception.source_ids)
                service_ids.update(
                    sid
                    for interval in exception.intervals
                    for sid in interval.last_order_source_ids
                )
            for source_id in service_ids:
                if source_id not in sources:
                    raise ValueError(f"unknown opening service source_id: {source_id}")
            if (
                outlet.halal.status == "certified"
                and not self.synthetic
                and not any(
                    sources[sid].kind == "certification_registry"
                    for sid in outlet.halal.source_ids
                )
            ):
                raise ValueError(
                    "certified halal evidence requires a certification_registry source"
                )
        for item in self.menu_items:
            if item.outlet_id not in outlets:
                raise ValueError(f"unknown outlet_id: {item.outlet_id}")
        return self

    def evidence_usable(
        self,
        source_ids: tuple[str, ...],
        purpose: Literal["display", "embed"] = "display",
        at: datetime | None = None,
    ) -> bool:
        sources = {source.source_id: source for source in self.sources}
        return bool(source_ids) and all(
            source_id in sources and sources[source_id].usable_for(purpose, at)
            for source_id in source_ids
        )


def load_catalog(path: Path | str, allow_synthetic: bool = False) -> Catalog:
    """Load UTF-8 JSON, validate links and refuse demo data unless explicitly enabled."""
    catalog = Catalog.model_validate_json(Path(path).read_text(encoding="utf-8"))
    if catalog.synthetic and not allow_synthetic:
        raise ValueError(
            "synthetic catalog requires allow_synthetic=True; never use it as real data"
        )
    return catalog
