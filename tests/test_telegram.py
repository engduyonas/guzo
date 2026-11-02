"""The driver bot: linking a chat, and a whole pickup worked from Telegram buttons."""

from datetime import datetime, timedelta

import pytest

from guzo.bookings.service import follow_flights
from guzo.config import get_settings
from guzo.events.outbox import deliver_pending
from guzo.providers.base import FlightInfoResult
from guzo.telegram.bot import handle_update

CHAT = 5551  # in a private chat, Telegram's chat id is the user's id
DRIVER_PHONE = "251911234567"  # Telegram sends numbers without the +
SECRET = "test-webhook-secret"  # noqa: S105

_ids = iter(range(1, 10_000))


def message(text: str = "/start", chat: int = CHAT, **extra) -> dict:
    return {
        "update_id": next(_ids),
        "message": {
            "message_id": next(_ids),
            "from": {"id": chat},
            "chat": {"id": chat, "type": "private"},
            "text": text,
            **extra,
        },
    }


def contact(phone: str, owner: int = CHAT, chat: int = CHAT) -> dict:
    update = message(chat=chat, contact={"phone_number": phone, "user_id": owner})
    del update["message"]["text"]
    return update


def press(bot, data: str, chat: int = CHAT) -> dict:
    card = [m for m in bot.chat(str(chat)) if any(b.data == data for r in m.buttons for b in r)]
    assert card, f"no button {data} in chat {chat}"
    return {
        "update_id": next(_ids),
        "callback_query": {
            "id": str(next(_ids)),
            "from": {"id": chat},
            "message": {"message_id": card[-1].message_id, "chat": {"id": chat}},
            "data": data,
        },
    }


def labels(bot_message) -> list[str]:
    return [b.text for row in bot_message.buttons for b in row]


@pytest.fixture
def bot(providers):
    return providers.driver_bot


@pytest.fixture
async def linked(driver, bot):
    await handle_update(contact(DRIVER_PHONE))
    assert "You are linked as Tadesse Bekele" in bot.chat(str(CHAT))[0].text
    return driver


async def test_a_driver_links_by_sharing_their_own_number(driver, ops, bot):
    await handle_update(message("/start"))
    assert bot.phone_requests == [str(CHAT)]

    # A forwarded contact card is someone else's number.
    await handle_update(contact(DRIVER_PHONE, owner=9999))
    assert bot.phone_requests == [str(CHAT), str(CHAT)]

    await handle_update(contact(DRIVER_PHONE))
    assert "You are linked" in bot.chat(str(CHAT))[-2].text
    assert bot.chat(str(CHAT))[-1].text == "You have no upcoming jobs."
    drivers = (await ops.get("/v1/ops/drivers")).json()
    assert drivers[0]["driver"]["telegram_linked"] is True


async def test_an_unknown_number_is_not_linked(booker, bot):
    await handle_update(contact("14155552671"))  # a booker, not a driver
    assert "not registered as a Guzo driver" in bot.chat(str(CHAT))[-1].text
    await handle_update(message("/jobs"))
    assert bot.phone_requests == [str(CHAT)]


async def test_a_whole_pickup_from_telegram(booker, ops, linked, bot, providers, flow):
    booking = await flow.assigned(booker, ops, linked)
    await deliver_pending()
    card = bot.chat(str(CHAT))[-1]
    assert "New job" in card.text and "Bole" in card.text and "Almaz Tesfaye" in card.text
    assert labels(card) == ["Accept", "Decline"]
    # Linked drivers get the job in Telegram, not by SMS.
    assert not [m for m in providers.notifier.to("+251911234567") if "Guzo job" in m.body]

    booking_id = booking["id"]
    for action, expected in [
        ("accept", ["Start trip", "Give the job back"]),
        ("start", ["Arrived at pickup"]),
        ("arrive", ["Rider on board", "No-show"]),
        ("pickup", ["Complete trip"]),
        ("complete", []),
    ]:
        await handle_update(press(bot, f"{action}:{booking_id}"))
        assert bot.answers[-1][1] is None, bot.answers[-1]
        assert labels(card) == expected  # the same card, edited in place

    assert "Trip complete" in card.text
    done = (await booker.get(f"/v1/bookings/{booking_id}")).json()
    assert done["status"] == "completed"


async def test_declining_asks_first_and_returns_the_job(booker, ops, linked, bot, flow):
    booking = await flow.assigned(booker, ops, linked)
    await deliver_pending()
    card = bot.chat(str(CHAT))[-1]

    await handle_update(press(bot, f"decline:{booking['id']}"))
    assert labels(card) == ["Yes, give the job back", "Back"]
    await handle_update(press(bot, f"back:{booking['id']}"))
    assert labels(card) == ["Accept", "Decline"]

    await handle_update(press(bot, f"decline:{booking['id']}"))
    await handle_update(press(bot, f"decline!:{booking['id']}"))
    assert labels(card) == [] and "gave this job back" in card.text
    assert (await booker.get(f"/v1/bookings/{booking['id']}")).json()["status"] == "confirmed"
    sent = len(bot.messages)
    await deliver_pending()
    assert len(bot.messages) == sent  # no "given to another driver" for their own choice


