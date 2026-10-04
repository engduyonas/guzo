from collections.abc import Callable
from datetime import UTC, datetime


def _system_now() -> datetime:
    return datetime.now(UTC)


_now: Callable[[], datetime] = _system_now


def utcnow() -> datetime:
    # Mongo keeps millisecond precision; truncate so values round-trip unchanged.
    now = _now()
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def set_clock(fn: Callable[[], datetime] | None) -> None:
    """Override the time source (tests only). Pass None to restore the system clock."""
    global _now
    _now = fn or _system_now
