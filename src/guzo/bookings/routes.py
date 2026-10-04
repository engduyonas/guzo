from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from guzo.bookings import service
from guzo.bookings.models import Assignment, Booking, Rider, StatusChange
from guzo.bookings.state_machine import Actor, BookingStatus
from guzo.catalog.models import Place, VehicleClass
from guzo.common.money import Money
from guzo.errors import NotFound
from guzo.identity.deps import Booker, Driver, Ops
from guzo.payments.models import Payment, PaymentStatus, Refund, RefundStatus
from guzo.pricing.models import FlightInfo

S = BookingStatus

router = APIRouter(prefix="/bookings", tags=["bookings"])
driver_router = APIRouter(prefix="/driver", tags=["driver"])
ops_router = APIRouter(prefix="/ops", tags=["ops"])


class BookingResponse(BaseModel):
    id: str
    product_code: str
    city_id: str
    booker_id: str
    riders: list[Rider]
    pickup: Place
    dropoff: Place
    scheduled_at: datetime
    flight: FlightInfo | None
    vehicle_class: VehicleClass
    seats: int
    bags: int
    price: Money
    partner_code: str | None
    status: BookingStatus
    status_history: list[StatusChange]
    assignment: Assignment | None
    quote_expires_at: datetime | None
    payment_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_booking(cls, booking: Booking) -> "BookingResponse":
        return cls(id=str(booking.id), **booking.model_dump(include=set(cls.model_fields) - {"id"}))


class PaymentResponse(BaseModel):
    id: str
    provider: str
    provider_ref: str | None
    checkout_url: str | None
    amount: Money
    status: PaymentStatus

    @classmethod
    def from_payment(cls, payment: Payment) -> "PaymentResponse":
        return cls(id=str(payment.id), **payment.model_dump(include=set(cls.model_fields) - {"id"}))


class BookingCreate(BaseModel):
    quote_id: str
    riders: list[Rider] = Field(min_length=1)
    partner_code: str | None = Field(default=None, max_length=40)


class ConfirmResponse(BaseModel):
    booking: BookingResponse
    payment: PaymentResponse