async def test_a_no_show_waits_for_the_free_waiting_time(booker, ops, linked, bot, flow, time):
    booking = await flow.assigned(booker, ops, linked, days_ahead=1)
    await deliver_pending()
    for action in ("accept", "start", "arrive"):
        await handle_update(press(bot, f"{action}:{booking['id']}"))
    await handle_update(press(bot, f"noshow:{booking['id']}"))
    await handle_update(press(bot, f"noshow!:{booking['id']}"))
    assert "waiting time" in bot.answers[-1][1]
    assert labels(bot.chat(str(CHAT))[-1]) == ["Rider on board", "No-show"]

    time.advance(days=1, hours=3)
    await handle_update(press(bot, f"noshow:{booking['id']}"))
    await handle_update(press(bot, f"noshow!:{booking['id']}"))
    assert (await booker.get(f"/v1/bookings/{booking['id']}")).json()["status"] == "no_show"


async def test_drivers_are_told_when_a_job_is_taken_back_or_cancelled(
    booker, ops, linked, bot, flow
):
    booking = await flow.assigned(booker, ops, linked)
    await deliver_pending()
    response = await ops.post(f"/v1/ops/bookings/{booking['id']}/unassign", {"reason": "swap"})
    assert response.status_code == 200, response.text
    await deliver_pending()
    assert "given to another driver" in bot.chat(str(CHAT))[-1].text

    again = await ops.post(f"/v1/ops/bookings/{booking['id']}/assign", {"driver_id": linked.id})
    assert again.status_code == 200, again.text
    assert (await booker.post(f"/v1/bookings/{booking['id']}/cancel")).status_code == 200
    await deliver_pending()
    assert "was cancelled" in bot.chat(str(CHAT))[-1].text


async def test_someone_elses_chat_cannot_work_the_job(booker, ops, linked, bot, flow, sign_in):
    other = await sign_in("0922000000", role="driver", name="Other Driver")
    await ops.post(f"/v1/ops/drivers/{other.id}/verify")
    await handle_update(contact("251922000000", owner=7777, chat=7777))
    booking = await flow.assigned(booker, ops, linked)
    await deliver_pending()

    # Callback data is client-supplied: a forged press from the other driver's own chat.
    own_message = bot.chat("7777")[-1]
    await handle_update(
        {
            "update_id": next(_ids),
            "callback_query": {
                "id": "forged",
                "from": {"id": 7777},
                "message": {"message_id": own_message.message_id, "chat": {"id": 7777}},
                "data": f"accept:{booking['id']}",
            },
        }
    )
    assert bot.answers[-1][1] == "booking not found"
    status = (await booker.get(f"/v1/bookings/{booking['id']}")).json()
    assert status["status"] == "assigned"


async def test_the_webhook_needs_the_secret(client, linked, bot, monkeypatch):
    url = "/v1/telegram/webhook"
    assert (await client.post(url, json=message())).status_code == 404  # not configured
    monkeypatch.setattr(get_settings(), "telegram_webhook_secret", SECRET)
    wrong = {"X-Telegram-Bot-Api-Secret-Token": "guess"}
    assert (await client.post(url, json=message(), headers=wrong)).status_code == 401
    right = {"X-Telegram-Bot-Api-Secret-Token": SECRET}
    assert (await client.post(url, json=message("/jobs"), headers=right)).status_code == 204
    assert bot.chat(str(CHAT))[-1].text == "You have no upcoming jobs."


async def test_pickups_follow_a_delayed_flight(booker, ops, linked, bot, providers, flow):
    booking = await flow.assigned(booker, ops, linked, days_ahead=0.1)  # lands in 2.4 hours
    await deliver_pending()
    scheduled = (await booker.get(f"/v1/bookings/{booking['id']}")).json()["scheduled_at"]
    flight = booking["flight"]

    def estimate(**shift) -> None:
        arrival = datetime.fromisoformat(flight["scheduled_arrival"])
        providers.flights.flights["ET501"] = FlightInfoResult(
            "ET501", arrival, arrival + timedelta(**shift)
        )

    estimate(minutes=3)  # too small to move a pickup
    assert await follow_flights() == 0

    estimate(minutes=45)
    assert await follow_flights() == 1
    assert await follow_flights() == 0  # already followed
    moved = (await booker.get(f"/v1/bookings/{booking['id']}")).json()
    shift = datetime.fromisoformat(moved["scheduled_at"]) - datetime.fromisoformat(scheduled)
    assert shift == timedelta(minutes=45)

    await deliver_pending()
    assert "Pickup for booking" in bot.chat(str(CHAT))[-1].text
    assert "now lands at a different time" in providers.notifier.to("+14155552671")[-1].body
