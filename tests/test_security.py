"""Access rules checked across every route, so a new endpoint cannot be left open."""

import re

import pytest

from guzo.config import Settings
from guzo.main import create_app

# Routes anyone may call. Everything else under /v1 must demand a signed-in user.
PUBLIC = {
    ("GET", "/health"),
    ("POST", "/v1/auth/otp/request"),
    ("POST", "/v1/auth/otp/verify"),
    ("POST", "/v1/auth/ops/login"),
    ("GET", "/v1/catalog/products"),
    ("GET", "/v1/catalog/zones"),
    ("GET", "/v1/catalog/places"),
    ("POST", "/v1/quotes/options"),
    ("GET", "/v1/partners/{code}"),
    ("GET", "/v1/meta/client"),
    ("POST", "/v1/payments/webhooks/{provider}"),  # authenticated by provider signature
}
ANY_ID = "0" * 24


def api_routes() -> list[tuple[str, str]]:
    spec = create_app().openapi()
    return sorted(
        (method.upper(), path) for path, methods in spec["paths"].items() for method in methods
    )


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", ANY_ID, path)


async def call(session_or_client, method: str, path: str, headers: dict | None = None):
    http = getattr(session_or_client, "http", session_or_client)
    auth = getattr(session_or_client, "headers", {})
    return await http.request(method, concrete(path), json={}, headers={**auth, **(headers or {})})


def test_the_public_list_is_exact():
    known = set(api_routes())
    assert known >= PUBLIC, f"stale entries: {PUBLIC - known}"


async def test_every_other_route_needs_a_valid_token(client):
    for method, path in api_routes():
        if (method, path) in PUBLIC:
            continue
        anonymous = await call(client, method, path)
        assert anonymous.status_code == 401, f"{method} {path} is open to anyone"
        forged = await call(client, method, path, {"Authorization": "Bearer not.a.token"})
        assert forged.status_code == 401, f"{method} {path} accepted a bad token"


async def test_ops_routes_refuse_bookers_and_drivers(booker, driver):
    ops_routes = [(m, p) for m, p in api_routes() if p.startswith("/v1/ops/")]
    assert len(ops_routes) > 20
    for method, path in ops_routes:
        for session in (booker, driver):
            response = await call(session, method, path)
            assert response.status_code == 403, f"{method} {path} let a {session.user['role']} in"


async def test_driver_and_booker_routes_refuse_the_other_roles(booker, driver, ops):
    for method, path in api_routes():
        if path.startswith("/v1/driver/"):
            for session in (booker, ops):
                assert (await call(session, method, path)).status_code == 403, f"{method} {path}"
        if path.startswith(("/v1/bookings", "/v1/quotes")) and (method, path) not in PUBLIC:
            for session in (driver, ops):
                assert (await call(session, method, path)).status_code == 403, f"{method} {path}"


async def test_a_deactivated_account_loses_access_at_once(driver, ops):
    assert (await driver.get("/v1/driver/bookings")).status_code == 200
    off = await ops.http.patch(
        f"/v1/ops/drivers/{driver.id}", json={"is_active": False}, headers=ops.headers
    )
    assert off.status_code == 200
    assert (await driver.get("/v1/driver/bookings")).status_code == 401


async def test_webhook_without_a_signature_is_rejected(client):
    response = await client.post("/v1/payments/webhooks/fake", content=b"{}")
    assert response.status_code == 401


def test_production_refuses_unsafe_settings():
    safe = {
        "env": "production",
        "jwt_secret": "x" * 40,
        "public_base_url": "https://api.guzo.example.com",
        "payment_provider": "real",
        "sms_provider": "real",
    }
    Settings(**safe)
    for unsafe in (
        {"jwt_secret": Settings().jwt_secret},
        {"payment_provider": "fake"},
        {"sms_provider": "fake"},
        {"public_base_url": "http://api.guzo.example.com"},
    ):
        with pytest.raises(ValueError):
            Settings(**(safe | unsafe))
    with pytest.raises(ValueError):
        Settings(env="prod")  # a typo must not silently run as a non-production env


async def test_app_version_gate(client, monkeypatch):
    from guzo.config import get_settings

    monkeypatch.setattr(get_settings(), "min_android_version", "1.4.0")

    async def check(platform: str, version: str):
        r = await client.get(f"/v1/meta/client?platform={platform}&version={version}")
        return r.status_code, r.json().get("update_required")

    assert await check("android", "1.3.9") == (200, True)
    assert await check("android", "1.4") == (200, False)
    assert await check("android", "1.10.0") == (200, False)  # numeric, not text, comparison
    assert await check("ios", "0.1.0") == (200, False)
    assert (await check("android", "latest"))[0] == 422
    assert (await check("windows", "1.0.0"))[0] == 422
