from datetime import datetime
from enum import StrEnum

from beanie import Document
from pydantic import Field
from pymongo import IndexModel

from guzo.common.clock import utcnow


class PartnerKind(StrEnum):
    HOTEL = "hotel"
    HOST = "host"
    AGENCY = "agency"


def normalize_code(code: str) -> str:
    return code.strip().upper()


class Partner(Document):
    """A hotel, host or agency that sends bookings through a referral code or link."""

    code: str  # upper case, e.g. HILTON
    kind: PartnerKind
    name: str
    commission_pct: int = Field(ge=0, le=100)
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "partners"
        indexes = [IndexModel([("code", 1)], unique=True)]
