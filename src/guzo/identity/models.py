from datetime import datetime
from enum import StrEnum

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import IndexModel

from guzo.common.clock import utcnow


class Role(StrEnum):
    BOOKER = "booker"
    DRIVER = "driver"
    OPS = "ops"


class User(Document):
    role: Role
    phone: str | None = None  # E.164; bookers and drivers
    email: str | None = None  # ops only
    name: str | None = None
    password_hash: str | None = None
    totp_secret: str | None = None
    # Bookers: phone confirmed. Drivers: vetted by ops. Ops: always true.
    is_verified: bool = False
    is_active: bool = True
    rating: float | None = None
    total_ratings: int = 0
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "users"
        indexes = [
            IndexModel(
                [("phone", 1)],
                unique=True,
                partialFilterExpression={"phone": {"$type": "string"}},
            ),
            IndexModel(
                [("email", 1)],
                unique=True,
                partialFilterExpression={"email": {"$type": "string"}},
            ),
        ]


class UserResponse(BaseModel):
    id: str
    role: Role
    phone: str | None
    email: str | None
    name: str | None
    is_verified: bool
    rating: float | None
    total_ratings: int
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> "UserResponse":
        return cls(id=str(user.id), **user.model_dump(include=set(cls.model_fields) - {"id"}))
