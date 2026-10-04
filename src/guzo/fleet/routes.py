from fastapi import APIRouter
from pydantic import BaseModel, Field, HttpUrl
from pymongo.errors import DuplicateKeyError

from guzo.audit import service as audit
from guzo.catalog.models import VehicleClass
from guzo.common.ids import object_id
from guzo.errors import Conflict, NotFound
from guzo.fleet.models import Vehicle, normalize_plate
from guzo.identity.deps import Ops
from guzo.identity.models import Role, User, UserResponse

router = APIRouter(prefix="/ops/drivers", tags=["ops"])


class VehicleCreate(BaseModel):
    plate: str = Field(min_length=2, max_length=20)
    make: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=40)
    color: str = Field(min_length=1, max_length=30)
    vehicle_class: VehicleClass


class VehicleResponse(VehicleCreate):
    id: str
    driver_id: str
    active: bool


class DriverResponse(BaseModel):
    driver: UserResponse
    vehicles: list[VehicleResponse]


class DriverUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    photo_url: HttpUrl | None = None
    is_active: bool | None = None


def _vehicle(vehicle: Vehicle) -> VehicleResponse:
    return VehicleResponse(id=str(vehicle.id), **vehicle.model_dump(exclude={"id", "created_at"}))


async def _driver(driver_id: str) -> User:
    driver = await User.get(object_id(driver_id, "driver"))
    if driver is None or driver.role != Role.DRIVER:
        raise NotFound("driver not found")
    return driver


async def _with_vehicles(driver: User) -> DriverResponse:
    vehicles = await Vehicle.find(Vehicle.driver_id == str(driver.id)).to_list()
    return DriverResponse(
        driver=UserResponse.from_user(driver), vehicles=[_vehicle(v) for v in vehicles]
    )


@router.get("")
async def list_drivers(user: Ops, verified: bool | None = None) -> list[DriverResponse]:
    query: dict = {"role": Role.DRIVER.value}
    if verified is not None:
        query["is_verified"] = verified
    drivers = await User.find(query).sort("name").limit(500).to_list()
    return [await _with_vehicles(d) for d in drivers]


@router.post("/{driver_id}/verify")
async def verify_driver(driver_id: str, user: Ops) -> UserResponse:
    """Mark a driver as vetted. Only verified drivers can be assigned."""
    driver = await _driver(driver_id)
    driver.is_verified = True
    await driver.save()
    await audit.record(user, "driver.verify", "driver", driver_id)
    return UserResponse.from_user(driver)


@router.patch("/{driver_id}")
async def update_driver(driver_id: str, body: DriverUpdate, user: Ops) -> DriverResponse:
    driver = await _driver(driver_id)
    changes = body.model_dump(exclude_none=True, mode="json")
    for field, value in changes.items():
        setattr(driver, field, value)
    await driver.save()
    await audit.record(user, "driver.update", "driver", driver_id, changes)
    return await _with_vehicles(driver)


@router.post("/{driver_id}/vehicles", status_code=201)
async def add_vehicle(driver_id: str, body: VehicleCreate, user: Ops) -> VehicleResponse:
    driver = await _driver(driver_id)
    vehicle = Vehicle(
        driver_id=str(driver.id), **body.model_dump() | {"plate": normalize_plate(body.plate)}
    )
    try:
        await vehicle.insert()
    except DuplicateKeyError as exc:
        raise Conflict("this plate is already registered", code="plate_exists") from exc
    await audit.record(user, "vehicle.add", "driver", driver_id, {"plate": vehicle.plate})
    return _vehicle(vehicle)


@router.delete("/{driver_id}/vehicles/{vehicle_id}", status_code=204)
async def retire_vehicle(driver_id: str, vehicle_id: str, user: Ops) -> None:
    """Take a vehicle out of service. It stays on past bookings."""
    vehicle = await Vehicle.get(object_id(vehicle_id, "vehicle"))
    if vehicle is None or vehicle.driver_id != driver_id:
        raise NotFound("vehicle not found")
    vehicle.active = False
    await vehicle.save()
    await audit.record(user, "vehicle.retire", "driver", driver_id, {"plate": vehicle.plate})
