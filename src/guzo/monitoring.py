import sentry_sdk

from guzo.config import Settings


def init_monitoring(settings: Settings) -> bool:
    """Report errors to Sentry when a DSN is configured. Returns whether it is on."""
    if not settings.sentry_dsn:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.env,
        # Phone numbers and names stay out of error reports.
        send_default_pii=False,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )
    return True
