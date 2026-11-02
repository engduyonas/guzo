"""What a driver sees for a job: the card text and the buttons for its next step."""

from zoneinfo import ZoneInfo

from guzo.bookings.models import Booking
from guzo.bookings.state_machine import BookingStatus
from guzo.catalog.models import City
from guzo.providers.base import Button

S = BookingStatus

HEADLINES: dict[BookingStatus, str] = {
    S.ASSIGNED: "Accepted. Start the trip when you leave for pickup.",
    S.EN_ROUTE: "On the way to pickup.",
    S.ARRIVED: "At pickup, waiting for the rider.",
    S.IN_PROGRESS: "Rider on board.",
    S.COMPLETED: "Trip complete. Thank you.",
    S.CANCELLED: "Cancelled. Do not go to pickup.",
    S.NO_SHOW: "Marked as a no-show.",
}


def ref(booking: Booking) -> str:
    return str(booking.id)[-6:].upper()


async def local_time(booking: Booking) -> str:
    """Pickup time in the city's own timezone, the way drivers and riders read it."""
    city = await City.find_one(City.code == booking.city_id)
    zone = ZoneInfo(city.timezone if city else "UTC")
    label = "Addis time" if city and city.timezone == "Africa/Addis_Ababa" else zone.key
    return f"{booking.scheduled_at.astimezone(zone):%a %d %b, %H:%M} ({label})"


def _buttons(booking: Booking, confirming: str | None) -> list[list[Button]]:
    booking_id = str(booking.id)

    def button(text: str, action: str) -> Button:
        return Button(text, f"{action}:{booking_id}")

    if confirming == "decline":
        return [[button("Yes, give the job back", "decline!"), button("Back", "back")]]
    if confirming == "noshow":
        return [[button("Yes, rider did not come", "noshow!"), button("Back", "back")]]
    match booking.status:
        case S.ASSIGNED if booking.assignment.accepted_at is None:
            return [[button("Accept", "accept"), button("Decline", "decline")]]
        case S.ASSIGNED:
            return [[button("Start trip", "start")], [button("Give the job back", "decline")]]
        case S.EN_ROUTE:
            return [[button("Arrived at pickup", "arrive")]]
        case S.ARRIVED:
            return [[button("Rider on board", "pickup")], [button("No-show", "noshow")]]
        case S.IN_PROGRESS:
            return [[button("Complete trip", "complete")]]
    return []


async def job_card(
    booking: Booking, confirming: str | None = None
) -> tuple[str, list[list[Button]]]:
    if booking.status == S.ASSIGNED and booking.assignment.accepted_at is None:
        headline = "New job. Please accept or decline."
    else:
        headline = HEADLINES.get(booking.status, booking.status.value)
    if confirming == "decline":
        headline = "Give this job back to Guzo operations?"
    elif confirming == "noshow":
        headline = "Mark the rider as a no-show? Only after the free waiting time is over."
    riders = "\n".join(f"  {r.name} {r.phone}" for r in booking.riders)
    lines = [
        f"Guzo job {ref(booking)}",
        headline,
        "",
        f"Pickup: {booking.pickup.label}",
        f"When: {await local_time(booking)}",
    ]
    if booking.flight is not None:
        lines.append(f"Flight: {booking.flight.number}")
    lines += [
        f"Drop-off: {booking.dropoff.label}",
        f"Riders:\n{riders}",
        f"Seats {booking.seats}, bags {booking.bags}",
    ]
    return "\n".join(lines), _buttons(booking, confirming)
