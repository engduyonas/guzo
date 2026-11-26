"""Business accounts: companies, their bookers and the monthly statement."""

from datetime import datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError

from guzo.audit import service as audit
from guzo.bookings import policies
from guzo.bookings.models import Booking
from guzo.bookings.state_machine import Actor, BookingStatus
from guzo.business.models import Company, CompanyPlan, MemberRole, Membership
from guzo.catalog.models import City
from guzo.common.ids import object_id
from guzo.common.money import Money
from guzo.errors import Conflict, Forbidden, NotFound, Unprocessable
from guzo.identity.models import Role, User

S = BookingStatus


async def get_company(company_id: str) -> Company:
    company = await Company.get(object_id(company_id, "company"))
    if company is None:
        raise NotFound("company not found")
    return company


async def membership_of(user: User) -> Membership | None:
    return await Membership.find_one({"user_id": str(user.id), "active": True})


async def billing_company(user: User) -> Company:
    """The company a booker may bill rides to. Raises when there is none."""
    membership = await membership_of(user)
    company = await Company.get(object_id(membership.company_id)) if membership else None
    if company is None or not company.active:
        raise Forbidden("this account cannot bill rides to a company", code="not_a_company_member")
    return company


async def add_member(
    company: Company, *, phone: str, name: str | None, role: MemberRole, by: User
) -> tuple[Membership, User]:
    """Add a booker by phone. The account is created now if the person has not signed in."""
    user = await User.find_one(User.phone == phone)
    if user is None:
        # Verified on the first sign-in, when the person proves the phone.
        user = User(role=Role.BOOKER, phone=phone, name=name)
        try:
            await user.insert()
        except DuplicateKeyError:
            user = await User.find_one(User.phone == phone)
    if user.role != Role.BOOKER or not user.is_active:
        raise Unprocessable("this phone belongs to a staff or driver account", code="not_a_booker")
    membership = Membership(company_id=str(company.id), user_id=str(user.id), role=role)
    try:
        await membership.insert()
    except DuplicateKeyError as exc:
        raise Conflict("this booker already belongs to a company", code="member_exists") from exc
    if by.role == Role.OPS:
        await audit.record(
            by,
            "company.member_add",
            "company",
            str(company.id),
            {"user_id": str(user.id), "role": role.value},
        )
    return membership, user


async def remove_member(company: Company, user_id: str, *, by: User) -> None:
    result = await Membership.get_pymongo_collection().update_one(
        {"company_id": str(company.id), "user_id": user_id, "active": True},
        {"$set": {"active": False}},
    )
    if result.modified_count != 1:
        raise NotFound("member not found")
    if by.role == Role.OPS:
        await audit.record(
            by, "company.member_remove", "company", str(company.id), {"user_id": user_id}
        )


# --- Monthly statement ------------------------------------------------------


class LineKind(StrEnum):
    RIDE = "ride"
    NO_SHOW = "no_show"
    LATE_CANCEL = "late_cancel"


class StatementLine(BaseModel):
    booking_id: str
    booker_id: str
    scheduled_at: datetime
    pickup: str
    dropoff: str
    riders: list[str]
    kind: LineKind
    included: bool  # covered by the plan's included rides
    charge: Money


class Statement(BaseModel):
    company_id: str
    company_name: str
    month: str  # YYYY-MM, in each city's local time
    plan: CompanyPlan
    rides_used: int
    lines: list[StatementLine]
    extras: Money
    total: Money


def _cancelled_by_booker_at(booking: Booking) -> datetime | None:
    change = booking.status_history[-1]
    if change.to_status == S.CANCELLED and change.actor == Actor.BOOKER:
        return change.at
    return None


def _local_month(when: datetime, timezone: str) -> str:
    return when.astimezone(ZoneInfo(timezone)).strftime("%Y-%m")


async def statement(company: Company, month: str) -> Statement:
    """Rides, no-shows and late cancellations for one month, priced against the plan.

    Rides and no-shows use the included rides in pickup order. After those run out a
    ride costs its fare and a no-show its fee. A late cancellation by the booker costs
    its fee and never uses an included ride; cancellations by ops cost nothing.
    """
    year, number = (int(part) for part in month.split("-"))
    start = datetime(year, number, 1)
    # Wide enough for any city's timezone; each booking is then checked in its own city.
    window = {"$gte": start - timedelta(days=1), "$lt": start + timedelta(days=33)}
    bookings = (
        await Booking.find(
            {
                "company_id": str(company.id),
                "status": {"$in": [S.COMPLETED.value, S.NO_SHOW.value, S.CANCELLED.value]},
                "scheduled_at": window,
            }
        )
        .sort("scheduled_at")
        .to_list()
    )
    timezones = {c.code: c.timezone for c in await City.find_all().to_list()}
    plan = company.plan
    zero = Money(amount_minor=0, currency=plan.monthly_fee.currency)
    remaining = plan.included_rides
    lines: list[StatementLine] = []
    for booking in bookings:
        if _local_month(booking.scheduled_at, timezones[booking.city_id]) != month:
            continue
        included = False
        if booking.status == S.CANCELLED:
            cancelled_at = _cancelled_by_booker_at(booking)
            if cancelled_at is None:
                continue
            refund = policies.cancellation_refund(
                booking.policy, booking.price, booking.scheduled_at, cancelled_at
            )
            charge, kind = booking.price - refund, LineKind.LATE_CANCEL
            if charge.is_zero():
                continue
        else:
            if booking.status == S.NO_SHOW:
                kind = LineKind.NO_SHOW
                charge = booking.price - policies.no_show_refund(booking.policy, booking.price)
            else:
                kind, charge = LineKind.RIDE, booking.price
            if remaining > 0:
                remaining -= 1
                included, charge = True, zero
        lines.append(
            StatementLine(
                booking_id=str(booking.id),
                booker_id=booking.booker_id,
                scheduled_at=booking.scheduled_at,
                pickup=booking.pickup.label,
                dropoff=booking.dropoff.label,
                riders=[rider.name for rider in booking.riders],
                kind=kind,
                included=included,
                charge=charge,
            )
        )
    extras = sum((line.charge for line in lines), zero)
    return Statement(
        company_id=str(company.id),
        company_name=company.name,
        month=month,
        plan=plan,
        rides_used=plan.included_rides - remaining,
        lines=lines,
        extras=extras,
        total=plan.monthly_fee + extras,
    )
