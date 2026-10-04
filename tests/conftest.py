import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

os.environ["GUZO_ENV"] = "test"
os.environ["GUZO_MONGO_URL"] = os.environ.get(
    "GUZO_TEST_MONGO_URL", "mongodb://localhost:27017/?directConnection=true"
)
os.environ["GUZO_MONGO_DB"] = f"guzo_test_{uuid4().hex[:8]}"

import pyotp  # noqa: E402
import pytest  # noqa: E402
from fakeredis import FakeAsyncRedis  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from guzo import db, kv  # noqa: E402
from guzo.catalog.models import VehicleClass, ZonePrice  # noqa: E402
from guzo.catalog.seed import ADDIS, AIRPORT_TRANSFER, BOLE_AIRPORT, seed_catalog  # noqa: E402
from guzo.common import clock  # noqa: E402
from guzo.common.money import Currency, Money  # noqa: E402
from guzo.config import get_settings  # noqa: E402
from guzo.events import handlers  # noqa: E402, F401 - registers outbox handlers
from guzo.identity.cli import create_ops  # noqa: E402
from guzo.main import create_app  # noqa: E402
from guzo.providers.base import PaymentEventStatus  # noqa: E402
from guzo.providers.registry import build_providers, set_providers  # noqa: E402

SEDAN_KAZANCHIS = Money(amount_minor=120_000, currency=Currency.ETB)
OPS_PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="session", autouse=True)
async def database():
    settings = get_settings()
    await db.init_db(settings)
    yield
    await db.get_client().drop_database(settings.mongo_db)
    await db.close_db()


@pytest.fixture(autouse=True)
async def clean_state(database):
    for model in db.DOCUMENT_MODELS:
        await model.get_pymongo_collection().delete_many({})
    kv.set_redis(FakeAsyncRedis(decode_responses=True))
    await seed_catalog()
    await ZonePrice(
        product_code=AIRPORT_TRANSFER,
        city_id=ADDIS,
        zone_id="kazanchis",
        vehicle_class=VehicleClass.SEDAN,
        price=SEDAN_KAZANCHIS,
    ).insert()
    yield
    clock.set_clock(None)
    kv.set_redis(None)


@pytest.fixture(autouse=True)
def providers(clean_state):
    built = build_providers(get_settings())
    set_providers(built)
    yield built
    set_providers(None)


class Clock:
    """Controllable time, so tests can cross quote, payment and waiting deadlines."""

    def __init__(self):
        self.now = datetime.now(UTC)
        clock.set_clock(lambda: self.now)

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def time(clean_state) -> Clock:
    return Clock()


@pytest.fixture
async def client():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


class Session:
    """An authenticated API user."""

    def __init__(self, http: AsyncClient, token: str, user: dict):
        self.http = http
        self.user = user
        self.id = user["id"]
        self.headers = {"Authorization": f"Bearer {token}"}

    async def get(self, path: str, **kwargs):
        return await self.http.get(path, headers=self.headers, **kwargs)

    async def post(self, path: str, json: dict | None = None, headers: dict | None = None):
        return await self.http.post(
            path, json=json or {}, headers={**self.headers, **(headers or {})}
        )


