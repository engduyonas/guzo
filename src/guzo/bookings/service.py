import logging
from datetime import timedelta
from typing import Any

from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError

from guzo.bookings import policies
from guzo.bookings.models import Assignment, Booking, Rider, StatusChange
from guzo.bookings.state_machine import Actor, BookingStatus, check_transition
from guzo.catalog.models import Product
from guzo.common.clock import utcnow
from guzo.common.ids import object_id
from guzo.config import get_settings
from guzo.db import in_transaction
from guzo.errors import Conflict, Forbidden, NotFound, Unavailable, Unprocessable
from guzo.events.outbox import emit
from guzo.identity.models import Role, User
from guzo.payments.models import Payment, PaymentStatus
from guzo.payments.refunds import request_refund
from guzo.pricing.models import Quote
from guzo.providers.registry import get_providers

S = BookingStatus
log = logging.getLogger(__name__)


async def get_booking(booking_id: str, session: AsyncClientSession | None = None) -> Booking:
    booking = await Booking.get(object_id(booking_id, "booking"), session=session)
    if booking is None:
        raise NotFound("booking not found")
    return booking


async def apply_transition(
    session: AsyncClientSession,
    booking: Booking,
    target: BookingStatus,
    *,
    actor: Actor,
    by: str | None = None,
    reason: str | None = None,
    set_fields: dict[str, Any] | None = None,
) -> Booking:
    """Move a booking to `target`. The only code path that changes `status`.

    Checks the transition table, appends to status_history and writes the
    `booking.<status>` outbox event in the caller's transaction.
    """
    check_transition(booking.status, target, actor)
    now = utcnow()
    change = StatusChange(
        from_status=booking.status, to_status=target, at=now, by=by, actor=actor, reason=reason
    )
    result = await Booking.get_pymongo_collection().update_one(
        # Guard on the status we checked, so a concurrent move cannot be overwritten.
        {"_id": booking.id, "status": booking.status.value},
        {
            "$set": {"status": target.value, "updated_at": now, **(set_fields or {})},
            "$push": {"status_history": change.model_dump(mode="python")},
        },
        session=session,
    )
    if result.modified_count != 1:
        raise Conflict("the booking changed, try again", code="stale_booking")
    await emit(
        session,
        f"booking.{target.value}",
        {"booking_id": str(booking.id), "from": booking.status.value, "to": target.value},
        booking_id=str(booking.id),
    )
    return await get_booking(str(booking.id), session)


def _validate_riders(riders: list[Rider], seats: int) -> None:
    if not riders:
        raise Unprocessable("a booking needs at least one rider", code="riders_required")
    if len(riders) > seats:
        raise Unprocessable("more riders than seats", code="too_many_riders")
    if sum(r.is_booker for r in riders) > 1:
        raise Unprocessable("only one rider can be the booker", code="riders_invalid")


async def create_booking(
    booker: User,
    *,
    quote_id: str,
    riders: list[Rider],
    partner_code: str | None,
    idempotency_key: str | None,
) -> Booking:
    """Create a booking from a quote. It starts as a draft and becomes quoted at once."""
    booker_id = str(booker.id)

    async def existing() -> Booking | None:
        if idempotency_key is None:
            return None
        return await Booking.find_one(
            Booking.booker_id == booker_id, Booking.idempotency_key == idempotency_key
        )

    if found := await existing():
        return found

    async def txn(session: AsyncClientSession) -> Booking:
        quote = await Quote.get(object_id(quote_id, "quote"), session=session)
        if quote is None or quote.booker_id != booker_id:
            raise NotFound("quote not found")
        now = utcnow()
        if quote.expires_at <= now:
            raise Conflict("the quote has expired, request a new one", code="quote_expired")
        request = quote.request
        _validate_riders(riders, request.seats)
        product = await Product.find_one(
            Product.city_id == request.city_id,
            Product.code == request.product_code,
            session=session,
        )
        if product is None or not product.active:
            raise NotFound("product not found", code="unknown_product")
        booking = Booking(
            product_code=request.product_code,
            city_id=request.city_id,
            booker_id=booker_id,
            riders=riders,
            pickup=request.pickup,
            dropoff=request.dropoff,
            scheduled_at=request.scheduled_at,
            flight=request.flight,
            vehicle_class=request.vehicle_class,
            seats=request.seats,
            bags=request.bags,
            price=quote.price,
            policy=product.policy,
            quote_id=quote_id,
            partner_code=partner_code,
            status=S.DRAFT,
            status_history=[
                StatusChange(
                    from_status=None, to_status=S.DRAFT, at=now, by=booker_id, actor=Actor.BOOKER
                )
            ],
            quote_expires_at=quote.expires_at,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )
        await booking.insert(session=session)
        return await apply_transition(session, booking, S.QUOTED, actor=Actor.SYSTEM)

    try:
        return await in_transaction(txn)
    except DuplicateKeyError as exc:
        if found := await existing():
            return found
        raise Conflict("this quote was already used for a booking", code="quote_used") from exc


