"""Versioned model pricing configuration and token cost accounting."""

from __future__ import annotations

from dataclasses import dataclass

from dining.core.constants import DEFAULT_ILMU_MODEL

DEFAULT_PRICE_VERSION = "2026-10"


@dataclass(frozen=True)
class ModelPrice:
    """Per-million-token prices for one provider/model/price version."""

    provider: str
    model: str
    price_version: str
    input_cost_per_million: float
    output_cost_per_million: float
    currency: str = "USD"

    @property
    def input_cost_per_token(self) -> float:
        """Input price per token."""
        return self.input_cost_per_million / 1_000_000.0

    @property
    def output_cost_per_token(self) -> float:
        """Output price per token."""
        return self.output_cost_per_million / 1_000_000.0


@dataclass(frozen=True)
class CostCalculation:
    """Estimated cost of one model call (None fields when unknown)."""

    estimated_cost: float | None
    currency: str | None
    price_version: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


class PricingCatalog:
    """Known model prices, keyed by price version, provider and model."""

    def __init__(self):
        self._prices: dict[tuple[str, str, str], ModelPrice] = {}
        self._load_defaults()

    def _load_defaults(self) -> None:
        defaults = [
            ModelPrice(
                provider="ilmu",
                model=DEFAULT_ILMU_MODEL,
                price_version="2026-10",
                input_cost_per_million=0.15,
                output_cost_per_million=0.60,
                currency="USD",
            ),
            ModelPrice(
                provider="ollama",
                model="*",
                price_version="2026-10",
                input_cost_per_million=0.0,
                output_cost_per_million=0.0,
                currency="USD",
            ),
            ModelPrice(
                provider="openai_compatible",
                model="gpt-4o-mini",
                price_version="2026-10",
                input_cost_per_million=0.15,
                output_cost_per_million=0.60,
                currency="USD",
            ),
            ModelPrice(
                provider="openai_compatible",
                model="gpt-4o",
                price_version="2026-10",
                input_cost_per_million=2.50,
                output_cost_per_million=10.00,
                currency="USD",
            ),
        ]
        for price in defaults:
            self.register(price)

    def register(self, price: ModelPrice) -> None:
        """Add or replace a price."""
        key = (price.price_version, price.provider, price.model)
        self._prices[key] = price

    def get_price(
        self, provider: str, model: str, price_version: str
    ) -> ModelPrice | None:
        """Exact model price, else the provider's wildcard price for that version."""
        key = (price_version, provider, model)
        if key in self._prices:
            return self._prices[key]
        wildcard_key = (price_version, provider, "*")
        return self._prices.get(wildcard_key)


_DEFAULT_PRICING_CATALOG: PricingCatalog | None = None


def get_pricing_catalog() -> PricingCatalog:
    """The process-wide pricing catalog."""
    global _DEFAULT_PRICING_CATALOG
    if _DEFAULT_PRICING_CATALOG is None:
        _DEFAULT_PRICING_CATALOG = PricingCatalog()
    return _DEFAULT_PRICING_CATALOG


def calculate_cost(
    provider: str | None,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    price_version: str | None = None,
    catalog: PricingCatalog | None = None,
) -> CostCalculation:
    """Calculate estimated cost only when provider, model, price version and usage are known."""
    total_tokens: int | None = None
    if (
        type(input_tokens) is int
        and input_tokens >= 0
        and type(output_tokens) is int
        and output_tokens >= 0
    ):
        total_tokens = input_tokens + output_tokens
    else:
        return CostCalculation(
            estimated_cost=None,
            currency=None,
            price_version=None,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
        )

    if not provider or not model or not price_version:
        return CostCalculation(
            estimated_cost=None,
            currency=None,
            price_version=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

    cat = catalog or get_pricing_catalog()
    price = cat.get_price(provider, model, price_version)
    if not price:
        return CostCalculation(
            estimated_cost=None,
            currency=None,
            price_version=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

    cost = (input_tokens * price.input_cost_per_token) + (
        output_tokens * price.output_cost_per_token
    )
    return CostCalculation(
        estimated_cost=round(cost, 8),
        currency=price.currency,
        price_version=price.price_version,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )
