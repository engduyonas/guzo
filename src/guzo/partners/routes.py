from fastapi import APIRouter
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from guzo.audit import service as audit
from guzo.bookings.models import Booking
from guzo.bookings.state_machine import BookingStatus
from guzo.common.money import Money
from guzo.errors import Conflict, NotFound
from guzo.identity.deps import Ops
from guzo.partners.models import Partner, PartnerKind, normalize_code

router = APIRouter(tags=["partners"])


class PartnerPublic(BaseModel):
    code: str
    kind: PartnerKind
    name: str


class PartnerResponse(PartnerPublic):
    commission_pct: int
    active: bool


class PartnerCreate(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{3,40}$")
    kind: PartnerKind
    name: str = Field(min_length=1, max_length=120)
    commission_pct: int = Field(ge=0, le=100)


class PartnerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    commission_pct: int | None = Field(default=None, ge=0, le=100)
    active: bool | None = None


class PartnerAttribution(BaseModel):
    """What a partner's code has brought in. Commission is owed on completed trips only."""

    code: str
    bookings_by_status: dict[str, int]
    completed_fares: list[Money]
    commission_due: list[Money]


def _out(partner: Partner) -> PartnerResponse:
    return PartnerResponse(**partner.model_dump(exclude={"id", "created_at"}))


async def _get(code: str) -> Partner:
    partner = await Partner.find_one(Partner.code == normalize_code(code))
    if partner is None:
        raise NotFound("partner not found")
    return partner


@router.get("/partners/{code}")
async def get_partner(code: str) -> PartnerPublic:
    """Lets a booking page check a referral code and show who it belongs to."""
    partner = await _get(code)
    if not partner.active:
        raise NotFound("partner not found")
    return PartnerPublic(code=partner.code, kind=partner.kind, name=partner.name)


@router.get("/ops/partners", tags=["ops"])
async def list_partners(user: Ops) -> list[PartnerResponse]:
    return [_out(p) for p in await Partner.find_all().sort("code").to_list()]


@router.post("/ops/partners", status_code=201, tags=["ops"])
async def create_partner(body: PartnerCreate, user: Ops) -> PartnerResponse:
    partner = Partner(**body.model_dump() | {"code": normalize_code(body.code)})
    try:
        await partner.insert()
    except DuplicateKeyError as exc:
        raise Conflict("this partner code is taken", code="partner_exists") from exc
    await audit.record(user, "partner.create", "partner", partner.code, body.model_dump())
    return _out(partner)


@router.patch("/ops/partners/{code}", tags=["ops"])
async def update_partner(code: str, body: PartnerUpdate, user: Ops) -> PartnerResponse:
    partner = await _get(code)
    changes = body.model_dump(exclude_none=True)
    for field, value in changes.items():
        setattr(partner, field, value)
    await partner.save()
    await audit.record(user, "partner.update", "partner", partner.code, changes)
    return _out(partner)


@router.get("/ops/partners/{code}/attribution", tags=["ops"])
async def partner_attribution(code: str, user: Ops) -> PartnerAttribution:
    partner = await _get(code)
    bookings = await Booking.find(Booking.partner_code == partner.code).to_list()
    by_status: dict[str, int] = {}
    fares: dict[str, Money] = {}
    due: dict[str, Money] = {}
    for booking in bookings:
        by_status[booking.status.value] = by_status.get(booking.status.value, 0) + 1
        if booking.status != BookingStatus.COMPLETED:
            continue
        currency = booking.price.currency
        zero = Money(amount_minor=0, currency=currency)
        # The rate agreed when the booking was made, not today's.
        commission = booking.price.percent(booking.partner_commission_pct or 0)
        fares[currency] = fares.get(currency, zero) + booking.price
        due[currency] = due.get(currency, zero) + commission
    return PartnerAttribution(
        code=partner.code,
        bookings_by_status=by_status,
        completed_fares=list(fares.values()),
        commission_due=list(due.values()),
    )
