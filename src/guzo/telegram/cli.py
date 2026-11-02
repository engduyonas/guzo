"""Driver bot setup and local running.

uv run python -m guzo.telegram.cli webhook   # point Telegram at GUZO_PUBLIC_BASE_URL
uv run python -m guzo.telegram.cli poll      # local development: no public URL needed
"""

import asyncio
import logging
import sys

from guzo.config import configure_logging, get_settings
from guzo.db import close_db, init_db
from guzo.providers.registry import build_providers, set_providers
from guzo.providers.telegram import TelegramBot
from guzo.telegram.bot import handle_update

log = logging.getLogger(__name__)


def _bot() -> TelegramBot:
    settings = get_settings()
    if settings.driver_bot_provider != "telegram":
        sys.exit("set GUZO_DRIVER_BOT_PROVIDER=telegram and the bot token first")
    return TelegramBot(settings.telegram_bot_token)


async def set_webhook() -> None:
    settings = get_settings()
    bot = _bot()
    url = f"{settings.public_base_url}/v1/telegram/webhook"
    await bot.call(
        "setWebhook",
        url=url,
        secret_token=settings.telegram_webhook_secret,
        allowed_updates=["message", "callback_query"],
    )
    await bot.aclose()
    print(f"Telegram now sends driver bot updates to {url}")


async def poll() -> None:
    settings = get_settings()
    await init_db(settings)
    set_providers(build_providers(settings))
    bot = _bot()
    await bot.call("deleteWebhook")  # getUpdates refuses to run while a webhook is set
    print("Polling Telegram for driver bot updates. Ctrl-C to stop.")
    offset = None
    try:
        while True:
            for update in await bot.get_updates(offset):
                offset = update["update_id"] + 1
                try:
                    await handle_update(update)
                except Exception:
                    log.exception("telegram update %s failed", update["update_id"])
    finally:
        await bot.aclose()
        await close_db()


def main() -> None:
    configure_logging()
    commands = {"webhook": set_webhook, "poll": poll}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    asyncio.run(commands[sys.argv[1]]())


if __name__ == "__main__":
    main()
