from fastapi import APIRouter, Request, Response

from guzo.payments.service import handle_webhook

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/webhooks/{provider}", status_code=204)
async def payment_webhook(provider: str, request: Request) -> Response:
    """Called by the payment provider. Authenticated by the provider's signature."""
    await handle_webhook(provider, await request.body(), request.headers)
    return Response(status_code=204)
