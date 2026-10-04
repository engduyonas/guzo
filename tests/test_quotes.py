from datetime import timedelta

from guzo.catalog.models import PricingStrategyName, Product, ProductPolicy, VehicleClass
from guzo.catalog.seed import ADDIS

from .conftest import RIDERS, quote_body


async def test_zone_fixed_quote_in_either_direction(booker, time):
    when = time.now + timedelta(days=2)
    to_city = await booker.post("/v1/quotes", quote_body(when))
    assert to_city.status_code == 201, to_city.text
    assert to_city.json()["price"] == {"amount_minor": 120_000, "currency": "ETB"}

    body = quote_body(when)
    body["pickup"], body["dropoff"] = body["dropoff"], body["pickup"]
    to_airport = await booker.post("/v1/quotes", body)
    assert to_airport.json()["price"] == to_city.json()["price"]


async def test_quote_errors(booker, time):
    when = time.now + timedelta(days=2)

    async def code(**overrides) -> tuple[int, str]:
        response = await booker.post("/v1/quotes", quote_body(when, **overrides))
        return response.status_code, response.json().get("error", {}).get("code")

    no_zone = quote_body(when)["dropoff"] | {"zone_id": None}
    bad_zone = quote_body(when)["dropoff"] | {"zone_id": "mars"}
    unpriced = quote_body(when)["dropoff"] | {"zone_id": "piassa"}
    assert await code(dropoff=no_zone) == (422, "zone_required")
    assert await code(dropoff=bad_zone) == (422, "unknown_zone")
    assert await code(dropoff=unpriced) == (422, "no_price")
    assert await code(pickup=quote_body(when)["dropoff"]) == (422, "airport_required")
    assert await code(vehicle_class="suv") == (422, "vehicle_class")
    assert await code(product_code="helicopter") == (404, "unknown_product")
    assert await code(scheduled_at=(time.now - timedelta(hours=1)).isoformat()) == (
        422,
        "scheduled_in_past",
    )
    # Times without a timezone are ambiguous for bookers abroad.
    naive = await booker.post("/v1/quotes", quote_body(when, scheduled_at="2027-01-07T07:00:00"))
    assert naive.status_code == 422


async def test_later_products_are_catalogue_rows_with_pricing_not_yet_available(booker, time):
    await Product(
        code="hourly",
        city_id=ADDIS,
        name="Hourly hire",
        pricing_strategy=PricingStrategyName.HOURLY,
        policy=ProductPolicy(
            free_cancel_hours=24, free_wait_minutes=15, no_show_fee_pct=100, late_cancel_fee_pct=100
        ),
        vehicle_classes=[VehicleClass.SEDAN],
    ).insert()
    products = (await booker.get("/v1/catalog/products")).json()
    assert {p["code"] for p in products} == {"airport_transfer", "hourly"}
    response = await booker.post(
        "/v1/quotes", quote_body(time.now + timedelta(days=1), product_code="hourly")
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "pricing_unavailable"


async def test_catalogue_lists_bole_as_an_airport_place(client):
    places = (await client.get("/v1/catalog/places")).json()
    assert places[0]["code"] == "bole_airport" and places[0]["place"]["kind"] == "airport"
    zones = (await client.get("/v1/catalog/zones")).json()
    assert {z["code"] for z in zones} == {"bole", "kazanchis", "piassa", "cmc"}


async def test_rider_rules(booker, time):
    quote = (await booker.post("/v1/quotes", quote_body(time.now + timedelta(days=2)))).json()

    async def create(riders) -> tuple[int, str | None]:
        response = await booker.post("/v1/bookings", {"quote_id": quote["id"], "riders": riders})
        return response.status_code, response.json().get("error", {}).get("code")

    assert (await create([]))[0] == 422
    assert (await create([{"name": "A", "phone": "not a phone"}]))[0] == 422
    assert await create(RIDERS * 2) == (422, "too_many_riders")  # 4 riders, 3 seats
    two_bookers = [{**r, "is_booker": True} for r in RIDERS]
    assert await create(two_bookers) == (422, "riders_invalid")
    # A booker can book for someone else and not ride at all.
    assert (await create([{"name": "Almaz", "phone": "0911000111"}]))[0] == 201
