"""Reactions to domain events. Import this module wherever the outbox is delivered.

Booking code never calls a notifier or a provider refund directly: it writes an event
and these handlers do the rest, so new reactions do not touch booking logic.
"""

from beanie import PydanticObjectId

from guzo.bookings.models import Booking
from guzo.bookings.state_machine import BookingStatus
from guzo.events.models import OutboxEvent
from guzo.events.outbox import on
from guzo.identity.models import User
from guzo.payments.refunds import REFUND_REQUESTED
from guzo.payments.service import process_refund
from guzo.providers.registry import get_providers

S = BookingStatus

# What the booker is told at each step. Riders are not account holders, so they only
# get the pickup details. Copy is English for now; translations come with the app.
BOOKER_MESSAGES: dict[BookingStatus, str] = {
    S.CONFIRMED: "Guzo: payment received. Your booking {ref} is confirmed.",
    S.ASSIGNED: "Guzo: {driver} will be your driver for booking {ref}.",
    S.EN_ROUTE: "Guzo: your driver is on the way for booking {ref}.",
    S.ARRIVED: "Guzo: your driver has arrived at {pickup}.",
    S.IN_PROGRESS: "Guzo: the rider is on board for booking {ref}.",
    S.COMPLETED: "Guzo: booking {ref} is complete. Thank you for riding with us.",
    S.CANCELLED: "Guzo: booking {ref} was cancelled.",
    S.EXPIRED: "Guzo: booking {ref} expired before it was paid.",
    S.NO_SHOW: "Guzo: the driver could not find the rider for booking {ref}.",
}


async def _load(event: OutboxEvent) -> tuple[Booking, User | None]:
    booking = await Booking.get(PydanticObjectId(event.payload["booking_id"]))
    driver = None
    if booking.assignment is not None:
        driver = await User.get(PydanticObjectId(booking.assignment.driver_id))
    return booking, driver


def _ref(booking: Booking) -> str:
    return str(booking.id)[-6:].upper()


@on(*(f"booking.{status.value}" for status in BOOKER_MESSAGES))
async def notify_booker(event: OutboxEvent) -> None:
    booking, driver = await _load(event)
    booker = await User.get(PydanticObjectId(booking.booker_id))
    if booker is None or booker.phone is None:
        return
    # A dropout sends the booking back to confirmed; do not announce payment again.
    if event.payload["to"] == S.CONFIRMED.value and event.payload["from"] == S.ASSIGNED.value:
        return
    body = BOOKER_MESSAGES[BookingStatus(event.payload["to"])].format(
        ref=_ref(booking),
        pickup=booking.pickup.label,
        driver=(driver.name if driver and driver.name else "A Guzo driver"),
    )
    await get_providers().notifier.send(to=booker.phone, body=body)


@on("booking.assigned")
async def send_pickup_details(event: OutboxEvent) -> None:
    """Riders get the driver and meeting point by SMS, before they travel."""
    booking, driver = await _load(event)
    if driver is None:
        return
    notifier = get_providers().notifier
    when = booking.scheduled_at.strftime("%d %b %Y %H:%M UTC")
    for rider in booking.riders:
        if rider.is_booker:
            continue  # already told by notify_booker
        await notifier.send(
            to=rider.phone,
            body=(
                f"Guzo: {driver.name or 'Your driver'} ({driver.phone}) will meet you at "
                f"{booking.pickup.label} on {when}. Booking {_ref(booking)}."
            ),
        )
    if driver.phone:
        names = ", ".join(r.name for r in booking.riders)
        await notifier.send(
            to=driver.phone,
            body=(
                f"Guzo job {_ref(booking)}: {booking.pickup.label} to {booking.dropoff.label} "
                f"on {when}. Riders: {names}."
            ),
        )


@on(REFUND_REQUESTED)
async def send_refund(event: OutboxEvent) -> None:
    await process_refund(event.payload["refund_id"])
