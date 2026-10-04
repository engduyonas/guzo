"""Operations controls: catalogue, fleet, partners and the audit trail."""

from datetime import timedelta

from guzo.audit.models import AuditEntry

from .conftest import RIDERS, VEHICLE, quote_body

PRICE = {
    "product_code": "airport_transfer",
    "zone_id": "piassa",
    "vehicle_class": "minivan",
    "price": {"amount_minor": 220_000, "currency": "ETB"},
}


async def actions(target_id: str | None = None) -> list[str]:
    query = {"target_id": target_id} if target_id else {}
    return [e.action for e in await AuditEntry.find(query).sort("at", "_id").to_list()]


async def test_ops_publish_change_and_withdraw_zone_prices(ops, booker, time):
    when = time.now + timedelta(days=2)
    piassa = quote_body(when)["dropoff"] | {"zone_id": "piassa"}
    body = quote_body(when, dropoff=piassa, vehicle_class="minivan")
    assert (await booker.post("/v1/quotes", body)).json()["error"]["code"] == "no_price"

    created = await ops.http.put("/v1/ops/catalog/zone-prices", json=PRICE, headers=ops.headers)
    assert created.status_code == 200, created.text
    assert (await booker.post("/v1/quotes", body)).json()["price"]["amount_minor"] == 220_000

    cheaper = PRICE | {"price": {"amount_minor": 200_000, "currency": "ETB"}}
    changed = await ops.http.put("/v1/ops/catalog/zone-prices", json=cheaper, headers=ops.headers)
    assert changed.json()["id"] == created.json()["id"]
    old_quote = (await booker.post("/v1/quotes", body)).json()
    assert old_quote["price"]["amount_minor"] == 200_000

    listed = (await ops.get("/v1/ops/catalog/zone-prices?product_code=airport_transfer")).json()
    assert {(p["zone_id"], p["vehicle_class"]) for p in listed} == {
        ("kazanchis", "sedan"),
        ("piassa", "minivan"),
    }
    deleted = await ops.http.delete(
        f"/v1/ops/catalog/zone-prices/{created.json()['id']}", headers=ops.headers
    )
    assert deleted.status_code == 204
    assert (await booker.post("/v1/quotes", body)).json()["error"]["code"] == "no_price"
    # A quote already given still stands.
    booked = await booker.post("/v1/bookings", {"quote_id": old_quote["id"], "riders": RIDERS})
    assert booked.status_code == 201 and booked.json()["price"]["amount_minor"] == 200_000

    entries = (
        await AuditEntry.find(AuditEntry.target_type == "zone_price").sort("at", "_id").to_list()
    )
    assert [e.action for e in entries] == ["zone_price.set", "zone_price.set", "zone_price.delete"]
    assert entries[1].details["previous_price"] == {"amount_minor": 220_000, "currency": "ETB"}
    assert entries[0].actor_id == ops.id


async def test_zone_price_validation(ops):
    async def put(**overrides) -> tuple[int, str | None]:
        r = await ops.http.put(
            "/v1/ops/catalog/zone-prices", json=PRICE | overrides, headers=ops.headers
        )
        return r.status_code, r.json().get("error", {}).get("code")

    assert await put(zone_id="mars") == (422, "unknown_zone")
    assert await put(product_code="helicopter") == (404, "unknown_product")
    assert await put(vehicle_class="suv") == (422, "vehicle_class")
    assert await put(price={"amount_minor": 0, "currency": "ETB"}) == (422, "invalid_price")
    assert (await put(price={"amount_minor": 1200.5, "currency": "ETB"}))[0] == 422


async def test_policy_changes_apply_to_new_bookings_only(flow, ops, booker):
    before = await flow.confirmed(booker, days_ahead=0.5)  # inside the 24h window
    policy = {
        "free_cancel_hours": 2,
        "free_wait_minutes": 90,
        "no_show_fee_pct": 50,
        "late_cancel_fee_pct": 25,
    }
    updated = await ops.http.patch(
        "/v1/ops/catalog/products/airport_transfer", json={"policy": policy}, headers=ops.headers
    )
    assert updated.status_code == 200 and updated.json()["policy"] == policy
    after = await flow.confirmed(booker, days_ahead=0.5)

    await booker.post(f"/v1/bookings/{before['id']}/cancel")
    await booker.post(f"/v1/bookings/{after['id']}/cancel")
    money_before = (await ops.get(f"/v1/ops/bookings/{before['id']}/money")).json()
    money_after = (await ops.get(f"/v1/ops/bookings/{after['id']}/money")).json()
    assert money_before["refunds"] == []  # booked under the old terms: late cancel keeps the fare
    assert [r["amount"]["amount_minor"] for r in money_after["refunds"]] == [120_000]
    assert money_after["payments"][0]["status"] == "captured"


