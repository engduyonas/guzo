"""In-memory providers for tests and local development."""

import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from guzo.common.money import Money
from guzo.providers.base import (
    Button,
    Channel,
    FlightInfoResult,
    IdentityResult,
    InvalidWebhook,
    PaymentEvent,
    PaymentEventStatus,
    PaymentIntent,
    RefundResult,
)

log = logging.getLogger(__name__)

SIGNATURE_HEADER = "x-fake-signature"


class FakePaymentProvider:
    name = "fake"

    def __init__(self, webhook_secret: str, checkout_base_url: str = "http://localhost:8000"):
        self._secret = webhook_secret.encode()
        self._checkout_base_url = checkout_base_url.rstrip("/")
        self.intents: dict[str, PaymentIntent] = {}
        self.refunds: dict[str, tuple[str, Money]] = {}
        self.fail_next_create = False

    async def create_payment(
        self, *, amount: Money, reference: str, idempotency_key: str
    ) -> PaymentIntent:
        if self.fail_next_create:
            self.fail_next_create = False
            raise RuntimeError("fake provider unavailable")
        if idempotency_key not in self.intents:
            ref = f"fake_{uuid4().hex}"
            self.intents[idempotency_key] = PaymentIntent(
                provider_ref=ref, checkout_url=f"{self._checkout_base_url}/dev/pay/{ref}"
            )
        return self.intents[idempotency_key]

    def sign(self, body: bytes) -> str:
        return hmac.new(self._secret, body, hashlib.sha256).hexdigest()

    def build_webhook(
        self, provider_ref: str, status: PaymentEventStatus = PaymentEventStatus.PAID
    ) -> tuple[bytes, dict[str, str]]:
        body = json.dumps({"provider_ref": provider_ref, "status": status.value}).encode()
        return body, {SIGNATURE_HEADER: self.sign(body), "content-type": "application/json"}

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> PaymentEvent:
        signature = headers.get(SIGNATURE_HEADER, "")
        if not hmac.compare_digest(signature, self.sign(body)):
            raise InvalidWebhook("bad signature")
        try:
            data = json.loads(body)
            return PaymentEvent(
                provider_ref=data["provider_ref"], status=PaymentEventStatus(data["status"])
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise InvalidWebhook("bad payload") from exc

    async def refund(
        self, *, provider_ref: str, amount: Money, idempotency_key: str
    ) -> RefundResult:
        self.refunds.setdefault(idempotency_key, (provider_ref, amount))
        return RefundResult(provider_ref=f"fake_refund_{idempotency_key}")


@dataclass
class SentMessage:
    to: str
    body: str
    channel: Channel


@dataclass
class FakeNotifier:
    sent: list[SentMessage] = field(default_factory=list)
    fail: bool = False

    async def send(self, *, to: str, body: str, channel: Channel = Channel.SMS) -> None:
        if self.fail:
            raise RuntimeError("fake notifier unavailable")
        # The only way to read a sign-in code locally. Fakes cannot run in production.
        log.info("fake %s to %s: %s", channel.value, to, body)
        self.sent.append(SentMessage(to=to, body=body, channel=channel))

    def to(self, phone: str) -> list[SentMessage]:
        return [m for m in self.sent if m.to == phone]


@dataclass
class FakeFlightStatus:
    flights: dict[str, FlightInfoResult] = field(default_factory=dict)

    async def lookup(self, *, number: str, date: datetime) -> FlightInfoResult | None:
        return self.flights.get(number)


@dataclass
class FakeIdentityCheck:
    verified: bool = True

    async def verify(self, *, user_id: str, document_refs: list[str]) -> IdentityResult:
        return IdentityResult(verified=self.verified)


@dataclass
class BotMessage:
    chat_id: str
    text: str
    buttons: list[list[Button]]
    message_id: int


@dataclass
class FakeDriverBot:
    """Keeps every chat as a list of messages; edits replace a message in place."""

    messages: list[BotMessage] = field(default_factory=list)
    answers: list[tuple[str, str | None]] = field(default_factory=list)
    phone_requests: list[str] = field(default_factory=list)

    async def send(self, *, chat_id: str, text: str, buttons: list[list[Button]] = ()) -> None:
        log.info("fake telegram to %s: %s", chat_id, text)
        self.messages.append(BotMessage(chat_id, text, list(buttons), len(self.messages) + 1))

    async def edit(
        self, *, chat_id: str, message_id: int, text: str, buttons: list[list[Button]] = ()
    ) -> None:
        message = self.messages[message_id - 1]
        if message.chat_id != chat_id:
            raise ValueError("message belongs to another chat")
        message.text, message.buttons = text, list(buttons)

    async def answer(self, *, callback_id: str, text: str | None = None) -> None:
        self.answers.append((callback_id, text))

    async def ask_for_phone(self, *, chat_id: str, text: str) -> None:
        self.phone_requests.append(chat_id)
        await self.send(chat_id=chat_id, text=text)

    def chat(self, chat_id: str) -> list[BotMessage]:
        return [m for m in self.messages if m.chat_id == chat_id]