async def confirm_booking(booker: User, booking_id: str) -> tuple[Booking, Payment]:
    """The booker accepts the price. Opens a payment with the provider.

    Safe to call again: a repeat returns the same payment, and retries the provider
    if the first call did not get through.
    """
    provider = get_providers().payments

    async def txn(session: AsyncClientSession) -> tuple[Booking, Payment]:
        booking = await get_booking(booking_id, session)
        if booking.booker_id != str(booker.id):
            raise NotFound("booking not found")
        now = utcnow()
        if booking.status == S.AWAITING_PAYMENT:
            attempts = await Payment.find(
                Payment.booking_id == booking_id, session=session
            ).to_list()
            latest = max(attempts, key=lambda p: p.created_at)
            if latest.status != PaymentStatus.FAILED:
                return booking, latest
            # The last attempt failed at the provider: open a fresh one.
            retry = Payment(
                booking_id=booking_id,
                provider=provider.name,
                idempotency_key=f"booking:{booking_id}:payment:{len(attempts) + 1}",
                amount=booking.price,
                created_at=now,
            )
            await retry.insert(session=session)
            return booking, retry
        if booking.status == S.QUOTED and booking.quote_expires_at <= now:
            raise Conflict("the quote has expired, request a new one", code="quote_expired")
        booking = await apply_transition(
            session,
            booking,
            S.AWAITING_PAYMENT,
            actor=Actor.BOOKER,
            by=str(booker.id),
            set_fields={
                "payment_expires_at": now + timedelta(minutes=get_settings().payment_ttl_minutes)
            },
        )
        payment = Payment(
            booking_id=booking_id,
            provider=provider.name,
            idempotency_key=f"booking:{booking_id}:payment:1",
            amount=booking.price,
            created_at=now,
        )
        await payment.insert(session=session)
        return booking, payment

    booking, payment = await in_transaction(txn)
    if payment.provider_ref is None:
        # Outside the transaction on purpose: a provider call cannot be rolled back.
        try:
            intent = await provider.create_payment(
                amount=payment.amount,
                reference=booking_id,
                idempotency_key=payment.idempotency_key,
            )
        except Exception as exc:
            log.exception("payment provider failed for booking %s", booking_id)
            raise Unavailable(
                "the payment provider is not responding, try again", code="payment_unavailable"
            ) from exc
        payment.provider_ref = intent.provider_ref
        payment.checkout_url = intent.checkout_url
        await payment.save()
    return booking, payment


async def cancel_booking(booking_id: str, *, actor: Actor, by: User, reason: str | None) -> Booking:
    async def txn(session: AsyncClientSession) -> Booking:
        booking = await get_booking(booking_id, session)
        if actor == Actor.BOOKER and booking.booker_id != str(by.id):
            raise NotFound("booking not found")
        updated = await apply_transition(
            session, booking, S.CANCELLED, actor=actor, by=str(by.id), reason=reason
        )
        refund = policies.cancellation_refund(
            booking.policy, booking.price, booking.scheduled_at, utcnow()
        )
        await request_refund(session, booking_id=booking_id, amount=refund, reason="cancelled")
        return updated

    return await in_transaction(txn)


async def assign_driver(
    booking_id: str, *, ops: User, driver_id: str, vehicle_id: str | None
) -> Booking:
    async def txn(session: AsyncClientSession) -> Booking:
        booking = await get_booking(booking_id, session)
        driver = await User.get(object_id(driver_id, "driver"), session=session)
        if driver is None or driver.role != Role.DRIVER or not driver.is_active:
            raise NotFound("driver not found", code="unknown_driver")
        if not driver.is_verified:
            raise Unprocessable("this driver has not been verified", code="driver_unverified")
        assignment = Assignment(driver_id=driver_id, vehicle_id=vehicle_id, assigned_at=utcnow())
        return await apply_transition(
            session,
            booking,
            S.ASSIGNED,
            actor=Actor.OPS,
            by=str(ops.id),
            set_fields={"assignment": assignment.model_dump(mode="python")},
        )

    return await in_transaction(txn)


