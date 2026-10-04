from datetime import datetime
from enum import StrEnum

from beanie import Document
from pydantic import Field, StrictInt
from pymongo import IndexModel

from guzo.common.clock import utcnow
from guzo.common.money import Currency, Money


class PaymentStatus(StrEnum):
    PENDING = "pending"
    CAPTURED = "captured"
    FAILED = "failed"


class Payment(Document):
    booking_id: str
    provider: str
    provider_ref: str | None = None  # set once the provider has created the payment
    checkout_url: str | None = None
    idempotency_key: str
    amount: Money
    status: PaymentStatus = PaymentStatus.PENDING
    created_at: datetime = Field(default_factory=utcnow)
    captured_at: datetime | None = None

    class Settings:
        name = "payments"
        indexes = [
            IndexModel([("idempotency_key", 1)], unique=True),
            IndexModel(
                [("provider", 1), ("provider_ref", 1)],
                unique=True,
                partialFilterExpression={"provider_ref": {"$type": "string"}},
            ),
            IndexModel([("booking_id", 1), ("created_at", -1)]),
        ]


class RefundStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"


class Refund(Document):
    booking_id: str
    payment_id: str
    amount: Money
    reason: str
    status: RefundStatus = RefundStatus.PENDING
    provider_ref: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None

    class Settings:
        name = "refunds"
        indexes = [IndexModel([("booking_id", 1)])]


class LedgerEntry(Document):
    """One side of a double-entry posting. Positive is a debit, negative a credit."""

    posting_id: str  # groups the entries that must sum to zero
    account: str
    booking_id: str | None
    amount_minor: StrictInt
    currency: Currency
    kind: str
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "ledger_entries"
        indexes = [
            IndexModel([("account", 1), ("created_at", 1)]),
            IndexModel([("booking_id", 1)]),
        ]
