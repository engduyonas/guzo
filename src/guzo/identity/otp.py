import hashlib
import hmac
import secrets

from guzo.config import get_settings
from guzo.errors import RateLimited, Unauthorized
from guzo.kv import get_redis, rate_limit
from guzo.providers.registry import get_providers


def _code_key(phone: str) -> str:
    return f"otp:code:{phone}"


def _attempts_key(phone: str) -> str:
    return f"otp:attempts:{phone}"


def _digest(phone: str, code: str) -> str:
    secret = get_settings().jwt_secret.encode()
    return hmac.new(secret, f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


async def request_code(phone: str, client_ip: str) -> int:
    """Send a one-time code by SMS. Returns the code lifetime in seconds."""
    settings = get_settings()
    await rate_limit(
        f"otp:req:ip:{client_ip}",
        limit=settings.otp_requests_per_ip_per_hour,
        window_seconds=3600,
    )
    await rate_limit(
        f"otp:req:phone:{phone}",
        limit=settings.otp_requests_per_phone_per_hour,
        window_seconds=3600,
    )
    code = f"{secrets.randbelow(10**6):06d}"
    redis = get_redis()
    await redis.set(_code_key(phone), _digest(phone, code), ex=settings.otp_ttl_seconds)
    await redis.delete(_attempts_key(phone))
    await get_providers().notifier.send(
        to=phone,
        body=f"Your Guzo code is {code}. It expires in {settings.otp_ttl_seconds // 60} minutes.",
    )
    return settings.otp_ttl_seconds


async def verify_code(phone: str, code: str) -> None:
    """Raises Unauthorized on a wrong or expired code, RateLimited after too many tries."""
    settings = get_settings()
    redis = get_redis()
    attempts = await redis.incr(_attempts_key(phone))
    if attempts == 1:
        await redis.expire(_attempts_key(phone), settings.otp_ttl_seconds)
    if attempts > settings.otp_max_attempts:
        await redis.delete(_code_key(phone))
        raise RateLimited("too many wrong codes, request a new one")
    expected = await redis.get(_code_key(phone))
    if expected is None or not hmac.compare_digest(expected, _digest(phone, code)):
        raise Unauthorized("wrong or expired code", code="invalid_code")
    await redis.delete(_code_key(phone), _attempts_key(phone))