async def unassign_driver(
    booking_id: str, *, actor: Actor, by: User, reason: str | None
) -> Booking:
    """Driver drops out, or ops takes the job back. The booking returns to confirmed."""

    async def txn(session: AsyncClientSession) -> Booking:
        booking = await get_booking(booking_id, session)
        if actor == Actor.DRIVER:
            _ensure_assigned_to(booking, by)
        return await apply_transition(
            session,
            booking,
            S.CONFIRMED,
            actor=actor,
            by=str(by.id),
            reason=reason,
            set_fields={"assignment": None},
        )

    return await in_transaction(txn)


def _ensure_assigned_to(booking: Booking, driver: User) -> None:
    if booking.assignment is None or booking.assignment.driver_id != str(driver.id):
        raise NotFound("booking not found")


async def driver_accept(booking_id: str, driver: User) -> Booking:
    """The assigned driver acknowledges the job. No status change."""
    booking = await get_booking(booking_id)
    _ensure_assigned_to(booking, driver)
    if booking.status != S.ASSIGNED:
        raise Conflict("this job can no longer be accepted", code="invalid_transition")
    await Booking.get_pymongo_collection().update_one(
        {
            "_id": booking.id,
            "status": S.ASSIGNED.value,
            "assignment.driver_id": str(driver.id),
            "assignment.accepted_at": None,
        },
        {"$set": {"assignment.accepted_at": utcnow()}},
    )
    return await get_booking(booking_id)


async def driver_advance(booking_id: str, driver: User, target: BookingStatus) -> Booking:
    """Driver moves the trip forward: en_route, arrived, in_progress, completed."""

    async def txn(session: AsyncClientSession) -> Booking:
        booking = await get_booking(booking_id, session)
        _ensure_assigned_to(booking, driver)
        if target == S.EN_ROUTE and booking.assignment.accepted_at is None:
            raise Conflict("accept the job before starting", code="not_accepted")
        return await apply_transition(
            session, booking, target, actor=Actor.DRIVER, by=str(driver.id)
        )

    return await in_transaction(txn)


async def mark_no_show(booking_id: str, *, actor: Actor, by: User) -> Booking:
    async def txn(session: AsyncClientSession) -> Booking:
        booking = await get_booking(booking_id, session)
        if actor == Actor.DRIVER:
            _ensure_assigned_to(booking, by)
            arrived_at = next(
                (c.at for c in reversed(booking.status_history) if c.to_status == S.ARRIVED), None
            )
            if arrived_at is not None:
                allowed_from = policies.no_show_allowed_from(
                    booking.policy, booking.scheduled_at, arrived_at
                )
                if utcnow() < allowed_from:
                    raise Forbidden("the free waiting time is not over yet", code="still_waiting")
        updated = await apply_transition(session, booking, S.NO_SHOW, actor=actor, by=str(by.id))
        refund = policies.no_show_refund(booking.policy, booking.price)
        await request_refund(session, booking_id=booking_id, amount=refund, reason="no_show")
        return updated

    return await in_transaction(txn)


async def expire_due() -> int:
    """Expire quotes and unpaid bookings past their deadline. Run by a worker."""
    now = utcnow()
    due = await Booking.find(
        {
            "$or": [
                {"status": S.QUOTED.value, "quote_expires_at": {"$lte": now}},
                {"status": S.AWAITING_PAYMENT.value, "payment_expires_at": {"$lte": now}},
            ]
        }
    ).to_list()
    expired = 0
    for candidate in due:

        async def txn(session: AsyncClientSession, booking_id: str = str(candidate.id)) -> None:
            booking = await get_booking(booking_id, session)
            await apply_transition(session, booking, S.EXPIRED, actor=Actor.SYSTEM)

        try:
            await in_transaction(txn)
            expired += 1
        except Conflict:
            # Paid or confirmed between the query and the transaction.
            continue
    return expired
