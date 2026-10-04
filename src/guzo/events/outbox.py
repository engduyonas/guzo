"""Transactional outbox.

`emit` is called inside the same transaction as the state change it describes, so an
event exists if and only if the change committed. Workers call `deliver_pending`;
delivery is at-least-once, so handlers must be idempotent.
"""

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from pymongo import ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession

from guzo.common.clock import utcnow
from guzo.events.models import OutboxEvent, OutboxStatus

log = logging.getLogger(__name__)

Handler = Callable[[OutboxEvent], Awaitable[None]]

MAX_ATTEMPTS = 8
CLAIM_SECONDS = 120

_handlers: dict[str, list[Handler]] = defaultdict(list)


def on(*event_types: str) -> Callable[[Handler], Handler]:
    """Register a reaction to one or more event types."""

    def register(handler: Handler) -> Handler:
        for event_type in event_types:
            _handlers[event_type].append(handler)
        return handler

    return register


async def emit(
    session: AsyncClientSession,
    event_type: str,
    payload: dict[str, Any],
    *,
    booking_id: str | None = None,
) -> None:
    now = utcnow()
    event = OutboxEvent(
        type=event_type, payload=payload, booking_id=booking_id, available_at=now, created_at=now
    )
    await event.insert(session=session)


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(3600, 10 * 2**attempts))


async def _claim() -> OutboxEvent | None:
    # Pushing available_at forward is the lock: a crashed worker's event comes back
    # on its own once the claim lapses.
    now = utcnow()
    raw = await OutboxEvent.get_pymongo_collection().find_one_and_update(
        {"status": OutboxStatus.PENDING.value, "available_at": {"$lte": now}},
        {"$set": {"available_at": now + timedelta(seconds=CLAIM_SECONDS)}, "$inc": {"attempts": 1}},
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )
    return OutboxEvent.model_validate(raw) if raw else None


async def deliver_pending(limit: int = 100) -> int:
    """Deliver due events. Returns how many were delivered."""
    delivered = 0
    collection = OutboxEvent.get_pymongo_collection()
    for _ in range(limit):
        event = await _claim()
        if event is None:
            break
        try:
            for handler in _handlers.get(event.type, []):
                await handler(event)
        except Exception as exc:  # noqa: BLE001 - any handler failure means retry
            log.exception("outbox event %s (%s) failed", event.id, event.type)
            dead = event.attempts >= MAX_ATTEMPTS
            await collection.update_one(
                {"_id": event.id},
                {
                    "$set": {
                        "status": (OutboxStatus.DEAD if dead else OutboxStatus.PENDING).value,
                        "available_at": utcnow() + _backoff(event.attempts),
                        "last_error": repr(exc)[:500],
                    }
                },
            )
            continue
        await collection.update_one(
            {"_id": event.id},
            {"$set": {"status": OutboxStatus.DELIVERED.value, "delivered_at": utcnow()}},
        )
        delivered += 1
    return delivered
