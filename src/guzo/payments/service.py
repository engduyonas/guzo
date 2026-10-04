from collections.abc import Mapping

from beanie import PydanticObjectId
from pymongo.asynchronous.client_session import AsyncClientSession

from guzo.bookings.service import apply_transition, get_booking
from guzo.bookings.state_machine import Actor, BookingStatus
from guzo.common.clock import utcnow
from guzo.db import in_transaction
from guzo.errors import NotFound, Unauthorized
from guzo.payments import ledger
from guzo.payments.models import Payment, PaymentStatus, Refund, RefundStatus
from guzo.payments.refunds import request_refund
from guzo.providers.base import InvalidWebhook, PaymentEventStatus
from guzo.providers.registry import get_providers


async def handle_webhook(provider_name: str, body: bytes, headers: Mapping[str, str]) -> None:
    """Apply a provider webhook. Providers resend, so every branch is idempotent."""
    provider = get_providers().payments
    if provider_name != provider.name:
        raise NotFound("unknown payment provider")
    try:
        event = provider.parse_webhook(body, headers)
    except InvalidWebhook as exc:
        raise Unauthorized("webhook signature rejected", code="invalid_signature") from exc

    async def txn(session: AsyncClientSession) -> None:
        payment = await Payment.find_one(
            Payment.provider == provider.name,
            Payment.provider_ref == event.provider_ref,
            session=session,
        )
        if payment is None:
            raise NotFound("payment not found")
        if payment.status != PaymentStatus.PENDING:
            return
        now = utcnow()
        if event.status == PaymentEventStatus.FAILED:
            payment.status = PaymentStatus.FAILED
            await payment.save(session=session)
            return
        payment.status = PaymentStatus.CAPTURED
        payment.captured_at = now
        await payment.save(session=session)
        await ledger.post(
            session,
            kind="payment_captured",
            booking_id=payment.booking_id,
            currency=payment.amount.currency,
            lines=[
                (ledger.provider_account(provider.name), payment.amount.amount_minor),
                (ledger.HELD_CUSTOMER_FUNDS, -payment.amount.amount_minor),
            ],
        )
        booking = await get_booking(payment.booking_id, session)
        if booking.status == BookingStatus.AWAITING_PAYMENT:
            await apply_transition(session, booking, BookingStatus.CONFIRMED, actor=Actor.SYSTEM)
        else:
            # Money arrived for a booking that already expired: send it back.
            await request_refund(
                session,
                booking_id=payment.booking_id,
                amount=payment.amount,
                reason=f"paid_after_{booking.status.value}",
            )

    await in_transaction(txn)


async def process_refund(refund_id: str) -> None:
    """Send a recorded refund through the provider. Safe to run more than once."""
    refund = await Refund.get(PydanticObjectId(refund_id))
    if refund is None or refund.status == RefundStatus.SUCCEEDED:
        return
    payment = await Payment.get(PydanticObjectId(refund.payment_id))
    provider = get_providers().payments
    result = await provider.refund(
        provider_ref=payment.provider_ref,
        amount=refund.amount,
        idempotency_key=f"refund:{refund_id}",
    )

    async def txn(session: AsyncClientSession) -> None:
        current = await Refund.get(refund.id, session=session)
        if current.status == RefundStatus.SUCCEEDED:
            return
        current.status = RefundStatus.SUCCEEDED
        current.provider_ref = result.provider_ref
        current.completed_at = utcnow()
        await current.save(session=session)
        await ledger.post(
            session,
            kind="refund_paid",
            booking_id=refund.booking_id,
            currency=refund.amount.currency,
            lines=[
                (ledger.REFUNDS_PAYABLE, refund.amount.amount_minor),
                (ledger.provider_account(payment.provider), -refund.amount.amount_minor),
            ],
        )

    await in_transaction(txn)
