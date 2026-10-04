from datetime import datetime

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import IndexModel

from guzo.bookings.state_machine import Actor, BookingStatus
from guzo.catalog.models import Place, ProductPolicy, VehicleClass
from guzo.common.clock import utcnow
from guzo.common.money import Money
from guzo.common.phone import PhoneNumber
from guzo.pricing.models import FlightInfo


class Rider(BaseModel):
    """Someone who gets picked up. Riders do not need an account or the app."""

    name: str = Field(min_length=1, max_length=120)
    phone: PhoneNumber
    is_booker: bool = False


class StatusChange(BaseModel):
    from_status: BookingStatus | None
    to_status: BookingStatus
    at: datetime
    by: str | None  # user id; None for the system
    actor: Actor
    reason: str | None = None


class Assignment(BaseModel):
    driver_id: str
    vehicle_id: str | None = None
    assigned_at: datetime
    accepted_at: datetime | None = None


class Booking(Document):
    product_code: str
    city_id: str
    booker_id: str  # pays for and manages the booking
    riders: list[Rider]
    pickup: Place
    dropoff: Place
    scheduled_at: datetime  # UTC
    flight: FlightInfo | None = None
    vehicle_class: VehicleClass
    seats: int
    bags: int
    price: Money
    policy: ProductPolicy  # snapshot of the product policy at booking time
    quote_id: str | None = None
    listing_id: str | None = None  # M4 marketplace
    partner_code: str | None = None
    partner_commission_pct: int | None = None  # the partner's rate when this was booked
    status: BookingStatus
    status_history: list[StatusChange] = Field(default_factory=list)
    assignment: Assignment | None = None
    quote_expires_at: datetime | None = None
    payment_expires_at: datetime | None = None
    idempotency_key: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "bookings"
        indexes = [
            IndexModel([("booker_id", 1), ("created_at", -1)]),
            IndexModel([("status", 1), ("scheduled_at", 1)]),
            IndexModel([("assignment.driver_id", 1), ("scheduled_at", 1)]),
            IndexModel([("partner_code", 1), ("status", 1)]),
            IndexModel(
                [("quote_id", 1)],
                unique=True,
                partialFilterExpression={"quote_id": {"$type": "string"}},
            ),
            IndexModel(
                [("booker_id", 1), ("idempotency_key", 1)],
                unique=True,
                partialFilterExpression={"idempotency_key": {"$type": "string"}},
            ),
        ]
