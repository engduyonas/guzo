"""Double-entry ledger. Balances are computed from entries and never edited."""

from uuid import uuid4

from pymongo.asynchronous.client_session import AsyncClientSession

from guzo.common.clock import utcnow
from guzo.common.money import Currency, Money
from guzo.payments.models import LedgerEntry

HELD_CUSTOMER_FUNDS = "held:customer_funds"
REFUNDS_PAYABLE = "payable:refunds"


def provider_account(provider: str) -> str:
    return f"provider:{provider}"


async def post(
    session: AsyncClientSession,
    *,
    kind: str,
    booking_id: str | None,
    currency: Currency,
    lines: list[tuple[str, int]],
) -> None:
    """Write one posting. `lines` are (account, signed minor amount) and must sum to zero."""
    if sum(amount for _, amount in lines) != 0:
        raise ValueError(f"unbalanced ledger posting {kind!r}: {lines}")
    posting_id = uuid4().hex
    now = utcnow()
    await LedgerEntry.insert_many(
        [
            LedgerEntry(
                posting_id=posting_id,
                account=account,
                booking_id=booking_id,
                amount_minor=amount,
                currency=currency,
                kind=kind,
                created_at=now,
            )
            for account, amount in lines
        ],
        session=session,
    )


async def balance(account: str, currency: Currency) -> Money:
    rows = await LedgerEntry.get_pymongo_collection().aggregate(
        [
            {"$match": {"account": account, "currency": currency.value}},
            {"$group": {"_id": None, "total": {"$sum": "$amount_minor"}}},
        ]
    )
    result = await rows.to_list()
    return Money(amount_minor=result[0]["total"] if result else 0, currency=currency)
