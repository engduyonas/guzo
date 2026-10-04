"""One interface, one implementation per pricing strategy.

The booking flow calls `quote()` and never knows which strategy ran. Only zone-fixed
is implemented in M0; the others are declared so later products slot in here.
"""

from abc import ABC, abstractmethod

from guzo.catalog.models import PlaceKind, PricingStrategyName, Product, Zone, ZonePrice
from guzo.common.money import Money
from guzo.errors import Unprocessable
from guzo.pricing.models import QuoteRequest


class PricingStrategy(ABC):
    name: PricingStrategyName

    @abstractmethod
    async def quote(self, request: QuoteRequest, product: Product) -> Money: ...


class ZoneFixedPricing(PricingStrategy):
    """Published price between an airport and a zone, per vehicle class."""

    name = PricingStrategyName.ZONE_FIXED

    async def quote(self, request: QuoteRequest, product: Product) -> Money:
        ends = (request.pickup, request.dropoff)
        airports = [p for p in ends if p.kind == PlaceKind.AIRPORT]
        if len(airports) != 1:
            raise Unprocessable(
                "exactly one of pickup and dropoff must be an airport", code="airport_required"
            )
        other = next(p for p in ends if p.kind != PlaceKind.AIRPORT)
        if other.zone_id is None:
            raise Unprocessable("the non-airport place needs a zone_id", code="zone_required")
        zone = await Zone.find_one(Zone.city_id == request.city_id, Zone.code == other.zone_id)
        if zone is None:
            raise Unprocessable(f"unknown zone {other.zone_id!r}", code="unknown_zone")
        row = await ZonePrice.find_one(
            ZonePrice.city_id == request.city_id,
            ZonePrice.product_code == product.code,
            ZonePrice.zone_id == zone.code,
            ZonePrice.vehicle_class == request.vehicle_class,
        )
        if row is None:
            raise Unprocessable("no price published for this zone and vehicle", code="no_price")
        return row.price


class NotYetAvailable(PricingStrategy):
    def __init__(self, name: PricingStrategyName):
        self.name = name

    async def quote(self, request: QuoteRequest, product: Product) -> Money:
        raise Unprocessable(
            f"{self.name.value} pricing is not available yet", code="pricing_unavailable"
        )


STRATEGIES: dict[PricingStrategyName, PricingStrategy] = {
    PricingStrategyName.ZONE_FIXED: ZoneFixedPricing(),
    **{
        name: NotYetAvailable(name)
        for name in (
            PricingStrategyName.PER_SEAT,
            PricingStrategyName.HOURLY,
            PricingStrategyName.QUOTED,
            PricingStrategyName.OFFER,
        )
    },
}
