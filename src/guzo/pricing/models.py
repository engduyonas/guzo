from datetime import datetime

from beanie import Document
from pydantic import AwareDatetime, BaseModel, Field
from pymongo import IndexModel

from guzo.catalog.models import Place, VehicleClass
from guzo.common.clock import utcnow
from guzo.common.money import Money


class FlightInfo(BaseModel):
    number: str = Field(min_length=2, max_length=10)
    scheduled_arrival: AwareDatetime
    estimated_arrival: AwareDatetime | None = None


class TripRequest(BaseModel):
    """Product + time + places + party size, before a vehicle is chosen."""

    product_code: str
    city_id: str = "addis"
    pickup: Place
    dropoff: Place
    scheduled_at: AwareDatetime
    seats: int = Field(default=1, ge=1, le=50)
    bags: int = Field(default=0, ge=0, le=50)
    flight: FlightInfo | None = None


class QuoteRequest(TripRequest):
    """Everything a pricing strategy may need."""

    vehicle_class: VehicleClass


class Quote(Document):
    booker_id: str
    request: QuoteRequest
    price: Money
    expires_at: datetime
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "quotes"
        indexes = [IndexModel([("booker_id", 1), ("created_at", -1)])]
