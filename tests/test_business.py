"""Business accounts: companies, their bookers, pay-later bookings and the statement."""

from datetime import UTC, datetime, timedelta

from guzo.events.outbox import deliver_pending
from guzo.payments.models import Payment

from .conftest import RIDERS, SEDAN_KAZANCHIS, quote_body

FARE = SEDAN_KAZANCHIS.amount_minor
PLAN = {"monthly_fee": {"amount_minor": 500_000, "currency": "ETB"}, "included_rides": 2}
ADMIN_PHONE = "+251911200200"
STAFF_PHONE = "+251911300300"


async def make_company(ops, plan: dict = PLAN, name: str = "Hilton Addis Ababa") -> dict:
    response = await ops.post(
        "/v1/ops/companies", {"name": name, "billing_email": "ap@hilton.example.com", "plan": plan}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def add_member(by, path: str, phone: str, role: str) -> dict:
    response = await by.post(path, {"phone": phone, "name": "Staff", "role": role})
    assert response.status_code == 201, response.text
    return response.json()


async def company_booking(session, at: datetime) -> dict:
    quote = await session.post("/v1/quotes", quote_body(at))
    assert quote.status_code == 201, quote.text
    response = await session.post(
        "/v1/bookings", {"quote_id": quote.json()["id"], "riders": RIDERS, "bill_to_company": True}
    )
    assert response.status_code == 201, response.text
    confirm = await session.post(f"/v1/bookings/{response.json()['id']}/confirm")
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["payment"] is None
    return confirm.json()["booking"]


async def test_company_booking_is_confirmed_without_payment(ops, sign_in, providers):
    company = await make_company(ops)
    await add_member(ops, f"/v1/ops/companies/{company['id']}/members", ADMIN_PHONE, "admin")

    # Added before the first sign-in: signing in proves the phone and keeps the account.
    admin = await sign_in(ADMIN_PHONE)
    assert admin.user["is_verified"] is True
    mine = (await admin.get("/v1/business/company")).json()
    assert mine["role"] == "admin" and mine["company"]["id"] == company["id"]

    await add_member(admin, "/v1/business/members", STAFF_PHONE, "booker")
    staff = await sign_in(STAFF_PHONE)
    booking = await company_booking(staff, datetime.now(UTC) + timedelta(days=3))

    assert booking["status"] == "confirmed"
    assert booking["company_id"] == company["id"]
    assert [c["to_status"] for c in booking["status_history"]][-2:] == [
        "awaiting_payment",
        "confirmed",
    ]
    assert booking["status_history"][-1]["reason"] == "billed to company"
    assert await Payment.find_all().count() == 0

    # A repeat confirm changes nothing.
    again = await staff.post(f"/v1/bookings/{booking['id']}/confirm")
    assert again.json()["booking"]["status_history"] == booking["status_history"]

    await deliver_pending()
    assert "billed to your company" in providers.notifier.to(STAFF_PHONE)[-1].body


async def test_only_members_of_an_active_company_can_bill_it(ops, sign_in, booker):
    body = {"riders": RIDERS, "bill_to_company": True}
    quote = await booker.post("/v1/quotes", quote_body(datetime.now(UTC) + timedelta(days=3)))
    response = await booker.post("/v1/bookings", {**body, "quote_id": quote.json()["id"]})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_a_company_member"

    company = await make_company(ops)
    path = f"/v1/ops/companies/{company['id']}/members"
    await add_member(ops, path, STAFF_PHONE, "booker")
    staff = await sign_in(STAFF_PHONE)
    assert (await staff.get("/v1/business/members")).status_code == 403  # not an admin

    # One company at a time.
    other = await make_company(ops, name="Sheraton Addis")
    response = await ops.post(
        f"/v1/ops/companies/{other['id']}/members",
        {"phone": STAFF_PHONE, "role": "booker"},
    )
    assert response.status_code == 409

    # A deactivated company cannot take new rides.
    await ops.http.patch(
        f"/v1/ops/companies/{company['id']}", json={"active": False}, headers=ops.headers
    )
    quote = await staff.post("/v1/quotes", quote_body(datetime.now(UTC) + timedelta(days=3)))
    response = await staff.post("/v1/bookings", {**body, "quote_id": quote.json()["id"]})
    assert response.status_code == 403


async def test_company_billed_in_another_currency_is_refused(ops, sign_in):
    plan = {"monthly_fee": {"amount_minor": 50_000, "currency": "USD"}, "included_rides": 0}
    company = await make_company(ops, plan)
    await add_member(ops, f"/v1/ops/companies/{company['id']}/members", STAFF_PHONE, "booker")
    staff = await sign_in(STAFF_PHONE)
    quote = await staff.post("/v1/quotes", quote_body(datetime.now(UTC) + timedelta(days=3)))
    response = await staff.post(
        "/v1/bookings", {"quote_id": quote.json()["id"], "riders": RIDERS, "bill_to_company": True}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "currency_mismatch"


async def test_statement_uses_included_rides_then_charges_extras(ops, sign_in, driver, time):
    time.now = datetime(2027, 1, 10, 6, tzinfo=UTC)
    company = await make_company(ops)
    await add_member(ops, f"/v1/ops/companies/{company['id']}/members", ADMIN_PHONE, "admin")
    admin = await sign_in(ADMIN_PHONE)

    pickup = time.now + timedelta(days=2)
    ride_1, no_show, ride_2, late_cancel, ops_cancel = [
        await company_booking(admin, pickup + timedelta(hours=hour)) for hour in range(5)
    ]

    time.advance(days=1, hours=20)  # inside the 24-hour free-cancel window
    response = await admin.post(f"/v1/bookings/{late_cancel['id']}/cancel")
    assert response.status_code == 200, response.text
    response = await ops.post(f"/v1/ops/bookings/{ops_cancel['id']}/cancel")
    assert response.status_code == 200, response.text

    for booking in (ride_1, no_show, ride_2):
        response = await ops.post(
            f"/v1/ops/bookings/{booking['id']}/assign", {"driver_id": driver.id}
        )
        assert response.status_code == 200, response.text
        actions = ["accept", "start", "arrive"]
        if booking is not no_show:
            actions += ["pickup", "complete"]
        for action in actions:
            response = await driver.post(f"/v1/driver/bookings/{booking['id']}/{action}")
            assert response.status_code == 200, response.text
    assert (await ops.post(f"/v1/ops/bookings/{no_show['id']}/no-show")).status_code == 200

    response = await admin.get("/v1/business/statement", params={"month": "2027-01"})
    assert response.status_code == 200, response.text
    statement = response.json()
    lines = [(line["booking_id"], line["kind"], line["included"]) for line in statement["lines"]]
    assert lines == [
        (ride_1["id"], "ride", True),
        (no_show["id"], "no_show", True),
        (ride_2["id"], "ride", False),
        (late_cancel["id"], "late_cancel", False),
    ]
    assert [line["charge"]["amount_minor"] for line in statement["lines"]] == [0, 0, FARE, FARE]
    assert statement["rides_used"] == 2
    assert statement["extras"] == {"amount_minor": 2 * FARE, "currency": "ETB"}
    assert statement["total"] == {"amount_minor": 500_000 + 2 * FARE, "currency": "ETB"}

    # Ops see the same statement; another month is empty apart from the fee.
    ops_view = await ops.get(
        f"/v1/ops/companies/{company['id']}/statement", params={"month": "2027-01"}
    )
    assert ops_view.json() == statement
    february = (await admin.get("/v1/business/statement", params={"month": "2027-02"})).json()
    assert february["lines"] == [] and february["total"]["amount_minor"] == 500_000

    bookings = await admin.get("/v1/business/bookings", params={"month": "2027-01"})
    assert len(bookings.json()) == 5


async def test_removed_member_can_no_longer_bill_the_company(ops, sign_in):
    company = await make_company(ops)
    await add_member(ops, f"/v1/ops/companies/{company['id']}/members", ADMIN_PHONE, "admin")
    admin = await sign_in(ADMIN_PHONE)
    staff_member = await add_member(admin, "/v1/business/members", STAFF_PHONE, "booker")
    staff = await sign_in(STAFF_PHONE)

    response = await admin.post(f"/v1/business/members/{staff_member['user_id']}/remove")
    assert response.status_code == 204
    members = (await admin.get("/v1/business/members")).json()
    assert [m["role"] for m in members] == ["admin"]

    quote = await staff.post("/v1/quotes", quote_body(datetime.now(UTC) + timedelta(days=3)))
    response = await staff.post(
        "/v1/bookings", {"quote_id": quote.json()["id"], "riders": RIDERS, "bill_to_company": True}
    )
    assert response.status_code == 403


async def test_ops_can_remove_a_member_and_it_is_audited(ops, sign_in):
    company = await make_company(ops)
    members_path = f"/v1/ops/companies/{company['id']}/members"
    member = await add_member(ops, members_path, STAFF_PHONE, "booker")

    response = await ops.post(f"{members_path}/{member['user_id']}/remove")
    assert response.status_code == 204
    assert (await ops.get(members_path)).json() == []
    again = await ops.post(f"{members_path}/{member['user_id']}/remove")
    assert again.status_code == 404

    trail = (await ops.get(f"/v1/ops/audit?target_type=company&target_id={company['id']}")).json()
    assert "company.member_remove" in [entry["action"] for entry in trail]


async def test_weekly_numbers_count_company_bookings_apart(ops, sign_in):
    company = await make_company(ops)
    await add_member(ops, f"/v1/ops/companies/{company['id']}/members", ADMIN_PHONE, "admin")
    admin = await sign_in(ADMIN_PHONE)
    await company_booking(admin, datetime.now(UTC) + timedelta(days=3))

    week = (await ops.get("/v1/ops/metrics/weekly?weeks=1")).json()[0]
    assert week["bookings_paid"] == 1 and week["bookings_billed"] == 1
    assert week["paid_by_channel"] == {"company": 1}
