"""M0 exit test: create a booking, pay with the fake provider, walk every state."""

from datetime import timedelta

from guzo.bookings.service import expire_due
from guzo.bookings.state_machine import BookingStatus
from guzo.common.money import Currency
from guzo.events.models import OutboxEvent
from guzo.events.outbox import deliver_pending
from guzo.payments import ledger
from guzo.payments.models import LedgerEntry, Refund

from .conftest import RIDERS, SEDAN_KAZANCHIS, quote_body

FARE = SEDAN_KAZANCHIS.amount_minor


def path(booking: dict) -> list[str]:
    return [change["to_status"] for change in booking["status_history"]]


async def balances() -> dict[str, int]:
    accounts = (ledger.provider_account("fake"), ledger.HELD_CUSTOMER_FUNDS, ledger.REFUNDS_PAYABLE)
    return {a: (await ledger.balance(a, Currency.ETB)).amount_minor for a in accounts}


async def assert_ledger_balanced() -> None:
    entries = await LedgerEntry.find_all().to_list()
    by_posting: dict[str, int] = {}
    for entry in entries:
        by_posting[entry.posting_id] = by_posting.get(entry.posting_id, 0) + entry.amount_minor
    assert entries and all(total == 0 for total in by_posting.values())


async def test_happy_path_walks_draft_to_completed(flow, booker, ops, driver, providers):
    booking = await flow.quoted(booker)
    assert booking["status"] == "quoted"
    assert booking["price"] == {"amount_minor": FARE, "currency": "ETB"}
    assert booking["booker_id"] == booker.id
    assert [r["phone"] for r in booking["riders"]] == ["+251911000111", "+14155552671"]

    confirm = await booker.post(f"/v1/bookings/{booking['id']}/confirm")
    payment = confirm.json()["payment"]
    assert confirm.json()["booking"]["status"] == "awaiting_payment"
    assert payment["status"] == "pending" and payment["checkout_url"]

    assert (await flow.pay(payment)).status_code == 204
    base = f"/v1/driver/bookings/{booking['id']}"
    assigned = await ops.post(f"/v1/ops/bookings/{booking['id']}/assign", {"driver_id": driver.id})
    assert assigned.json()["status"] == "assigned"
    for action, status in [
        ("accept", "assigned"),
        ("start", "en_route"),
        ("arrive", "arrived"),
        ("pickup", "in_progress"),
        ("complete", "completed"),
    ]:
        response = await driver.post(f"{base}/{action}")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == status

    final = (await booker.get(f"/v1/bookings/{booking['id']}")).json()
    assert path(final) == [
        "draft",
        "quoted",
        "awaiting_payment",
        "confirmed",
        "assigned",
        "en_route",
        "arrived",
        "in_progress",
        "completed",
    ]
    history = final["status_history"]
    assert [c["from_status"] for c in history] == [None, *path(final)[:-1]]
    assert history[2]["by"] == booker.id and history[2]["actor"] == "booker"
    assert history[3]["by"] is None and history[3]["actor"] == "system"
    assert history[4]["by"] == ops.id
    assert history[5]["by"] == driver.id
    assert final["assignment"]["driver_id"] == driver.id
    assert final["assignment"]["accepted_at"] is not None

    await assert_ledger_balanced()
    assert await balances() == {
        "provider:fake": FARE,
        "held:customer_funds": -FARE,
        "payable:refunds": 0,
    }

    # Every transition left an event; workers turn them into messages.
    events = await OutboxEvent.find_all().sort("created_at").to_list()
    assert [e.type for e in events] == [f"booking.{s}" for s in path(final)[1:]]
    providers.notifier.sent.clear()
    assert await deliver_pending() == len(events)
    assert await deliver_pending() == 0
    rider_messages = providers.notifier.to("+251911000111")
    assert len(rider_messages) == 1 and "Tadesse Bekele" in rider_messages[0].body
    assert "Bole International Airport" in rider_messages[0].body
    booker_messages = [m.body for m in providers.notifier.to("+14155552671")]
    assert len(booker_messages) == 6 and "confirmed" in booker_messages[0]
    assert len(providers.notifier.to("+251911234567")) == 1  # driver job details


async def test_quote_expires(flow, booker, time):
    booking = await flow.quoted(booker)
    time.advance(minutes=16)
    late = await booker.post(f"/v1/bookings/{booking['id']}/confirm")
    assert late.status_code == 409 and late.json()["error"]["code"] == "quote_expired"
    assert await expire_due() == 1
    expired = (await booker.get(f"/v1/bookings/{booking['id']}")).json()
    assert path(expired) == ["draft", "quoted", "expired"]
    assert await expire_due() == 0


