"""Operations edit the catalogue here: prices, policies, zones and vehicle capacity.

Nothing a customer is charged or refunded comes from code. It all lives in these records.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from guzo.audit import service as audit
from guzo.catalog.models import (
    City,
    Product,
    ProductPolicy,
    VehicleCapacity,
    VehicleClass,
    Zone,
    ZonePrice,
)
from guzo.catalog.routes import ProductResponse, ZoneResponse
from guzo.common.ids import object_id
from guzo.common.money import Money
from guzo.errors import NotFound, Unprocessable
from guzo.identity.deps import Ops

router = APIRouter(prefix="/ops/catalog", tags=["ops"])


class ZonePriceBody(BaseModel):
    product_code: str
    city_id: str = "addis"
    zone_id: str
    vehicle_class: VehicleClass
    price: Money


class ZonePriceResponse(ZonePriceBody):
    id: str


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    policy: ProductPolicy | None = None
    vehicle_classes: list[VehicleClass] | None = Field(default=None, min_length=1)
    active: bool | None = None


class ZoneBody(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9_]{2,40}$")
    city_id: str = "addis"
    name: str = Field(min_length=1, max_length=80)


class VehicleCapacityBody(BaseModel):
    city_id: str = "addis"
    vehicle_class: VehicleClass
    max_seats: int = Field(ge=1, le=60)
    max_bags: int = Field(ge=0, le=60)


def _price(row: ZonePrice) -> ZonePriceResponse:
    return ZonePriceResponse(id=str(row.id), **row.model_dump(exclude={"id"}))


async def _product(city_id: str, code: str) -> Product:
    product = await Product.find_one(Product.city_id == city_id, Product.code == code)
    if product is None:
        raise NotFound("product not found", code="unknown_product")
    return product


@router.get("/zone-prices")
async def list_zone_prices(
    user: Ops, city_id: str = "addis", product_code: str | None = None
) -> list[ZonePriceResponse]:
    query = {"city_id": city_id} | ({"product_code": product_code} if product_code else {})
    return [_price(r) for r in await ZonePrice.find(query).sort("zone_id").to_list()]


@router.put("/zone-prices")
async def set_zone_price(body: ZonePriceBody, user: Ops) -> ZonePriceResponse:
    """Publish or change the fixed price for a zone and vehicle class."""
    product = await _product(body.city_id, body.product_code)
    if await Zone.find_one(Zone.city_id == body.city_id, Zone.code == body.zone_id) is None:
        raise Unprocessable(f"unknown zone {body.zone_id!r}", code="unknown_zone")
    if body.vehicle_class not in product.vehicle_classes:
        raise Unprocessable("vehicle class not offered for this product", code="vehicle_class")
    if body.price.amount_minor <= 0:
        raise Unprocessable("price must be positive", code="invalid_price")
    row = await ZonePrice.find_one(
        ZonePrice.city_id == body.city_id,
        ZonePrice.product_code == body.product_code,
        ZonePrice.zone_id == body.zone_id,
        ZonePrice.vehicle_class == body.vehicle_class,
    )
    previous = row.price.model_dump(mode="json") if row else None
    if row is None:
        row = ZonePrice(**body.model_dump())
    else:
        row.price = body.price
    await row.save()
    await audit.record(
        user,
        "zone_price.set",
        "zone_price",
        str(row.id),
        body.model_dump(mode="json") | {"previous_price": previous},
    )
    return _price(row)


@router.delete("/zone-prices/{price_id}", status_code=204)
async def delete_zone_price(price_id: str, user: Ops) -> None:
    """Stop selling a zone and vehicle class. Existing quotes and bookings keep their price."""
    row = await ZonePrice.get(object_id(price_id, "zone price"))
    if row is None:
        raise NotFound("zone price not found")
    await row.delete()
    await audit.record(
        user,
        "zone_price.delete",
        "zone_price",
        price_id,
        row.model_dump(mode="json", exclude={"id"}),
    )


@router.patch("/products/{code}")
async def update_product(
    code: str, body: ProductUpdate, user: Ops, city_id: str = "addis"
) -> ProductResponse:
    """Change a product's policy or vehicle options. Bookings already made keep their terms."""
    product = await _product(city_id, code)
    for field in body.model_fields_set:
        value = getattr(body, field)
        if value is not None:
            setattr(product, field, value)
    await product.save()
    await audit.record(
        user,
        "product.update",
        "product",
        f"{city_id}:{code}",
        body.model_dump(mode="json", exclude_none=True),
    )
    return ProductResponse(**product.model_dump(exclude={"id", "active"}))


@router.put("/zones")
async def set_zone(body: ZoneBody, user: Ops) -> ZoneResponse:
    if await City.find_one(City.code == body.city_id) is None:
        raise Unprocessable(f"unknown city {body.city_id!r}", code="unknown_city")
    zone = await Zone.find_one(Zone.city_id == body.city_id, Zone.code == body.code)
    if zone is None:
        zone = Zone(**body.model_dump())
    else:
        zone.name = body.name
    await zone.save()
    await audit.record(user, "zone.set", "zone", f"{body.city_id}:{body.code}", body.model_dump())
    return ZoneResponse(**zone.model_dump(exclude={"id"}))


@router.put("/vehicle-capacities")
async def set_vehicle_capacity(body: VehicleCapacityBody, user: Ops) -> VehicleCapacityBody:
    """How many riders and bags a vehicle class takes. Quotes refuse anything larger."""
    row = await VehicleCapacity.find_one(
        VehicleCapacity.city_id == body.city_id,
        VehicleCapacity.vehicle_class == body.vehicle_class,
    )
    if row is None:
        row = VehicleCapacity(**body.model_dump())
    else:
        row.max_seats, row.max_bags = body.max_seats, body.max_bags
    await row.save()
    await audit.record(
        user,
        "vehicle_capacity.set",
        "vehicle_capacity",
        f"{body.city_id}:{body.vehicle_class.value}",
        body.model_dump(mode="json"),
    )
    return body
