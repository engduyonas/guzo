from guzo.config import Settings
from guzo.providers.base import Providers
from guzo.providers.fakes import (
    FakeFlightStatus,
    FakeIdentityCheck,
    FakeNotifier,
    FakePaymentProvider,
)

_providers: Providers | None = None


def build_providers(settings: Settings) -> Providers:
    """Pick implementations from settings. Only fakes exist until M1 chooses real providers."""
    chosen = {
        "payment_provider": settings.payment_provider,
        "sms_provider": settings.sms_provider,
        "flight_provider": settings.flight_provider,
        "identity_provider": settings.identity_provider,
    }
    unknown = {k: v for k, v in chosen.items() if v != "fake"}
    if unknown:
        raise ValueError(f"no implementation for providers: {unknown}")
    return Providers(
        payments=FakePaymentProvider(
            settings.fake_payment_webhook_secret, settings.public_base_url
        ),
        notifier=FakeNotifier(),
        flights=FakeFlightStatus(),
        identity=FakeIdentityCheck(),
    )


def set_providers(providers: Providers | None) -> None:
    global _providers
    _providers = providers


def get_providers() -> Providers:
    if _providers is None:
        raise RuntimeError("providers are not initialised")
    return _providers
