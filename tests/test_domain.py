"""Rules that do not need the API: money, the transition table, refund policy."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from guzo.bookings import policies
from guzo.bookings.state_machine import TERMINAL, TRANSITIONS, Actor, check_transition
from guzo.bookings.state_machine import BookingStatus as S
from guzo.catalog.models import ProductPolicy
from guzo.common.money import Currency, Money
from guzo.errors import Conflict, Forbidden


def etb(amount_minor: int) -> Money:
    return Money(amount_minor=amount_minor, currency=Currency.ETB)


@pytest.mark.parametrize("bad", [1200.0, 1200.5, "1200", None])
def test_money_rejects_anything_but_integers(bad):
    with pytest.raises(ValidationError):
        Money(amount_minor=bad, currency="ETB")


def test_money_arithmetic_is_exact_and_currency_safe():
    assert sum([etb(10)] * 3, etb(0)) == etb(30)  # 0.1 + 0.1 + 0.1 drifts as floats
    assert etb(99_999).percent(18) == etb(18_000)  # 17999.82 rounds half up
    assert etb(250).percent(50) == etb(125)
    assert etb(100) - etb(100).percent(100) == etb(0)
    with pytest.raises(ValueError, match="currency mismatch"):
        etb(1) + Money(amount_minor=1, currency=Currency.USD)


def test_transition_table_matches_the_documented_lifecycle():
    edges = {(a.value, b.value) for a, moves in TRANSITIONS.items() for b in moves}
    assert edges == {
        ("draft", "quoted"),
        ("quoted", "awaiting_payment"),
        ("quoted", "expired"),
        ("awaiting_payment", "confirmed"),
        ("awaiting_payment", "expired"),
        ("confirmed", "assigned"),
        ("assigned", "confirmed"),
        ("assigned", "en_route"),
        ("en_route", "arrived"),
        ("arrived", "in_progress"),
        ("arrived", "no_show"),
        ("in_progress", "completed"),
        ("confirmed", "cancelled"),
        ("assigned", "cancelled"),
    }
    assert set(TRANSITIONS) == set(S)
    assert {S.COMPLETED, S.CANCELLED, S.EXPIRED, S.NO_SHOW} == TERMINAL


def test_transitions_check_both_the_move_and_who_makes_it():
    check_transition(S.CONFIRMED, S.CANCELLED, Actor.BOOKER)
    with pytest.raises(Conflict):
        check_transition(S.COMPLETED, S.CANCELLED, Actor.OPS)
    with pytest.raises(Conflict):
        check_transition(S.EN_ROUTE, S.CANCELLED, Actor.BOOKER)
    with pytest.raises(Forbidden):
        check_transition(S.AWAITING_PAYMENT, S.CONFIRMED, Actor.BOOKER)  # only a payment can
    with pytest.raises(Forbidden):
        check_transition(S.ASSIGNED, S.EN_ROUTE, Actor.OPS)


def test_refund_policy():
    policy = ProductPolicy(
        free_cancel_hours=24, free_wait_minutes=60, no_show_fee_pct=100, late_cancel_fee_pct=50
    )
    pickup = datetime(2026, 12, 20, 9, 0, tzinfo=UTC)
    fare = etb(120_000)
    assert policies.cancellation_refund(policy, fare, pickup, pickup - timedelta(hours=24)) == fare
    late = policies.cancellation_refund(policy, fare, pickup, pickup - timedelta(hours=23))
    assert late == etb(60_000)
    assert policies.no_show_refund(policy, fare) == etb(0)

    # Waiting is free for an hour after the scheduled time, or after a late arrival.
    early_arrival = pickup - timedelta(minutes=10)
    late_arrival = pickup + timedelta(minutes=20)
    assert policies.no_show_allowed_from(policy, pickup, early_arrival) == pickup + timedelta(
        hours=1
    )
    assert policies.no_show_allowed_from(policy, pickup, late_arrival) == late_arrival + timedelta(
        hours=1
    )
