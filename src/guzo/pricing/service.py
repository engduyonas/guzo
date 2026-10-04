from datetime import timedelta

from pydantic import BaseModel

from guzo.catalog.models import Product, VehicleCapacity, VehicleClass
from guzo.common.clock import utcnow
from guzo.common.money import Money
from guzo.config import get_settings
from guzo.errors import NotFound, Unprocessable
from guzo.pricing.models import Quote, QuoteRequest, TripRequest
from guzo.pricing.strategies import STRATEGIES


class VehicleOption(BaseModel):
    vehicle_class: VehicleClass
    max_seats: int | None
    max_bags: int | None
    price: Money


async def _product_for(request: TripRequest) -> Product:
    product = await Product.find_one(
        Product.city_id == request.city_id, Product.code == request.product_code
    )
    if product is None or not product.active:
        raise NotFound("product not found", code="unknown_product")
    if request.scheduled_at <= utcnow():
        raise Unprocessable("scheduled_at must be in the future", code="scheduled_in_past")
    return product


async def _capacities(city_id: str) -> dict[VehicleClass, VehicleCapacity]:
    rows = await VehicleCapacity.find(VehicleCapacity.city_id == city_id).to_list()
    return {row.vehicle_class: row for row in rows}


def _fits(capacity: VehicleCapacity | None, request: TripRequest) -> bool:
    return capacity is None or (
        request.seats <= capacity.max_seats and request.bags <= capacity.max_bags
    )


async def vehicle_options(request: TripRequest) -> list[VehicleOption]:
    """The vehicle classes that fit this party and have a published price, cheapest first."""
    product = await _product_for(request)
    capacities = await _capacities(request.city_id)
    options = []
    for vehicle_class in product.vehicle_classes:
        capacity = capacities.get(vehicle_class)
        if not _fits(capacity, request):
            continue
        full = QuoteRequest(**request.model_dump(), vehicle_class=vehicle_class)
        try:
            price = await STRATEGIES[product.pricing_strategy].quote(full, product)
        except Unprocessable as exc:
            if exc.code == "no_price":
                continue  # this class is simply not sold for the zone
            raise
        options.append(
            VehicleOption(
                vehicle_class=vehicle_class,
                max_seats=capacity.max_seats if capacity else None,
                max_bags=capacity.max_bags if capacity else None,
                price=price,
            )
        )
    return sorted(options, key=lambda option: option.price.amount_minor)


async def create_quote(booker_id: str, request: QuoteRequest) -> Quote:
    now = utcnow()
    product = await _product_for(request)
    if request.vehicle_class not in product.vehicle_classes:
        raise Unprocessable("vehicle class not offered for this product", code="vehicle_class")
    capacity = (await _capacities(request.city_id)).get(request.vehicle_class)
    if not _fits(capacity, request):
        raise Unprocessable("too many riders or bags for this vehicle class", code="over_capacity")
    price = await STRATEGIES[product.pricing_strategy].quote(request, product)
    quote = Quote(
        booker_id=booker_id,
        request=request,
        price=price,
        expires_at=now + timedelta(minutes=get_settings().quote_ttl_minutes),
        created_at=now,
    )
    await quote.insert()
    return quote
