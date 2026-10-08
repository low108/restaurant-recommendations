"""HTTP route groups. Each module exposes ``register(router, ctx)``."""

from dining.api.routes import accounts, adaptive, decisions, learning, meals, rooms

# Registration order of the route groups on the API router.
ROUTE_MODULES = (accounts, rooms, meals, adaptive, decisions, learning)

__all__ = ["ROUTE_MODULES"]