async def test_payment_times_out_and_late_payment_is_refunded(flow, booker, time, providers):
    booking, payment = await flow.awaiting_payment(booker)
    time.advance(minutes=31)
    assert await expire_due() == 1
    expired = (await booker.get(f"/v1/bookings/{booking['id']}")).json()
    assert path(expired)[-2:] == ["awaiting_payment", "expired"]

    assert (await flow.pay(payment)).status_code == 204
    still = (await booker.get(f"/v1/bookings/{booking['id']}")).json()
    assert still["status"] == "expired"
    await deliver_pending()
    refund = await Refund.find_one(Refund.booking_id == booking["id"])
    assert refund.amount == SEDAN_KAZANCHIS and refund.status == "succeeded"
    assert await balances() == {"provider:fake": 0, "held:customer_funds": 0, "payable:refunds": 0}
    await assert_ledger_balanced()


async def test_cancel_outside_window_refunds_in_full(flow, booker, providers):
    booking = await flow.confirmed(booker, days_ahead=7)
    response = await booker.post(
        f"/v1/bookings/{booking['id']}/cancel", {"reason": "plans changed"}
    )
    assert response.status_code == 200, response.text
    cancelled = response.json()
    assert path(cancelled)[-2:] == ["confirmed", "cancelled"]
    assert cancelled["status_history"][-1]["reason"] == "plans changed"

    assert await balances() == {
        "provider:fake": FARE,
        "held:customer_funds": 0,
        "payable:refunds": -FARE,
    }
    await deliver_pending()
    assert [amount.amount_minor for _, amount in providers.payments.refunds.values()] == [FARE]
    assert await balances() == {"provider:fake": 0, "held:customer_funds": 0, "payable:refunds": 0}
    await assert_ledger_balanced()


async def test_late_cancel_of_assigned_booking_keeps_the_fare(flow, booker, ops, driver):
    booking = await flow.assigned(booker, ops, driver, days_ahead=0.5)
    response = await ops.post(f"/v1/ops/bookings/{booking['id']}/cancel", {"reason": "duplicate"})
    assert path(response.json())[-2:] == ["assigned", "cancelled"]
    assert await Refund.find_all().count() == 0
    assert (await balances())["held:customer_funds"] == -FARE


async def test_driver_dropout_returns_booking_to_confirmed(flow, booker, ops, driver, sign_in):
    booking = await flow.assigned(booker, ops, driver)
    dropped = await driver.post(
        f"/v1/driver/bookings/{booking['id']}/drop", {"reason": "car fault"}
    )
    assert dropped.status_code == 200, dropped.text
    assert dropped.json()["status"] == "confirmed" and dropped.json()["assignment"] is None
    assert path(dropped.json())[-3:] == ["confirmed", "assigned", "confirmed"]

    # The job is gone for the first driver and can be given to another.
    assert (await driver.post(f"/v1/driver/bookings/{booking['id']}/start")).status_code == 404
    other = await sign_in("+251922333444", role="driver", name="Kebede")
    await ops.post(f"/v1/ops/drivers/{other.id}/verify")
    again = await ops.post(f"/v1/ops/bookings/{booking['id']}/assign", {"driver_id": other.id})
    assert again.json()["assignment"]["driver_id"] == other.id


async def test_no_show_after_waiting_window_keeps_the_fare(flow, booker, ops, driver, time):
    booking = await flow.arrived(booker, ops, driver, days_ahead=1)
    url = f"/v1/driver/bookings/{booking['id']}/no-show"
    early = await driver.post(url)
    assert early.status_code == 403 and early.json()["error"]["code"] == "still_waiting"

    time.advance(days=1, minutes=59)
    assert (await driver.post(url)).status_code == 403
    time.advance(minutes=2)
    response = await driver.post(url)
    assert response.status_code == 200, response.text
    assert path(response.json())[-2:] == ["arrived", "no_show"]
    assert await Refund.find_all().count() == 0  # no_show_fee_pct is 100 for this product


