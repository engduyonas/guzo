"""Boundaries to everything outside Guzo. Booking logic depends only on these."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from guzo.common.money import Money


class InvalidWebhook(Exception):
    """Signature or payload of a provider webhook could not be trusted."""


class PaymentEventStatus(StrEnum):
    PAID = "paid"
    FAILED = "failed"


@dataclass(frozen=True)
class PaymentIntent:
    provider_ref: str
    checkout_url: str | None


@dataclass(frozen=True)
class PaymentEvent:
    provider_ref: str
    status: PaymentEventStatus


@dataclass(frozen=True)
class RefundResult:
    provider_ref: str


class PaymentProvider(Protocol):
    name: str

    async def create_payment(
        self, *, amount: Money, reference: str, idempotency_key: str
    ) -> PaymentIntent: ...

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> PaymentEvent:
        """Verify the signature and decode the event. Raises InvalidWebhook."""
        ...

    async def refund(
        self, *, provider_ref: str, amount: Money, idempotency_key: str
    ) -> RefundResult: ...


class Channel(StrEnum):
    SMS = "sms"
    WHATSAPP = "whatsapp"
    PUSH = "push"
    TELEGRAM = "telegram"


class Notifier(Protocol):
    async def send(self, *, to: str, body: str, channel: Channel = Channel.SMS) -> None: ...


@dataclass(frozen=True)
class FlightInfoResult:
    number: str
    scheduled_arrival: datetime
    estimated_arrival: datetime | None


class FlightStatus(Protocol):
    async def lookup(self, *, number: str, date: datetime) -> FlightInfoResult | None: ...


@dataclass(frozen=True)
class Button:
    text: str
    data: str  # sent back when pressed; Telegram allows 64 bytes


class DriverBot(Protocol):
    """Chat with drivers: job cards with buttons, edited in place as the trip moves."""

    async def send(self, *, chat_id: str, text: str, buttons: list[list[Button]] = ()) -> None: ...

    async def edit(
        self, *, chat_id: str, message_id: int, text: str, buttons: list[list[Button]] = ()
    ) -> None: ...

    async def answer(self, *, callback_id: str, text: str | None = None) -> None:
        """Acknowledge a button press, optionally with a short popup."""
        ...

    async def ask_for_phone(self, *, chat_id: str, text: str) -> None:
        """Show a one-tap button that shares the driver's own phone number."""
        ...


@dataclass(frozen=True)
class IdentityResult:
    verified: bool
    reason: str | None = None


class IdentityCheck(Protocol):
    async def verify(self, *, user_id: str, document_refs: list[str]) -> IdentityResult: ...


@dataclass
class Providers:
    payments: PaymentProvider
    notifier: Notifier
    flights: FlightStatus
    identity: IdentityCheck
    driver_bot: DriverBot
