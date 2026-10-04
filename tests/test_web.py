"""The browser pages: served safely, and the parts of the API only they use."""

import json
from pathlib import Path

from guzo.web.routes import STATIC

from .conftest import RIDERS, quote_body


async def test_pages_are_served_with_a_strict_content_policy(client):
    for path in ("/book", "/ops", "/dev/pay/fake_123"):
        page = await client.get(path)
        assert page.status_code == 200 and "text/html" in page.headers["content-type"]
        policy = page.headers["content-security-policy"]
        assert "default-src 'self'" in policy and "unsafe-inline" not in policy
        assert '<script type="module" src="/static/' in page.text
    script = await client.get("/static/book.js")
    assert script.status_code == 200 and "javascript" in script.headers["content-type"]


def test_pages_have_no_inline_script_or_html_injection():
    for page in STATIC.glob("*.html"):
        assert "<script>" not in page.read_text() and "onclick=" not in page.read_text()
    for script in STATIC.glob("*.js"):
        assert "innerHTML" not in script.read_text(), f"{script.name} must build DOM as text"


def test_every_language_has_every_string():
    languages = {p.stem: json.loads(p.read_text()) for p in (STATIC / "i18n").glob("*.json")}
    assert set(languages) == {"en", "am"}
    assert set(languages["en"]) == set(languages["am"])
    used = set()
    source = Path(STATIC / "book.js").read_text()
    for key in languages["en"]:
        if f'"{key}"' in source or key.startswith("status_") or key in ("sedan", "minivan", "suv"):
            used.add(key)
    assert used == set(languages["en"]), f"unused strings: {set(languages['en']) - used}"


async def test_fake_checkout_pays_a_booking(flow, booker, client):
    booking, payment = await flow.awaiting_payment(booker)
    assert payment["checkout_url"].startswith("http://localhost:8000/dev/pay/fake_")
    path = payment["checkout_url"].removeprefix("http://localhost:8000")

    paid = await client.post(path, json={"status": "paid"})
    assert paid.status_code == 204
    assert (await booker.get(f"/v1/bookings/{booking['id']}")).json()["status"] == "confirmed"
    assert (await client.post("/dev/pay/fake_unknown", json={})).status_code == 404


async def test_a_typed_address_needs_no_coordinates(booker, time):
    from datetime import timedelta

    body = quote_body(time.now + timedelta(days=2))
    body["dropoff"] = {"label": "Sheraton Addis", "kind": "address", "zone_id": "kazanchis"}
    quote = await booker.post("/v1/quotes", body)
    assert quote.status_code == 201, quote.text
    assert quote.json()["request"]["dropoff"]["lat"] is None


async def test_booker_sees_the_driver_and_car_once_assigned(flow, booker, ops, driver, sign_in):
    booking = await flow.confirmed(booker)
    url = f"/v1/bookings/{booking['id']}/driver"
    waiting = await booker.get(url)
    assert waiting.status_code == 404 and waiting.json()["error"]["code"] == "no_driver"
    assert booking["policy"]["free_cancel_hours"] == 24

    await ops.post(f"/v1/ops/bookings/{booking['id']}/assign", {"driver_id": driver.id})
    seen = (await booker.get(url)).json()
    assert seen == {
        "name": "Tadesse Bekele",
        "phone": "+251911234567",
        "photo_url": None,
        "vehicle": {"plate": "AA 2-B12345", "make": "Toyota", "model": "Corolla", "color": "White"},
    }
    stranger = await sign_in("+447911123456")
    assert (await stranger.get(url)).status_code == 404
    assert RIDERS[0]["name"] not in json.dumps(seen)
