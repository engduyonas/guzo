"""Seed the Addis catalogue: python -m guzo.catalog.seed [--example-prices].

Idempotent. Zone coordinates and policy numbers are starting points for ops to edit.
Prices are only written with --example-prices and are placeholders, not market data.
"""

import argparse
import asyncio

from guzo.catalog.models import (
    City,
    KnownPlace,
    Place,
    PlaceKind,
    PricingStrategyName,
    Product,
    ProductPolicy,
    VehicleClass,
    Zone,
    ZonePrice,
)
from guzo.common.money import Currency, Money
from guzo.config import get_settings
from guzo.db import close_db, init_db

ADDIS = "addis"
AIRPORT_TRANSFER = "airport_transfer"
BOLE_AIRPORT = Place(
    label="Bole International Airport",
    lat=8.9779,
    lng=38.7993,
    kind=PlaceKind.AIRPORT,
    zone_id="bole",
)
ZONES = {"bole": "Bole", "kazanchis": "Kazanchis", "piassa": "Piassa", "cmc": "CMC"}
EXAMPLE_PRICES_ETB = {
    VehicleClass.SEDAN: {"bole": 800, "kazanchis": 1200, "piassa": 1500, "cmc": 1500},
    VehicleClass.MINIVAN: {"bole": 1300, "kazanchis": 1800, "piassa": 2200, "cmc": 2200},
}


async def _upsert(model, query: dict, doc) -> None:
    if await model.find_one(query) is None:
        await doc.insert()


async def seed_catalog() -> None:
    await _upsert(
        City,
        {"code": ADDIS},
        City(
            code=ADDIS,
            name="Addis Ababa",
            timezone="Africa/Addis_Ababa",
            default_currency=Currency.ETB,
        ),
    )
    for code, name in ZONES.items():
        await _upsert(
            Zone, {"city_id": ADDIS, "code": code}, Zone(code=code, city_id=ADDIS, name=name)
        )
    await _upsert(
        KnownPlace,
        {"city_id": ADDIS, "code": "bole_airport"},
        KnownPlace(code="bole_airport", city_id=ADDIS, place=BOLE_AIRPORT),
    )
    await _upsert(
        Product,
        {"city_id": ADDIS, "code": AIRPORT_TRANSFER},
        Product(
            code=AIRPORT_TRANSFER,
            city_id=ADDIS,
            name="Airport transfer",
            pricing_strategy=PricingStrategyName.ZONE_FIXED,
            policy=ProductPolicy(
                free_cancel_hours=24,
                free_wait_minutes=60,
                no_show_fee_pct=100,
                late_cancel_fee_pct=100,
            ),
            vehicle_classes=[VehicleClass.SEDAN, VehicleClass.MINIVAN],
        ),
    )


async def seed_example_prices() -> None:
    for vehicle_class, by_zone in EXAMPLE_PRICES_ETB.items():
        for zone_id, etb in by_zone.items():
            key = {
                "city_id": ADDIS,
                "product_code": AIRPORT_TRANSFER,
                "zone_id": zone_id,
                "vehicle_class": vehicle_class.value,
            }
            price = Money(amount_minor=etb * 100, currency=Currency.ETB)
            await _upsert(ZonePrice, key, ZonePrice(**key, price=price))


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Seed the Guzo catalogue")
    parser.add_argument("--example-prices", action="store_true")
    args = parser.parse_args()
    await init_db(get_settings())
    try:
        await seed_catalog()
        if args.example_prices:
            await seed_example_prices()
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(_main())
