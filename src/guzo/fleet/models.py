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
            IndexModel([("plate", 1)], unique=True),
            IndexModel([("driver_id", 1)]),
        ]
