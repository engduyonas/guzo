import hmac
import logging

from fastapi import APIRouter, Header, Request, Response

from guzo.config import get_settings
from guzo.errors import NotFound, Unauthorized
from guzo.telegram.bot import handle_update

router = APIRouter(prefix="/telegram", tags=["telegram"])
log = logging.getLogger(__name__)


@router.post("/webhook", status_code=204)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> Response:
    """Updates from the driver bot. Telegram sends the secret set with setWebhook."""
    secret = get_settings().telegram_webhook_secret
    if not secret:
        raise NotFound("the driver bot is not configured")
    if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
        x_telegram_bot_api_secret_token, secret
    ):
        raise Unauthorized("bad webhook secret")
    update = await request.json()
    try:
        await handle_update(update)
    except Exception:
        # Telegram retries failed updates forever and would replay button presses.
        # Log it (Sentry picks it up) and let the driver press again.
        log.exception("telegram update %s failed", update.get("update_id"))
    return Response(status_code=204)