async def test_webhook_is_idempotent_and_signed(flow, booker, client, providers):
    booking, payment = await flow.awaiting_payment(booker)
    body, headers = providers.payments.build_webhook(payment["provider_ref"])

    forged = await client.post(
        "/v1/payments/webhooks/fake", content=body, headers={"x-fake-signature": "0" * 64}
    )
    assert forged.status_code == 401
    assert (await booker.get(f"/v1/bookings/{booking['id']}")).json()[
        "status"
    ] == "awaiting_payment"

    for _ in range(3):
        assert (
            await client.post("/v1/payments/webhooks/fake", content=body, headers=headers)
        ).status_code == 204
    paid = (await booker.get(f"/v1/bookings/{booking['id']}")).json()
    assert path(paid).count("confirmed") == 1
    assert await LedgerEntry.find_all().count() == 2


async def test_confirm_is_retryable_when_the_provider_is_down(flow, booker, providers):
    booking = await flow.quoted(booker)
    providers.payments.fail_next_create = True
    down = await booker.post(f"/v1/bookings/{booking['id']}/confirm")
    assert down.status_code == 503 and down.json()["error"]["code"] == "payment_unavailable"
    retry = await booker.post(f"/v1/bookings/{booking['id']}/confirm")
    assert retry.status_code == 200
    again = await booker.post(f"/v1/bookings/{booking['id']}/confirm")
    assert again.json()["payment"] == retry.json()["payment"]
    assert retry.json()["payment"]["provider_ref"]
    assert path(retry.json()["booking"]).count("awaiting_payment") == 1


async def test_failed_payment_can_be_retried(flow, booker):
    booking, payment = await flow.awaiting_payment(booker)
    assert (await flow.pay(payment, "failed")).status_code == 204
    retry = (await booker.post(f"/v1/bookings/{booking['id']}/confirm")).json()["payment"]
    assert retry["id"] != payment["id"] and retry["provider_ref"] != payment["provider_ref"]
    assert (await flow.pay(retry)).status_code == 204
    assert (await booker.get(f"/v1/bookings/{booking['id']}")).json()["status"] == "confirmed"


async def test_create_booking_is_idempotent_and_a_quote_is_single_use(booker, time):
    quote = (await booker.post("/v1/quotes", quote_body(time.now + timedelta(days=3)))).json()
    body = {"quote_id": quote["id"], "riders": RIDERS}
    first = await booker.post("/v1/bookings", body, headers={"Idempotency-Key": "abc"})
    second = await booker.post("/v1/bookings", body, headers={"Idempotency-Key": "abc"})
    assert first.json()["id"] == second.json()["id"]
    reuse = await booker.post("/v1/bookings", body, headers={"Idempotency-Key": "other"})
    assert reuse.status_code == 409 and reuse.json()["error"]["code"] == "quote_used"


async def test_invalid_moves_are_rejected(flow, booker, ops, driver, sign_in):
    booking = await flow.quoted(booker)
    base = f"/v1/bookings/{booking['id']}"
    # A quoted booking cannot be cancelled or assigned; only paid bookings can.
    cancel = await booker.post(f"{base}/cancel")
    assert cancel.status_code == 409 and cancel.json()["error"]["code"] == "invalid_transition"
    assign = await ops.post(f"/v1/ops{base[3:]}/assign", {"driver_id": driver.id})
    assert assign.status_code == 409

    assert (await booker.get("/v1/bookings/not-an-id")).status_code == 404
    bad_driver = await ops.post(f"/v1/ops{base[3:]}/assign", {"driver_id": "nope"})
    assert bad_driver.status_code == 404

    # Other accounts cannot see or act on it.
    stranger = await sign_in("+447911123456", name="Someone Else")
    assert (await stranger.get(base)).status_code == 404
    assert (await stranger.post(f"{base}/confirm")).status_code == 404
    assert (await booker.get("/v1/ops/bookings")).status_code == 403
    assert (await driver.post(f"/v1/driver{base[3:]}/start")).status_code == 404

    unverified = await sign_in("+251933555666", role="driver")
    paid = await flow.confirmed(booker)
    response = await ops.post(f"/v1/ops/bookings/{paid['id']}/assign", {"driver_id": unverified.id})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "driver_unverified"


async def test_driver_must_accept_before_starting(flow, booker, ops, driver):
    booking = await flow.assigned(booker, ops, driver)
    response = await driver.post(f"/v1/driver/bookings/{booking['id']}/start")
    assert response.status_code == 409 and response.json()["error"]["code"] == "not_accepted"


def test_every_status_is_covered_by_these_tests():
    """Guard: a new status must come with a lifecycle test that reaches it."""
    reached = {
        "draft", "quoted", "awaiting_payment", "confirmed", "assigned", "en_route",
        "arrived", "in_progress", "completed", "cancelled", "expired", "no_show",
    }  # fmt: skip
    assert reached == {status.value for status in BookingStatus}
