"""Refund and waiting rules. Pure functions of the product policy, never in endpoints."""

from datetime import datetime, timedelta

from guzo.catalog.models import ProductPolicy
from guzo.common.money import Money


def cancellation_refund(
    policy: ProductPolicy, price: Money, scheduled_at: datetime, now: datetime
) -> Money:
    """Full refund outside the free-cancel window, otherwise the fare minus the late fee."""
    if scheduled_at - now >= timedelta(hours=policy.free_cancel_hours):
        return price
    return price - price.percent(policy.late_cancel_fee_pct)


def no_show_refund(policy: ProductPolicy, price: Money) -> Money:
    return price - price.percent(policy.no_show_fee_pct)


def no_show_allowed_from(
    policy: ProductPolicy, scheduled_at: datetime, arrived_at: datetime
) -> datetime:
    """Free waiting starts at the scheduled pickup, or when the driver arrives if later."""
    return max(scheduled_at, arrived_at) + timedelta(minutes=policy.free_wait_minutes)
