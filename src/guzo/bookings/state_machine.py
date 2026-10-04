"""The one booking lifecycle, shared by every product.

Products differ in which states they skip, not in having their own machines. This
table is the only place that says which moves exist and who may make them.
"""

from enum import StrEnum

from guzo.errors import Conflict, Forbidden


class BookingStatus(StrEnum):
    DRAFT = "draft"
    QUOTED = "quoted"
    AWAITING_PAYMENT = "awaiting_payment"
    CONFIRMED = "confirmed"
    ASSIGNED = "assigned"
    EN_ROUTE = "en_route"
    ARRIVED = "arrived"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    NO_SHOW = "no_show"


class Actor(StrEnum):
    BOOKER = "booker"
    DRIVER = "driver"
    OPS = "ops"
    SYSTEM = "system"


S = BookingStatus
_BOOKER, _DRIVER, _OPS, _SYSTEM = Actor.BOOKER, Actor.DRIVER, Actor.OPS, Actor.SYSTEM

TRANSITIONS: dict[BookingStatus, dict[BookingStatus, frozenset[Actor]]] = {
    S.DRAFT: {S.QUOTED: frozenset({_SYSTEM})},
    S.QUOTED: {
        S.AWAITING_PAYMENT: frozenset({_BOOKER}),
        S.EXPIRED: frozenset({_SYSTEM}),
    },
    S.AWAITING_PAYMENT: {
        S.CONFIRMED: frozenset({_SYSTEM}),
        S.EXPIRED: frozenset({_SYSTEM}),
    },
    S.CONFIRMED: {
        S.ASSIGNED: frozenset({_OPS}),
        S.CANCELLED: frozenset({_BOOKER, _OPS}),
    },
    S.ASSIGNED: {
        S.CONFIRMED: frozenset({_DRIVER, _OPS}),  # driver drops out or ops unassigns
        S.EN_ROUTE: frozenset({_DRIVER}),
        S.CANCELLED: frozenset({_BOOKER, _OPS}),
    },
    S.EN_ROUTE: {S.ARRIVED: frozenset({_DRIVER})},
    S.ARRIVED: {
        S.IN_PROGRESS: frozenset({_DRIVER}),
        S.NO_SHOW: frozenset({_DRIVER, _OPS}),
    },
    S.IN_PROGRESS: {S.COMPLETED: frozenset({_DRIVER})},
    S.COMPLETED: {},
    S.CANCELLED: {},
    S.EXPIRED: {},
    S.NO_SHOW: {},
}

TERMINAL = frozenset(status for status, moves in TRANSITIONS.items() if not moves)


def check_transition(current: BookingStatus, target: BookingStatus, actor: Actor) -> None:
    allowed = TRANSITIONS[current].get(target)
    if allowed is None:
        raise Conflict(
            f"a {current.value} booking cannot become {target.value}", code="invalid_transition"
        )
    if actor not in allowed:
        raise Forbidden(f"{actor.value} cannot move a booking to {target.value}")
