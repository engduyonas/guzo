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
