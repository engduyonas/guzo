from redis.asyncio import Redis

from guzo.errors import RateLimited

_redis: Redis | None = None


def init_redis(url: str) -> Redis:
    global _redis
    _redis = Redis.from_url(url, decode_responses=True)
    return _redis


def set_redis(redis: Redis | None) -> None:
    global _redis
    _redis = redis


def get_redis() -> Redis:
    if _redis is None:
        raise RuntimeError("redis is not initialised")
    return _redis


async def rate_limit(key: str, *, limit: int, window_seconds: int) -> None:
    """Fixed-window counter. Raises RateLimited once `limit` hits are used up."""
    redis = get_redis()
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window_seconds)
    if count > limit:
        raise RateLimited("too many attempts, try again later")
