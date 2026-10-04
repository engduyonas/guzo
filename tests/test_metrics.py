from datetime import UTC, datetime, timedelta

from .conftest import RIDERS, quote_body

MONDAY = datetime(2026, 12, 14, 9, 0, tzinfo=UTC)  # 12:00 Monday in Addis


async def test_weekly_metrics(flow, ops, booker, driver, sign_in, time):
    time.now = MONDAY
    await ops.post(
        "/v1/ops/partners",
        {"code": "HILTON", "kind": "hotel", "name": "Hilton", "commission_pct": 10},
    )

    async def book(session, *, days_ahead: float, partner: str | None = None) -> dict:
        when = time.now + timedelta(days=days_ahead)
        quote = await session.post("/v1/quotes", quote_body(when))
        booking = await session.post(
            "/v1/bookings",
            {"quote_id": quote.json()["id"], "riders": RIDERS, "partner_code": partner},
        )
        return booking.json()

    async def pay(session, booking: dict) -> None:
        payment = (await session.post(f"/v1/bookings/{booking['id']}/confirm")).json()["payment"]
        assert (await flow.pay(payment)).status_code == 204

    async def drive(booking: dict, *, arrive_late_by: int | None, finish: bool = True) -> None:
        base = f"/v1/driver/bookings/{booking['id']}"
        await driver.post(f"{base}/accept")
        await driver.post(f"{base}/start")
        scheduled = datetime.fromisoformat(booking["scheduled_at"])
        time.now = scheduled + timedelta(minutes=arrive_late_by or -10)
        await driver.post(f"{base}/arrive")
        if finish:
            await driver.post(f"{base}/pickup")
            await driver.post(f"{base}/complete")

    # Week 1 (14 Dec): three paid bookings for that week, one never paid.
    on_time = await book(booker, days_ahead=1, partner="HILTON")
    late = await book(booker, days_ahead=2)
    cancelled = await book(booker, days_ahead=3)
    await book(booker, days_ahead=3)  # abandoned
    for booking in (on_time, late, cancelled):
        await pay(booker, booking)
    time.advance(minutes=30)
    await ops.post(f"/v1/ops/bookings/{on_time['id']}/assign", {"driver_id": driver.id})
    time.advance(minutes=60)
    await ops.post(f"/v1/ops/bookings/{late['id']}/assign", {"driver_id": driver.id})
    await booker.post(f"/v1/bookings/{cancelled['id']}/cancel")
    await drive(on_time, arrive_late_by=None)
    await drive(late, arrive_late_by=12)

    # Week 2 (21 Dec): the same booker returns; a new booker's rider does not show up.
    time.now = MONDAY + timedelta(days=7)
    again = await book(booker, days_ahead=1)
    await pay(booker, again)
    newcomer = await sign_in("+447911123456", name="New Booker")
    missed = await book(newcomer, days_ahead=2)
    await pay(newcomer, missed)
    await ops.post(f"/v1/ops/bookings/{missed['id']}/assign", {"driver_id": driver.id})
    await drive(missed, arrive_late_by=None, finish=False)
    time.advance(hours=2)
    await driver.post(f"/v1/driver/bookings/{missed['id']}/no-show")

    weeks = (await ops.get("/v1/ops/metrics/weekly?weeks=3")).json()
    assert [w["week_start"] for w in weeks] == ["2026-12-21", "2026-12-14", "2026-12-07"]
    second, first, empty = weeks

    assert first == {
        "week_start": "2026-12-14",
        "bookings_created": 4,
        "bookings_paid": 3,
        "paid_by_product": {"airport_transfer": 3},
        "paid_by_channel": {"HILTON": 1, "direct": 2},
        "bookers": 1,
        "repeat_bookers": 0,
        "trips_scheduled": 3,
        "completed": 2,
        "cancelled": 1,
        "no_show": 0,
        "cancellation_rate": 0.3333,
        "no_show_rate": 0.0,
        "arrivals": 2,
        "on_time_arrivals": 1,
        "on_time_rate": 0.5,
        "median_minutes_to_assign": 60.0,  # 30 and 90 minutes after payment
    }
    assert second["bookings_paid"] == 2 and second["bookers"] == 2
    assert second["repeat_bookers"] == 1
    assert second["trips_scheduled"] == 2 and second["no_show"] == 1
    assert second["no_show_rate"] == 0.5 and second["on_time_rate"] == 1.0
    assert empty["bookings_created"] == 0 and empty["on_time_rate"] is None
    assert empty["median_minutes_to_assign"] is None


async def test_weeks_follow_addis_time_not_utc(flow, ops, booker, time):
    # 22:30 UTC on Sunday 13 Dec is already 01:30 on Monday 14 Dec in Addis.
    time.now = datetime(2026, 12, 13, 22, 30, tzinfo=UTC)
    await flow.quoted(booker, days_ahead=10)
    weeks = (await ops.get("/v1/ops/metrics/weekly?weeks=2")).json()
    assert [(w["week_start"], w["bookings_created"]) for w in weeks] == [
        ("2026-12-14", 1),
        ("2026-12-07", 0),
    ]
    assert (await ops.get("/v1/ops/metrics/weekly?weeks=99")).status_code == 422
