"""The driver bot: links a Telegram chat to a driver and turns button presses into
the same driver actions the API offers. Every rule stays in bookings.service."""

import logging
from typing import Any

from pymongo.errors import DuplicateKeyError

from guzo.bookings import service
from guzo.bookings.models import Booking
from guzo.bookings.state_machine import Actor, BookingStatus
from guzo.common.phone import normalize_phone
from guzo.errors import DomainError
from guzo.identity.models import Role, User
from guzo.providers.registry import get_providers
from guzo.telegram.cards import job_card

S = BookingStatus
log = logging.getLogger(__name__)

ACTIVE = [S.ASSIGNED.value, S.EN_ROUTE.value, S.ARRIVED.value, S.IN_PROGRESS.value]
ADVANCE = {
    "start": S.EN_ROUTE,
    "arrive": S.ARRIVED,
    "pickup": S.IN_PROGRESS,
    "complete": S.COMPLETED,
}
CONFIRM_FIRST = {"decline", "noshow"}

ASK_FOR_PHONE = (
    "Welcome to Guzo dispatch. Tap the button below to share your phone number, "
    "so we can find your driver account."
)


async def handle_update(update: dict[str, Any]) -> None:
    if callback := update.get("callback_query"):
        await _on_button(callback)
    elif (message := update.get("message")) and message["chat"].get("type") == "private":
        await _on_message(message)


async def driver_for_chat(chat_id: str) -> User | None:
    return await User.find_one(
        {"telegram_chat_id": chat_id, "role": Role.DRIVER.value, "is_active": True}
    )


async def send_job(driver: User, booking: Booking) -> None:
    text, buttons = await job_card(booking)
    await get_providers().driver_bot.send(
        chat_id=driver.telegram_chat_id, text=text, buttons=buttons
    )


async def _send_jobs(driver: User) -> None:
    jobs = (
        await Booking.find({"assignment.driver_id": str(driver.id), "status": {"$in": ACTIVE}})
        .sort("scheduled_at")
        .to_list()
    )
    if not jobs:
        await get_providers().driver_bot.send(
            chat_id=driver.telegram_chat_id, text="You have no upcoming jobs."
        )
    for booking in jobs:
        await send_job(driver, booking)


async def _on_message(message: dict[str, Any]) -> None:
    bot = get_providers().driver_bot
    chat_id = str(message["chat"]["id"])
    if contact := message.get("contact"):
        # A forwarded contact card is someone else's number; only accept the sender's own.
        if contact.get("user_id") != message["from"]["id"]:
            await bot.ask_for_phone(
                chat_id=chat_id, text="Please share your own number with the button."
            )
            return
        await _link(chat_id, contact.get("phone_number", ""))
        return
    driver = await driver_for_chat(chat_id)
    if driver is None:
        await bot.ask_for_phone(chat_id=chat_id, text=ASK_FOR_PHONE)
        return
    await _send_jobs(driver)


async def _link(chat_id: str, raw_phone: str) -> None:
    bot = get_providers().driver_bot
    try:
        phone = normalize_phone("+" + raw_phone.lstrip("+"))
    except ValueError:
        phone = None
    driver = phone and await User.find_one({"phone": phone, "role": Role.DRIVER.value})
    if not driver or not driver.is_active:
        await bot.send(
            chat_id=chat_id,
            text="This number is not registered as a Guzo driver. Ask Guzo operations to add you.",
        )
        return
    users = User.get_pymongo_collection()
    # One chat, one driver: a phone moved to a new Telegram account takes the link with it.
    await users.update_many(
        {"telegram_chat_id": chat_id, "_id": {"$ne": driver.id}},
        {"$set": {"telegram_chat_id": None}},
    )
    try:
        await users.update_one({"_id": driver.id}, {"$set": {"telegram_chat_id": chat_id}})
    except DuplicateKeyError:  # a concurrent link from the same chat; that one won
        return
    driver.telegram_chat_id = chat_id
    log.info("driver %s linked a Telegram chat", driver.id)
    await bot.send(
        chat_id=chat_id,
        text=(
            f"You are linked as {driver.name or 'a Guzo driver'}. New jobs will arrive here. "
            "Send any message to see your upcoming jobs."
        ),
    )
    await _send_jobs(driver)


async def _on_button(callback: dict[str, Any]) -> None:
    bot = get_providers().driver_bot
    callback_id = callback["id"]
    chat_id = str(callback["from"]["id"])
    message = callback.get("message") or {}  # absent when the message is too old
    action, _, booking_id = callback.get("data", "").partition(":")
    driver = await driver_for_chat(chat_id)
    if driver is None:
        await bot.answer(callback_id=callback_id, text="Share your phone number with the bot first")
        return
    confirming = None
    error = None
    try:
        booking = await _perform(action, booking_id, driver)
        if action in CONFIRM_FIRST:
            confirming = action
    except DomainError as exc:
        error = exc.message
        booking = await _own_booking(booking_id, driver)
    await bot.answer(callback_id=callback_id, text=error)
    if not message:
        return
    if booking is None:
        text, buttons = "You gave this job back, or it is no longer yours.", []
    else:
        text, buttons = await job_card(booking, confirming)
    await bot.edit(
        chat_id=str(message["chat"]["id"]),
        message_id=message["message_id"],
        text=text,
        buttons=buttons,
    )


async def _own_booking(booking_id: str, driver: User) -> Booking | None:
    try:
        booking = await service.get_booking(booking_id)
    except DomainError:
        return None
    if booking.assignment is None or booking.assignment.driver_id != str(driver.id):
        return None
    return booking


async def _perform(action: str, booking_id: str, driver: User) -> Booking | None:
    """Run the driver action. Returns the booking as the driver may now see it."""
    if action in ADVANCE:
        return await service.driver_advance(booking_id, driver, ADVANCE[action])
    if action == "accept":
        return await service.driver_accept(booking_id, driver)
    if action == "decline!":
        await service.unassign_driver(
            booking_id, actor=Actor.DRIVER, by=driver, reason="declined in Telegram"
        )
        return None
    if action == "noshow!":
        return await service.mark_no_show(booking_id, actor=Actor.DRIVER, by=driver)
    if action in CONFIRM_FIRST or action == "back":
        booking = await _own_booking(booking_id, driver)
        if booking is None:
            raise DomainError("this job is no longer yours")
        return booking
    raise DomainError("unknown button")
