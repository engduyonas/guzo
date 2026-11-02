import logging
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-jwt-secret-change-me-before-deploying"  # noqa: S105
ENVIRONMENTS = {"dev", "test", "staging", "production"}
DEV_WEBHOOK_SECRET = "dev-only-webhook-secret"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GUZO_", env_file=".env", extra="ignore")

    env: str = "dev"
    mongo_url: str = "mongodb://localhost:27017/?directConnection=true"
    mongo_db: str = "guzo"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = DEV_JWT_SECRET
    access_token_minutes: int = 60 * 24

    default_phone_region: str = "ET"
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_requests_per_phone_per_hour: int = 5
    otp_requests_per_ip_per_hour: int = 30
    login_attempts_per_15_minutes: int = 10

    # Where clients reach this service; used in links sent to people.
    public_base_url: str = "http://localhost:8000"

    quote_ttl_minutes: int = 15
    payment_ttl_minutes: int = 30

    # Oldest app builds still allowed to talk to this API. Raise to retire old versions.
    min_android_version: str = "0.0.0"
    min_ios_version: str = "0.0.0"

    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.0

    payment_provider: str = "fake"
    sms_provider: str = "fake"
    flight_provider: str = "fake"
    identity_provider: str = "fake"
    fake_payment_webhook_secret: str = DEV_WEBHOOK_SECRET

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @model_validator(mode="after")
    def _known_environment(self) -> "Settings":
        if self.env not in ENVIRONMENTS:
            raise ValueError(f"GUZO_ENV must be one of {sorted(ENVIRONMENTS)}")
        return self

    @model_validator(mode="after")
    def _no_dev_defaults_in_production(self) -> "Settings":
        if not self.is_production:
            return self
        if self.jwt_secret == DEV_JWT_SECRET:
            raise ValueError("GUZO_JWT_SECRET must be set in production")
        if not self.public_base_url.startswith("https://"):
            raise ValueError("GUZO_PUBLIC_BASE_URL must be an https URL in production")
        fakes = [
            name for name in ("payment_provider", "sms_provider") if getattr(self, name) == "fake"
        ]
        if fakes:
            raise ValueError(f"fake providers are not allowed in production: {fakes}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
