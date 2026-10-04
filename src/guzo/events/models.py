from datetime import datetime
from enum import StrEnum
from typing import Any

from beanie import Document
from pydantic import Field
from pymongo import IndexModel

from guzo.common.clock import utcnow


class OutboxStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    DEAD = "dead"  # gave up after max attempts; needs a person


class OutboxEvent(Document):
    type: str  # e.g. booking.confirmed, refund.requested
    payload: dict[str, Any]
    booking_id: str | None = None
    status: OutboxStatus = OutboxStatus.PENDING
    attempts: int = 0
    available_at: datetime = Field(default_factory=utcnow)
    last_error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    delivered_at: datetime | None = None

    class Settings:
        name = "outbox"
        indexes = [IndexModel([("status", 1), ("available_at", 1)])]
