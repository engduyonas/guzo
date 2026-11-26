from datetime import datetime

from beanie import Document
from pydantic import Field
from pymongo import IndexModel

from guzo.catalog.models import VehicleClass
from guzo.common.clock import utcnow


def normalize_plate(plate: str) -> str:
    return " ".join(plate.upper().split())


class Vehicle(Document):
    """A car a driver brings. Riders are told the plate, make and colour before pickup."""

    driver_id: str
    plate: str
    make: str
    model: str
    color: str
    vehicle_class: VehicleClass
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "vehicles"
        indexes = [
            # A plate is unique among cars in service; a retired car can be added again.
            IndexModel(
                [("plate", 1)],
                unique=True,
                partialFilterExpression={"active": True},
                name="plate_in_service",
            ),
            IndexModel([("driver_id", 1)]),
        ]