class Reason(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class AssignRequest(BaseModel):
    driver_id: str
    vehicle_id: str | None = None


def _out(booking: Booking) -> BookingResponse:
    return BookingResponse.from_booking(booking)


# --- Booker -----------------------------------------------------------------


@router.post("", status_code=201)
async def create_booking(
    body: BookingCreate,
    user: Booker,
    idempotency_key: Annotated[str | None, Header(max_length=100)] = None,
) -> BookingResponse:
    """Book a quoted price for one or more riders. Send Idempotency-Key to retry safely."""
    booking = await service.create_booking(
        user,
        quote_id=body.quote_id,
        riders=body.riders,
        partner_code=body.partner_code,
        idempotency_key=idempotency_key,
    )
    return _out(booking)


@router.get("")
async def list_my_bookings(user: Booker) -> list[BookingResponse]:
    bookings = (
        await Booking.find(Booking.booker_id == str(user.id))
        .sort("-created_at")
        .limit(100)
        .to_list()
    )
    return [_out(b) for b in bookings]


@router.get("/{booking_id}")
async def get_booking(booking_id: str, user: Booker) -> BookingResponse:
    booking = await service.get_booking(booking_id)
    if booking.booker_id != str(user.id):
        raise NotFound("booking not found")
    return _out(booking)


@router.post("/{booking_id}/confirm")
async def confirm_booking(booking_id: str, user: Booker) -> ConfirmResponse:
    """Accept the price and open a payment. Repeat calls return the same payment."""
    booking, payment = await service.confirm_booking(user, booking_id)
    return ConfirmResponse(booking=_out(booking), payment=PaymentResponse.from_payment(payment))


@router.post("/{booking_id}/cancel")
async def cancel_booking(booking_id: str, body: Reason, user: Booker) -> BookingResponse:
    return _out(
        await service.cancel_booking(booking_id, actor=Actor.BOOKER, by=user, reason=body.reason)
    )


# --- Driver -----------------------------------------------------------------


@driver_router.get("/bookings")
async def list_my_jobs(user: Driver) -> list[BookingResponse]:
    jobs = (
        await Booking.find({"assignment.driver_id": str(user.id)})
        .sort("scheduled_at")
        .limit(100)
        .to_list()
    )
    return [_out(b) for b in jobs]


@driver_router.post("/bookings/{booking_id}/accept")
async def accept_job(booking_id: str, user: Driver) -> BookingResponse:
    return _out(await service.driver_accept(booking_id, user))


@driver_router.post("/bookings/{booking_id}/drop")
async def drop_job(booking_id: str, body: Reason, user: Driver) -> BookingResponse:
    """Give the job back. The booking returns to confirmed for ops to reassign."""
    return _out(
        await service.unassign_driver(booking_id, actor=Actor.DRIVER, by=user, reason=body.reason)
    )


@driver_router.post("/bookings/{booking_id}/start")
async def start_trip(booking_id: str, user: Driver) -> BookingResponse:
    return _out(await service.driver_advance(booking_id, user, S.EN_ROUTE))


@driver_router.post("/bookings/{booking_id}/arrive")
async def arrive(booking_id: str, user: Driver) -> BookingResponse:
    return _out(await service.driver_advance(booking_id, user, S.ARRIVED))


@driver_router.post("/bookings/{booking_id}/pickup")
async def pick_up(booking_id: str, user: Driver) -> BookingResponse:
    return _out(await service.driver_advance(booking_id, user, S.IN_PROGRESS))


@driver_router.post("/bookings/{booking_id}/complete")
async def complete(booking_id: str, user: Driver) -> BookingResponse:
    return _out(await service.driver_advance(booking_id, user, S.COMPLETED))


@driver_router.post("/bookings/{booking_id}/no-show")
async def driver_no_show(booking_id: str, user: Driver) -> BookingResponse:
    """Only once the product's free waiting time has passed."""
    return _out(await service.mark_no_show(booking_id, actor=Actor.DRIVER, by=user))


# --- Operations -------------------------------------------------------------


@ops_router.get("/bookings")
async def list_bookings(user: Ops, status: BookingStatus | None = None) -> list[BookingResponse]:
    query = {"status": status.value} if status else {}
    bookings = await Booking.find(query).sort("scheduled_at").limit(200).to_list()
    return [_out(b) for b in bookings]


@ops_router.get("/bookings/{booking_id}")
async def ops_get_booking(booking_id: str, user: Ops) -> BookingResponse:
    return _out(await service.get_booking(booking_id))


@ops_router.post("/bookings/{booking_id}/assign")
async def assign(booking_id: str, body: AssignRequest, user: Ops) -> BookingResponse:
    return _out(
        await service.assign_driver(
            booking_id, ops=user, driver_id=body.driver_id, vehicle_id=body.vehicle_id
        )
    )


@ops_router.post("/bookings/{booking_id}/unassign")
async def unassign(booking_id: str, body: Reason, user: Ops) -> BookingResponse:
    return _out(
        await service.unassign_driver(booking_id, actor=Actor.OPS, by=user, reason=body.reason)
    )


@ops_router.post("/bookings/{booking_id}/cancel")
async def ops_cancel(booking_id: str, body: Reason, user: Ops) -> BookingResponse:
    return _out(
        await service.cancel_booking(booking_id, actor=Actor.OPS, by=user, reason=body.reason)
    )


@ops_router.post("/bookings/{booking_id}/no-show")
async def ops_no_show(booking_id: str, user: Ops) -> BookingResponse:
    return _out(await service.mark_no_show(booking_id, actor=Actor.OPS, by=user))


class RefundResponse(BaseModel):
    id: str
    amount: Money
    reason: str
    status: RefundStatus


class BookingMoney(BaseModel):
    payments: list[PaymentResponse]
    refunds: list[RefundResponse]


@ops_router.get("/bookings/{booking_id}/money")
async def booking_money(booking_id: str, user: Ops) -> BookingMoney:
    """Every payment attempt and refund for a booking."""
    booking = await service.get_booking(booking_id)
    payments = await Payment.find(Payment.booking_id == str(booking.id)).to_list()
    refunds = await Refund.find(Refund.booking_id == str(booking.id)).to_list()
    return BookingMoney(
        payments=[PaymentResponse.from_payment(p) for p in payments],
        refunds=[
            RefundResponse(id=str(r.id), amount=r.amount, reason=r.reason, status=r.status)
            for r in refunds
        ],
    )
