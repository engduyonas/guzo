from datetime import datetime
from enum import StrEnum

from beanie import Document
from pydantic import BaseModel, EmailStr, Field
from pymongo import IndexModel

from guzo.common.clock import utcnow
from guzo.common.money import Money


class CompanyPlan(BaseModel):
    """What a company pays each month and how many rides that covers."""

    monthly_fee: Money
    included_rides: int = Field(ge=0)


class Company(Document):
    """A hotel, NGO, embassy or other business whose staff book rides billed monthly."""

    name: str
    billing_email: EmailStr
    plan: CompanyPlan
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "companies"


class MemberRole(StrEnum):
    ADMIN = "admin"  # manages members and sees every company booking and the statement
    BOOKER = "booker"


class Membership(Document):
    """Links a booker account to a company. A booker belongs to one company at a time."""

    company_id: str
    user_id: str
    role: MemberRole
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "memberships"
        indexes = [
            IndexModel([("user_id", 1)], unique=True, partialFilterExpression={"active": True}),
            IndexModel([("company_id", 1)]),
        ]
