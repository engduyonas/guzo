import pyotp
import pytest

from guzo.identity.cli import create_ops

from .conftest import OPS_PASSWORD


async def request_code(client, providers, phone: str) -> str:
    response = await client.post("/v1/auth/otp/request", json={"phone": phone})
    assert response.status_code == 202, response.text
    return providers.notifier.to(response.json()["phone"])[-1].body.split("code is ")[1][:6]


async def test_first_sign_in_registers_and_returns_the_full_user(client, providers):
    code = await request_code(client, providers, "+14155552671")
    response = await client.post(
        "/v1/auth/otp/verify", json={"phone": "+14155552671", "code": code, "name": "Selam"}
    )
    assert response.status_code == 200, response.text
    user = response.json()["user"]
    assert user["phone"] == "+14155552671" and user["role"] == "booker"
    assert user["is_verified"] is True and user["total_ratings"] == 0

    me = await client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"}
    )
    assert me.status_code == 200 and me.json() == user


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("0911234567", "+251911234567"),  # local Ethiopian format
        ("+251 91 123 4567", "+251911234567"),
        ("+44 7911 123456", "+447911123456"),  # diaspora number
        ("+1 (415) 555-2671", "+14155552671"),
    ],
)
async def test_phone_numbers_are_stored_in_international_format(client, providers, typed, stored):
    code = await request_code(client, providers, typed)
    response = await client.post("/v1/auth/otp/verify", json={"phone": typed, "code": code})
    assert response.json()["user"]["phone"] == stored


async def test_signing_in_again_reuses_the_account(sign_in):
    first = await sign_in("0911234567", role="driver")
    second = await sign_in("+251911234567")
    assert first.id == second.id and second.user["role"] == "driver"
    assert second.user["is_verified"] is False  # drivers wait for ops vetting


async def test_invalid_phone_is_rejected(client):
    response = await client.post("/v1/auth/otp/request", json={"phone": "12345"})
    assert response.status_code == 422


async def test_wrong_code_fails_and_locks_out_after_five_tries(client, providers):
    code = await request_code(client, providers, "+251911234567")
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        response = await client.post(
            "/v1/auth/otp/verify", json={"phone": "+251911234567", "code": wrong}
        )
        assert response.status_code == 401
    # Even the right code is refused now; a new one must be requested.
    locked = await client.post("/v1/auth/otp/verify", json={"phone": "+251911234567", "code": code})
    assert locked.status_code == 429


async def test_a_code_works_once(client, providers):
    code = await request_code(client, providers, "+251911234567")
    body = {"phone": "+251911234567", "code": code}
    assert (await client.post("/v1/auth/otp/verify", json=body)).status_code == 200
    assert (await client.post("/v1/auth/otp/verify", json=body)).status_code == 401


async def test_otp_requests_are_rate_limited_per_phone(client, providers):
    for _ in range(5):
        assert (
            await client.post("/v1/auth/otp/request", json={"phone": "+251911234567"})
        ).status_code == 202
    limited = await client.post("/v1/auth/otp/request", json={"phone": "+251911234567"})
    assert limited.status_code == 429
    assert len(providers.notifier.sent) == 5


async def test_ops_login_needs_password_and_totp(client):
    _, uri = await create_ops("ops@guzo.example.com", "Ops", OPS_PASSWORD)
    totp = pyotp.TOTP(pyotp.parse_uri(uri).secret)
    login = {"email": "Ops@Guzo.example.com", "password": OPS_PASSWORD, "totp_code": totp.now()}

    bad_password = await client.post("/v1/auth/ops/login", json={**login, "password": "nope"})
    wrong = "000000" if totp.now() != "000000" else "111111"
    bad_totp = await client.post("/v1/auth/ops/login", json={**login, "totp_code": wrong})
    assert bad_password.status_code == 401 and bad_totp.status_code == 401
    missing = await client.post(
        "/v1/auth/ops/login", json={"email": login["email"], "password": OPS_PASSWORD}
    )
    assert missing.status_code == 422

    ok = await client.post("/v1/auth/ops/login", json=login)
    assert ok.status_code == 200 and ok.json()["user"]["role"] == "ops"


async def test_ops_login_is_rate_limited(client):
    body = {"email": "ops@guzo.example.com", "password": "x", "totp_code": "123456"}
    for _ in range(10):
        assert (await client.post("/v1/auth/ops/login", json=body)).status_code == 401
    assert (await client.post("/v1/auth/ops/login", json=body)).status_code == 429


async def test_protected_routes_need_a_valid_token(client):
    assert (await client.get("/v1/bookings")).status_code == 401
    bad = await client.get("/v1/bookings", headers={"Authorization": "Bearer nonsense"})
    assert bad.status_code == 401
