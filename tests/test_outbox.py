from datetime import timedelta

import pytest

from guzo.bookings.models import Booking
from guzo.bookings.service import apply_transition, get_booking
from guzo.bookings.state_machine import Actor
from guzo.bookings.state_machine import BookingStatus as S
from guzo.db import in_transaction
from guzo.events.models import OutboxEvent, OutboxStatus
from guzo.events.outbox import deliver_pending


async def test_event_and_transition_commit_or_roll_back_together(flow, booker):
    booking = await flow.quoted(booker)
    before = await OutboxEvent.find_all().count()

    async def txn(session):
        current = await get_booking(booking["id"], session)
        await apply_transition(session, current, S.AWAITING_PAYMENT, actor=Actor.BOOKER)
        raise RuntimeError("crash after the write, before commit")

    with pytest.raises(RuntimeError):
        await in_transaction(txn)
    stored = await get_booking(booking["id"])
    assert stored.status == S.QUOTED and len(stored.status_history) == 2
    assert await OutboxEvent.find_all().count() == before


async def test_failed_delivery_is_retried_with_backoff(flow, booker, providers, time):
    await flow.confirmed(booker)
    providers.notifier.sent.clear()
    providers.notifier.fail = True
    # booking.quoted and booking.awaiting_payment have no reactions and go straight through.
    assert await deliver_pending() == 2
    failed = await OutboxEvent.find_one(OutboxEvent.type == "booking.confirmed")
    assert failed.status == OutboxStatus.PENDING and failed.attempts == 1
    assert "fake notifier unavailable" in failed.last_error

    providers.notifier.fail = False
    assert await deliver_pending() == 0  # still backing off
    time.advance(seconds=21)
    assert await deliver_pending() == 1
    assert len(providers.notifier.sent) == 1
    delivered = await OutboxEvent.get(failed.id)
    assert delivered.status == OutboxStatus.DELIVERED and delivered.attempts == 2


async def test_event_is_parked_after_too_many_failures(flow, booker, providers, time):
    await flow.confirmed(booker)
    providers.notifier.fail = True
    for _ in range(8):
        await deliver_pending()
        time.advance(hours=2)
    dead = await OutboxEvent.find_one(OutboxEvent.type == "booking.confirmed")
    assert dead.status == OutboxStatus.DEAD and dead.attempts == 8
    providers.notifier.fail = False
    assert await deliver_pending() == 0


async def test_concurrent_moves_cannot_both_win(flow, booker, time):
    """The status guard on the update stops a stale read from overwriting a newer state."""
    booking = await flow.quoted(booker)
    stale = await get_booking(booking["id"])
    await booker.post(f"/v1/bookings/{booking['id']}/confirm")

    async def txn(session):
        await apply_transition(session, stale, S.EXPIRED, actor=Actor.SYSTEM)

    with pytest.raises(Exception) as caught:
        await in_transaction(txn)
    assert getattr(caught.value, "code", None) == "stale_booking"
    assert (await Booking.get(stale.id)).status == S.AWAITING_PAYMENT
    assert timedelta(0) < (await Booking.get(stale.id)).payment_expires_at - time.now
