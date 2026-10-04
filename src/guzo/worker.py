"""Background worker: arq guzo.worker.WorkerSettings

Nothing slow or retryable runs inside a request handler. Requests write to Mongo
(including the outbox); this process delivers events and expires stale bookings.
"""

from arq import cron
from arq.connections import RedisSettings

from guzo.bookings.service import expire_due
from guzo.config import configure_logging, get_settings
from guzo.db import close_db, init_db
from guzo.events import handlers  # noqa: F401 - registers the outbox handlers
from guzo.events.outbox import deliver_pending
from guzo.monitoring import init_monitoring
from guzo.providers.registry import build_providers, set_providers


async def startup(ctx: dict) -> None:
    configure_logging()
    settings = get_settings()
    init_monitoring(settings)
    await init_db(settings)
    set_providers(build_providers(settings))


async def shutdown(ctx: dict) -> None:
    await close_db()


async def deliver_outbox(ctx: dict) -> int:
    return await deliver_pending()


async def expire_bookings(ctx: dict) -> int:
    return await expire_due()


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = startup
    on_shutdown = shutdown
    cron_jobs = [
        cron(deliver_outbox, second=set(range(0, 60, 5)), run_at_startup=True),
        cron(expire_bookings, second=0),
    ]
