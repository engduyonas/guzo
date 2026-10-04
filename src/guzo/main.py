from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from guzo.audit.routes import router as audit_router
from guzo.bookings.routes import driver_router, ops_router
from guzo.bookings.routes import router as bookings_router
from guzo.catalog.ops_routes import router as ops_catalog_router
from guzo.catalog.routes import router as catalog_router
from guzo.config import configure_logging, get_settings
from guzo.db import close_db, get_client, init_db
from guzo.errors import DomainError
from guzo.fleet.routes import router as fleet_router
from guzo.identity.routes import router as auth_router
from guzo.kv import get_redis, init_redis
from guzo.partners.routes import router as partners_router
from guzo.payments.routes import router as payments_router
from guzo.pricing.routes import router as quotes_router
from guzo.providers.registry import build_providers, set_providers

API_VERSION = "v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    await init_db(settings)
    redis = init_redis(settings.redis_url)
    set_providers(build_providers(settings))
    yield
    await redis.aclose()
    await close_db()


class Health(BaseModel):
    status: str


def create_app() -> FastAPI:
    app = FastAPI(
        title="Guzo API",
        version="1.0.0",
        lifespan=lifespan,
        # Short operation ids give the generated Dart client readable method names.
        generate_unique_id_function=lambda route: route.name,
    )

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.get("/health", tags=["meta"])
    async def health() -> Health:
        await get_client().admin.command("ping")
        await get_redis().ping()
        return Health(status="ok")

    v1 = APIRouter(prefix=f"/{API_VERSION}")
    for router in (
        auth_router,
        catalog_router,
        quotes_router,
        bookings_router,
        driver_router,
        ops_router,
        ops_catalog_router,
        fleet_router,
        partners_router,
        audit_router,
        payments_router,
    ):
        v1.include_router(router)
    app.include_router(v1)
    return app


app = create_app()
