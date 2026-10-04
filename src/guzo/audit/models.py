from datetime import datetime
from typing import Any

from beanie import Document
from pydantic import Field
from pymongo import IndexModel

from guzo.common.clock import utcnow


class AuditEntry(Document):
    """Who in operations did what, and to which record. Append-only."""

    actor_id: str
    action: str  # e.g. booking.assign, zone_price.set
    target_type: str
    target_id: str
    details: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "audit_log"
        indexes = [
            IndexModel([("target_type", 1), ("target_id", 1), ("at", -1)]),
            IndexModel([("actor_id", 1), ("at", -1)]),
        ]