async def test_vehicle_options_fit_the_party_and_need_a_price(client, ops, time):
    await ops.http.put(
        "/v1/ops/catalog/zone-prices", json=PRICE | {"zone_id": "kazanchis"}, headers=ops.headers
    )
    trip = quote_body(time.now + timedelta(days=2))
    del trip["vehicle_class"]

    options = await client.post("/v1/quotes/options", json=trip)  # no sign-in needed
    assert options.status_code == 200, options.text
    assert [(o["vehicle_class"], o["price"]["amount_minor"]) for o in options.json()] == [
        ("sedan", 120_000),
        ("minivan", 220_000),
    ]
    assert options.json()[0] | {"price": None} == {
        "vehicle_class": "sedan",
        "max_seats": 3,
        "max_bags": 3,
        "price": None,
    }
    family = await client.post("/v1/quotes/options", json=trip | {"seats": 5, "bags": 6})
    assert [o["vehicle_class"] for o in family.json()] == ["minivan"]
    assert (await client.post("/v1/quotes/options", json=trip | {"seats": 9})).json() == []


async def test_quote_refuses_a_party_too_big_for_the_vehicle(booker, ops, time):
    body = quote_body(time.now + timedelta(days=2), seats=4)
    response = await booker.post("/v1/quotes", body)
    assert response.status_code == 422 and response.json()["error"]["code"] == "over_capacity"

    capacity = {"vehicle_class": "sedan", "max_seats": 4, "max_bags": 3}
    changed = await ops.http.put(
        "/v1/ops/catalog/vehicle-capacities", json=capacity, headers=ops.headers
    )
    assert changed.status_code == 200
    assert (await booker.post("/v1/quotes", body)).status_code == 201


async def test_assignment_needs_a_vehicle_and_is_audited(flow, ops, booker, sign_in):
    booking = await flow.confirmed(booker)
    assign = f"/v1/ops/bookings/{booking['id']}/assign"

    driver = await sign_in("0911234567", role="driver", name="Tadesse Bekele")
    await ops.post(f"/v1/ops/drivers/{driver.id}/verify")
    none = await ops.post(assign, {"driver_id": driver.id})
    assert none.status_code == 422 and none.json()["error"]["code"] == "vehicle_required"

    corolla = (await ops.post(f"/v1/ops/drivers/{driver.id}/vehicles", VEHICLE)).json()
    assert corolla["plate"] == "AA 2-B12345"
    duplicate = await ops.post(f"/v1/ops/drivers/{driver.id}/vehicles", VEHICLE)
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "plate_exists"
    hiace = (
        await ops.post(
            f"/v1/ops/drivers/{driver.id}/vehicles",
            VEHICLE | {"plate": "AA 3-99999", "model": "HiAce", "vehicle_class": "minivan"},
        )
    ).json()
    two = await ops.post(assign, {"driver_id": driver.id})
    assert two.json()["error"]["code"] == "vehicle_required"
    wrong = await ops.post(assign, {"driver_id": driver.id, "vehicle_id": booking["id"]})
    assert wrong.status_code == 404 and wrong.json()["error"]["code"] == "unknown_vehicle"

    done = await ops.post(assign, {"driver_id": driver.id, "vehicle_id": hiace["id"]})
    assert done.status_code == 200 and done.json()["assignment"]["vehicle_id"] == hiace["id"]
    await ops.post(f"/v1/ops/bookings/{booking['id']}/unassign", {"reason": "swap"})

    retired = await ops.http.delete(
        f"/v1/ops/drivers/{driver.id}/vehicles/{hiace['id']}", headers=ops.headers
    )
    assert retired.status_code == 204
    only = await ops.post(assign, {"driver_id": driver.id})  # one car left: no need to name it
    assert only.json()["assignment"]["vehicle_id"] == corolla["id"]
    await ops.post(f"/v1/ops/bookings/{booking['id']}/cancel", {"reason": "test"})

    assert await actions(booking["id"]) == [
        "booking.assign",
        "booking.unassign",
        "booking.assign",
        "booking.cancel",
    ]
    trail = (await ops.get(f"/v1/ops/audit?target_id={booking['id']}")).json()
    assert trail[0]["action"] == "booking.cancel" and trail[0]["actor_id"] == ops.id
    assert trail[0]["details"]["refund"] == {"amount_minor": 120_000, "currency": "ETB"}
    assert trail[1]["details"]["plate"] == "AA 2-B12345"

    listed = (await ops.get("/v1/ops/drivers?verified=true")).json()
    assert [len(d["vehicles"]) for d in listed] == [2]
    photo = await ops.http.patch(
        f"/v1/ops/drivers/{driver.id}",
        json={"photo_url": "https://cdn.example.com/tadesse.jpg"},
        headers=ops.headers,
    )
    assert photo.json()["driver"]["photo_url"] == "https://cdn.example.com/tadesse.jpg"


