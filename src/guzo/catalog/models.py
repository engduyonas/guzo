from enum import StrEnum

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import IndexModel

from guzo.common.money import Currency, Money


class PlaceKind(StrEnum):
    AIRPORT = "airport"
    ADDRESS = "address"
    LANDMARK = "landmark"


class Place(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    kind: PlaceKind
    zone_id: str | None = None


class VehicleClass(StrEnum):
    SEDAN = "sedan"
    MINIVAN = "minivan"
    SUV = "suv"


class PricingStrategyName(StrEnum):
    ZONE_FIXED = "zone_fixed"
    PER_SEAT = "per_seat"
    HOURLY = "hourly"
    QUOTED = "quoted"
    OFFER = "offer"


class ProductPolicy(BaseModel):
    """Per-product rules. Bookings keep a snapshot, so edits only affect new bookings."""

    free_cancel_hours: int = Field(ge=0)
    free_wait_minutes: int = Field(ge=0)
    no_show_fee_pct: int = Field(ge=0, le=100)
    # Share of the fare kept when cancelling inside the free-cancel window.
    late_cancel_fee_pct: int = Field(ge=0, le=100)


class City(Document):
    code: str
    name: str
    timezone: str
    default_currency: Currency

    class Settings:
        name = "cities"
        indexes = [IndexModel([("code", 1)], unique=True)]


class Zone(Document):
    code: str
    city_id: str
    name: str

    class Settings:
        name = "zones"
        indexes = [IndexModel([("city_id", 1), ("code", 1)], unique=True)]


class KnownPlace(Document):
    """A named place bookers can pick without typing an address, e.g. Bole airport."""

    code: str
    city_id: str
    place: Place

    class Settings:
        name = "known_places"
        indexes = [IndexModel([("city_id", 1), ("code", 1)], unique=True)]


class Product(Document):
    """Catalogue entry. Products are data: a new service is a new row, not new code."""

    code: str
    city_id: str
    name: str
    pricing_strategy: PricingStrategyName
    policy: ProductPolicy
    vehicle_classes: list[VehicleClass]
    active: bool = True

    class Settings:
        name = "products"
        indexes = [IndexModel([("city_id", 1), ("code", 1)], unique=True)]


class ZonePrice(Document):
    """Fixed price between the city's airport and a zone, per vehicle class."""

    product_code: str
    city_id: str
    zone_id: str
    vehicle_class: VehicleClass
    price: Money

    class Settings:
        name = "zone_prices"
        indexes = [
            IndexModel(
                [("city_id", 1), ("product_code", 1), ("zone_id", 1), ("vehicle_class", 1)],
                unique=True,
            )
        ]
