"""Browser pages: the booking link, the ops console and, with the fake provider, a pay page.

The pages are static files. Everything they do goes through the same /v1 API as the app,
so there is no second copy of the booking rules here.
"""

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from guzo.config import Settings
from guzo.payments.service import handle_webhook
from guzo.providers.base import PaymentEventStatus
from guzo.providers.fakes import FakePaymentProvider
from guzo.providers.registry import get_providers

STATIC = Path(__file__).parent / "static"

# Scripts and styles only from this origin, and nothing inline.
PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' https: data:; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-cache",
}


def _page(name: str) -> Response:
    return FileResponse(STATIC / name, media_type="text/html", headers=PAGE_HEADERS)


class SimulatedPayment(BaseModel):
    status: PaymentEventStatus = PaymentEventStatus.PAID


def mount_web(app: FastAPI, settings: Settings) -> None:
    router = APIRouter(include_in_schema=False)

    @router.get("/book")
    async def booking_page() -> Response:
        return _page("book.html")

    @router.get("/ops")
    async def ops_console() -> Response:
        return _page("ops.html")

    if settings.payment_provider == "fake":
        # Stands in for the provider's hosted checkout. Never registered with a real provider.
        @router.get("/dev/pay/{provider_ref}")
        async def fake_checkout(provider_ref: str) -> Response:
            return _page("devpay.html")

        @router.post("/dev/pay/{provider_ref}", status_code=204)
        async def simulate_payment(provider_ref: str, body: SimulatedPayment) -> Response:
            provider = get_providers().payments
            if not isinstance(provider, FakePaymentProvider):
                return Response(status_code=404)
            payload, headers = provider.build_webhook(provider_ref, body.status)
            await handle_webhook(provider.name, payload, headers)
            return Response(status_code=204)

    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
