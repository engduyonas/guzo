from collections.abc import Awaitable, Callable

from beanie import init_beanie
from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession

from guzo.bookings.models import Booking
from guzo.catalog.models import City, KnownPlace, Product, Zone, ZonePrice
from guzo.config import Settings
from guzo.events.models import OutboxEvent
from guzo.identity.models import User
from guzo.payments.models import LedgerEntry, Payment, Refund
from guzo.pricing.models import Quote

DOCUMENT_MODELS = [
    User,
    City,
    Zone,
    KnownPlace,
    Product,
    ZonePrice,
    Quote,
    Booking,
    Payment,
    Refund,
    LedgerEntry,
    OutboxEvent,
]

_client: AsyncMongoClient | None = None


async def init_db(settings: Settings) -> None:
    global _client
    _client = AsyncMongoClient(settings.mongo_url, tz_aware=True)
    await init_beanie(database=_client[settings.mongo_db], document_models=DOCUMENT_MODELS)


async def close_db() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def get_client() -> AsyncMongoClient:
    if _client is None:
        raise RuntimeError("database is not initialised")
    return _client


async def in_transaction[T](fn: Callable[[AsyncClientSession], Awaitable[T]]) -> T:
    """Run `fn` in a multi-document transaction (needs a replica set).

    The driver retries `fn` on transient conflicts, so it must only touch the database.
    """
    async with get_client().start_session() as session:
        return await session.with_transaction(fn)
