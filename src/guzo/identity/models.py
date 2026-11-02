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
    photo_url: str | None = None  # drivers; shown to riders before pickup
    telegram_chat_id: str | None = None  # drivers; set when they share their number with the bot
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
                [("telegram_chat_id", 1)],
                unique=True,
                partialFilterExpression={"telegram_chat_id": {"$type": "string"}},
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
    photo_url: str | None
    is_verified: bool
    rating: float | None
    total_ratings: int
    telegram_linked: bool
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> "UserResponse":
        fields = user.model_dump(include=set(cls.model_fields) - {"id", "telegram_linked"})
        return cls(id=str(user.id), telegram_linked=user.telegram_chat_id is not None, **fields)
