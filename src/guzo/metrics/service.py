"""Weekly numbers for the service review. Read-only, computed from bookings.

Weeks start on Monday in the city's own timezone, so a Sunday-night pickup in Addis
is not counted in the next week because it was already Monday in UTC.
"""

from datetime import date, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from guzo.bookings.models import Booking
from guzo.bookings.state_machine import BookingStatus
from guzo.catalog.models import City
from guzo.common.clock import utcnow

S = BookingStatus
REPEAT_WINDOW = timedelta(days=90)
DIRECT = "direct"


class WeekMetrics(BaseModel):
    week_start: date
    # Demand: bookings made this week.
    bookings_created: int
    bookings_paid: int
    paid_by_product: dict[str, int]
    paid_by_channel: dict[str, int]  # partner code, or "direct"
    bookers: int
    repeat_bookers: int  # had a completed trip in the 90 days before booking again
    # Service: paid trips scheduled for this week.
    trips_scheduled: int
    completed: int
    cancelled: int
    no_show: int
    cancellation_rate: float | None
    no_show_rate: float | None
    arrivals: int
    on_time_arrivals: int  # driver at pickup at or before the scheduled time
    on_time_rate: float | None
    median_minutes_to_assign: float | None  # from payment to a driver being assigned


def _reached(booking: Booking, status: BookingStatus) -> datetime | None:
    return next((c.at for c in booking.status_history if c.to_status == status), None)


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _week_start(moment: datetime, zone: ZoneInfo) -> date:
    local = moment.astimezone(zone).date()
    return local - timedelta(days=local.weekday())


async def weekly_metrics(city_id: str, weeks: int) -> list[WeekMetrics]:
    city = await City.find_one(City.code == city_id)
    zone = ZoneInfo(city.timezone) if city else ZoneInfo("UTC")
    this_week = _week_start(utcnow(), zone)
    starts = [this_week - timedelta(weeks=n) for n in range(weeks)]
    since = datetime.combine(starts[-1], datetime.min.time(), zone)

    bookings = await Booking.find(
        {
            "city_id": city_id,
            "$or": [{"created_at": {"$gte": since}}, {"scheduled_at": {"$gte": since}}],
        }
    ).to_list()
    paid = [b for b in bookings if _reached(b, S.CONFIRMED)]

    # Earlier completed trips by the same bookers, to spot people coming back.
    booker_ids = list({b.booker_id for b in paid})
    history = await Booking.find(
        {"booker_id": {"$in": booker_ids}, "status": S.COMPLETED.value}
    ).to_list()
    completed_at: dict[str, list[datetime]] = {}
    for trip in history:
        if done := _reached(trip, S.COMPLETED):
            completed_at.setdefault(trip.booker_id, []).append(done)

    result = []
    for start in starts:
        created = [b for b in bookings if _week_start(b.created_at, zone) == start]
        created_paid = [b for b in created if _reached(b, S.CONFIRMED)]
        by_product: dict[str, int] = {}
        by_channel: dict[str, int] = {}
        bookers: set[str] = set()
        repeat: set[str] = set()
        for b in created_paid:
            by_product[b.product_code] = by_product.get(b.product_code, 0) + 1
            channel = b.partner_code or DIRECT
            by_channel[channel] = by_channel.get(channel, 0) + 1
            bookers.add(b.booker_id)
            if any(
                timedelta(0) < b.created_at - done <= REPEAT_WINDOW
                for done in completed_at.get(b.booker_id, [])
            ):
                repeat.add(b.booker_id)

        trips = [b for b in paid if _week_start(b.scheduled_at, zone) == start]
        arrivals = [(b, at) for b in trips if (at := _reached(b, S.ARRIVED))]
        on_time = sum(1 for b, at in arrivals if at <= b.scheduled_at)
        to_assign = [
            (assigned - _reached(b, S.CONFIRMED)).total_seconds() / 60
            for b in trips
            if (assigned := _reached(b, S.ASSIGNED))
        ]
        count = {status: sum(1 for b in trips if b.status == status) for status in S}
        result.append(
            WeekMetrics(
                week_start=start,
                bookings_created=len(created),
                bookings_paid=len(created_paid),
                paid_by_product=by_product,
                paid_by_channel=by_channel,
                bookers=len(bookers),
                repeat_bookers=len(repeat),
                trips_scheduled=len(trips),
                completed=count[S.COMPLETED],
                cancelled=count[S.CANCELLED],
                no_show=count[S.NO_SHOW],
                cancellation_rate=_rate(count[S.CANCELLED], len(trips)),
                no_show_rate=_rate(count[S.NO_SHOW], len(trips)),
                arrivals=len(arrivals),
                on_time_arrivals=on_time,
                on_time_rate=_rate(on_time, len(arrivals)),
                median_minutes_to_assign=round(median(to_assign), 1) if to_assign else None,
            )
        )
    return result