async def test_booker_and_driver_actions_are_not_in_the_ops_audit_log(flow, booker, ops, driver):
    booking = await flow.assigned(booker, ops, driver)
    await driver.post(f"/v1/driver/bookings/{booking['id']}/drop")
    await booker.post(f"/v1/bookings/{booking['id']}/cancel")
    assert await actions(booking["id"]) == ["booking.assign"]


async def test_ops_routes_are_closed_to_everyone_else(client, booker, driver):
    for path in (
        "/v1/ops/catalog/zone-prices",
        "/v1/ops/drivers",
        "/v1/ops/partners",
        "/v1/ops/audit",
    ):
        assert (await client.get(path)).status_code == 401
        assert (await booker.get(path)).status_code == 403
        assert (await driver.get(path)).status_code == 403


async def test_partner_codes_attribute_bookings_and_commission(
    flow, ops, booker, driver, client, time
):
    created = await ops.post(
        "/v1/ops/partners",
        {"code": "hilton", "kind": "hotel", "name": "Hilton Addis", "commission_pct": 10},
    )
    assert created.status_code == 201 and created.json()["code"] == "HILTON"
    assert (await ops.post("/v1/ops/partners", created.json())).status_code == 409
    public = await client.get("/v1/partners/Hilton")
    assert public.json() == {"code": "HILTON", "kind": "hotel", "name": "Hilton Addis"}

    async def book(code: str):
        quote = await booker.post("/v1/quotes", quote_body(time.now + timedelta(days=3)))
        return await booker.post(
            "/v1/bookings",
            {"quote_id": quote.json()["id"], "riders": RIDERS, "partner_code": code},
        )

    unknown = await book("NOPE")
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "unknown_partner"

    completed = (await book(" hilton ")).json()
    assert completed["partner_code"] == "HILTON"
    pending = (await book("HILTON")).json()
    payment = (await booker.post(f"/v1/bookings/{completed['id']}/confirm")).json()["payment"]
    await flow.pay(payment)
    await ops.post(f"/v1/ops/bookings/{completed['id']}/assign", {"driver_id": driver.id})
    for action in ("accept", "start", "arrive", "pickup", "complete"):
        await driver.post(f"/v1/driver/bookings/{completed['id']}/{action}")

    # A later rate change does not rewrite what was agreed for earlier bookings.
    await ops.http.patch(
        "/v1/ops/partners/HILTON", json={"commission_pct": 50}, headers=ops.headers
    )
    report = (await ops.get("/v1/ops/partners/hilton/attribution")).json()
    assert report == {
        "code": "HILTON",
        "bookings_by_status": {"completed": 1, "quoted": 1},
        "completed_fares": [{"amount_minor": 120_000, "currency": "ETB"}],
        "commission_due": [{"amount_minor": 12_000, "currency": "ETB"}],
    }
    assert pending["status"] == "quoted"

    await ops.http.patch("/v1/ops/partners/HILTON", json={"active": False}, headers=ops.headers)
    assert (await client.get("/v1/partners/HILTON")).status_code == 404
    assert (await book("HILTON")).json()["error"]["code"] == "unknown_partner"
