"""Telegram Bot API over HTTPS. Only the handful of methods the driver bot uses."""

import logging
from typing import Any

import httpx

from guzo.providers.base import Button

log = logging.getLogger(__name__)

API = "https://api.telegram.org"


class TelegramError(Exception):
    pass


def _keyboard(buttons: list[list[Button]]) -> dict[str, Any]:
    rows = [[{"text": b.text, "callback_data": b.data} for b in row] for row in buttons]
    return {"inline_keyboard": rows}


class TelegramBot:
    def __init__(self, token: str, timeout: float = 10.0):
        self._base = f"{API}/bot{token}"
        self._http = httpx.AsyncClient(timeout=timeout)

    async def call(self, method: str, **params: Any) -> Any:
        response = await self._http.post(f"{self._base}/{method}", json=params)
        data = response.json()
        if not data.get("ok"):
            # The token is in the URL, so never log the request itself.
            raise TelegramError(f"{method}: {data.get('description', response.status_code)}")
        return data["result"]

    async def send(self, *, chat_id: str, text: str, buttons: list[list[Button]] = ()) -> None:
        await self.call(
            "sendMessage", chat_id=chat_id, text=text, reply_markup=_keyboard(list(buttons))
        )

    async def edit(
        self, *, chat_id: str, message_id: int, text: str, buttons: list[list[Button]] = ()
    ) -> None:
        try:
            await self.call(
                "editMessageText",
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=_keyboard(list(buttons)),
            )
        except TelegramError as exc:
            if "message is not modified" not in str(exc):  # a repeated press
                raise

    async def answer(self, *, callback_id: str, text: str | None = None) -> None:
        params: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            params |= {"text": text, "show_alert": True}
        try:
            await self.call("answerCallbackQuery", **params)
        except TelegramError as exc:
            # Only clears the button's spinner; the action itself already happened.
            log.warning("could not answer a button press: %s", exc)

    async def ask_for_phone(self, *, chat_id: str, text: str) -> None:
        keyboard = {
            "keyboard": [[{"text": "Share my phone number", "request_contact": True}]],
            "one_time_keyboard": True,
            "resize_keyboard": True,
        }
        await self.call("sendMessage", chat_id=chat_id, text=text, reply_markup=keyboard)

    async def get_updates(self, offset: int | None, wait: int = 30) -> list[dict[str, Any]]:
        """Long polling, for local development where Telegram cannot reach a webhook."""
        response = await self._http.post(
            f"{self._base}/getUpdates",
            json={"offset": offset, "timeout": wait},
            timeout=wait + 10,
        )
        data = response.json()
        if not data.get("ok"):
            raise TelegramError(f"getUpdates: {data.get('description')}")
        return data["result"]

    async def aclose(self) -> None:
        await self._http.aclose()
