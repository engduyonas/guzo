from fastapi import APIRouter
from pydantic import BaseModel

from guzo.catalog.models import (
    KnownPlace,
    Place,
    PricingStrategyName,
    Product,
    ProductPolicy,
    VehicleClass,
    Zone,
)

router = APIRouter(prefix="/catalog", tags=["catalog"])


class ProductResponse(BaseModel):
    code: str
    city_id: str
    name: str
    pricing_strategy: PricingStrategyName
    policy: ProductPolicy
    vehicle_classes: list[VehicleClass]


class ZoneResponse(BaseModel):
    code: str
    city_id: str
    name: str


class KnownPlaceResponse(BaseModel):
    code: str
    city_id: str
    place: Place


@router.get("/products")
async def list_products(city_id: str = "addis") -> list[ProductResponse]:
    products = await Product.find(Product.city_id == city_id, Product.active == True).to_list()  # noqa: E712
    return [ProductResponse(**p.model_dump(exclude={"id", "active"})) for p in products]


@router.get("/zones")
async def list_zones(city_id: str = "addis") -> list[ZoneResponse]:
    zones = await Zone.find(Zone.city_id == city_id).sort("name").to_list()
    return [ZoneResponse(**z.model_dump(exclude={"id"})) for z in zones]


@router.get("/places")
async def list_places(city_id: str = "addis") -> list[KnownPlaceResponse]:
    places = await KnownPlace.find(KnownPlace.city_id == city_id).to_list()
    return [KnownPlaceResponse(**p.model_dump(exclude={"id"})) for p in places]
