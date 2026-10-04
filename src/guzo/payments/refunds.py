from pymongo.asynchronous.client_session import AsyncClientSession

from guzo.common.clock import utcnow
from guzo.common.money import Money
from guzo.events.outbox import emit
from guzo.payments import ledger
from guzo.payments.models import Payment, PaymentStatus, Refund

REFUND_REQUESTED = "refund.requested"


async def captured_payment(session: AsyncClientSession, booking_id: str) -> Payment | None:
    return await Payment.find_one(
        Payment.booking_id == booking_id,
        Payment.status == PaymentStatus.CAPTURED,
        session=session,
    )


async def request_refund(
    session: AsyncClientSession, *, booking_id: str, amount: Money, reason: str
) -> Refund | None:
    """Record money owed back to the booker. A worker sends it through the provider."""
    if amount.amount_minor <= 0:
        return None
    payment = await captured_payment(session, booking_id)
    if payment is None:
        return None
    refund = Refund(
        booking_id=booking_id,
        payment_id=str(payment.id),
        amount=amount,
        reason=reason,
        created_at=utcnow(),
    )
    await refund.insert(session=session)
    await ledger.post(
        session,
        kind="refund_requested",
        booking_id=booking_id,
        currency=amount.currency,
        lines=[
            (ledger.HELD_CUSTOMER_FUNDS, amount.amount_minor),
            (ledger.REFUNDS_PAYABLE, -amount.amount_minor),
        ],
    )
    await emit(session, REFUND_REQUESTED, {"refund_id": str(refund.id)}, booking_id=booking_id)
    return refund