@pytest.fixture
def sign_in(client, providers):
    async def _sign_in(phone: str, role: str = "booker", name: str = "Test User") -> Session:
        response = await client.post("/v1/auth/otp/request", json={"phone": phone})
        assert response.status_code == 202, response.text
        normalized = response.json()["phone"]
        code = providers.notifier.to(normalized)[-1].body.split("code is ")[1][:6]
        response = await client.post(
            "/v1/auth/otp/verify",
            json={"phone": phone, "code": code, "name": name, "role": role},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        return Session(client, data["access_token"], data["user"])

    return _sign_in


@pytest.fixture
async def booker(sign_in) -> Session:
    # A diaspora booker on a US number.
    return await sign_in("+14155552671", name="Selam Tesfaye")


@pytest.fixture
async def ops(client) -> Session:
    user, uri = await create_ops("ops@guzo.example.com", "Ops", OPS_PASSWORD)
    code = pyotp.TOTP(pyotp.parse_uri(uri).secret).now()
    response = await client.post(
        "/v1/auth/ops/login",
        json={"email": "ops@guzo.example.com", "password": OPS_PASSWORD, "totp_code": code},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    return Session(client, data["access_token"], data["user"])


@pytest.fixture
async def driver(sign_in, ops) -> Session:
    session = await sign_in("0911234567", role="driver", name="Tadesse Bekele")
    response = await ops.post(f"/v1/ops/drivers/{session.id}/verify")
    assert response.status_code == 200, response.text
    response = await ops.post(f"/v1/ops/drivers/{session.id}/vehicles", VEHICLE)
    assert response.status_code == 201, response.text
    return session


def quote_body(scheduled_at: datetime, /, **overrides) -> dict:
    body = {
        "product_code": AIRPORT_TRANSFER,
        "pickup": BOLE_AIRPORT.model_dump(mode="json"),
        "dropoff": {
            "label": "Hilton Addis Ababa",
            "lat": 9.0192,
            "lng": 38.7636,
            "kind": "address",
            "zone_id": "kazanchis",
        },
        "scheduled_at": scheduled_at.isoformat(),
        "vehicle_class": "sedan",
        "seats": 3,
        "bags": 3,
        "flight": {"number": "ET501", "scheduled_arrival": scheduled_at.isoformat()},
    }
    return {**body, **overrides}


VEHICLE = {
    "plate": "aa 2-b12345",
    "make": "Toyota",
    "model": "Corolla",
    "color": "White",
    "vehicle_class": "sedan",
}

RIDERS = [
    {"name": "Almaz Tesfaye", "phone": "+251911000111"},
    {"name": "Selam Tesfaye", "phone": "+14155552671", "is_booker": True},
]


@pytest.fixture
def flow(client, providers, time):
    """Helpers that drive a booking through the API the way the clients will."""

    class Flow:
        async def quoted(self, booker: Session, *, days_ahead: float = 7, **kwargs) -> dict:
            scheduled_at = time.now + timedelta(days=days_ahead)
            quote = await booker.post("/v1/quotes", quote_body(scheduled_at))
            assert quote.status_code == 201, quote.text
            response = await booker.post(
                "/v1/bookings", {"quote_id": quote.json()["id"], "riders": RIDERS}, **kwargs
            )
            assert response.status_code == 201, response.text
            return response.json()

        async def awaiting_payment(self, booker: Session, **kwargs) -> tuple[dict, dict]:
            booking = await self.quoted(booker, **kwargs)
            response = await booker.post(f"/v1/bookings/{booking['id']}/confirm")
            assert response.status_code == 200, response.text
            return response.json()["booking"], response.json()["payment"]

        async def pay(self, payment: dict, status: str = "paid"):
            body, headers = providers.payments.build_webhook(
                payment["provider_ref"], PaymentEventStatus(status)
            )
            return await client.post("/v1/payments/webhooks/fake", content=body, headers=headers)

        async def confirmed(self, booker: Session, **kwargs) -> dict:
            booking, payment = await self.awaiting_payment(booker, **kwargs)
            assert (await self.pay(payment)).status_code == 204
            return (await booker.get(f"/v1/bookings/{booking['id']}")).json()

        async def assigned(self, booker: Session, ops: Session, driver: Session, **kwargs) -> dict:
            booking = await self.confirmed(booker, **kwargs)
            response = await ops.post(
                f"/v1/ops/bookings/{booking['id']}/assign", {"driver_id": driver.id}
            )
            assert response.status_code == 200, response.text
            return response.json()

        async def arrived(self, booker: Session, ops: Session, driver: Session, **kwargs) -> dict:
            booking = await self.assigned(booker, ops, driver, **kwargs)
            for action in ("accept", "start", "arrive"):
                response = await driver.post(f"/v1/driver/bookings/{booking['id']}/{action}")
                assert response.status_code == 200, response.text
            return response.json()

    return Flow()
