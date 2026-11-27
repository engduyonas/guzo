from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field

from guzo.audit import service as audit
from guzo.bookings.models import Booking
from guzo.bookings.routes import BookingResponse
from guzo.business import service
from guzo.business.models import Company, CompanyPlan, MemberRole, Membership
from guzo.business.service import Statement
from guzo.common.ids import object_id
from guzo.common.phone import PhoneNumber
from guzo.errors import Forbidden
from guzo.identity.deps import Booker, Ops
from guzo.identity.models import User

router = APIRouter(tags=["business"])

Month = Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM")]


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    billing_email: EmailStr
    plan: CompanyPlan


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    billing_email: EmailStr | None = None
    plan: CompanyPlan | None = None
    active: bool | None = None


class CompanyResponse(BaseModel):
    id: str
    name: str
    billing_email: str
    plan: CompanyPlan
    active: bool

    @classmethod
    def from_company(cls, company: Company) -> "CompanyResponse":
        return cls(id=str(company.id), **company.model_dump(include=set(cls.model_fields) - {"id"}))


class MemberCreate(BaseModel):
    phone: PhoneNumber
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: MemberRole


class MemberResponse(BaseModel):
    user_id: str
    name: str | None
    phone: str | None
    role: MemberRole


class MyCompany(BaseModel):
    company: CompanyResponse
    role: MemberRole


def _member(membership: Membership, user: User) -> MemberResponse:
    return MemberResponse(
        user_id=membership.user_id, name=user.name, phone=user.phone, role=membership.role
    )


async def _members(company: Company) -> list[MemberResponse]:
    memberships = await Membership.find({"company_id": str(company.id), "active": True}).to_list()
    users = {
        str(u.id): u
        for u in await User.find(
            {"_id": {"$in": [object_id(m.user_id) for m in memberships]}}
        ).to_list()
    }
    return [_member(m, users[m.user_id]) for m in memberships]


async def company_admin(user: Booker) -> tuple[Company, User]:
    membership = await service.membership_of(user)
    if membership is None or membership.role != MemberRole.ADMIN:
        raise Forbidden("only a company admin can do this", code="not_company_admin")
    return await service.get_company(membership.company_id), user


CompanyAdmin = Annotated[tuple[Company, User], Depends(company_admin)]


# --- Company members --------------------------------------------------------


@router.get("/business/company")
async def my_company(user: Booker) -> MyCompany:
    """The company this booker can bill rides to, and their role there."""
    membership = await service.membership_of(user)
    if membership is None:
        raise Forbidden("this account does not belong to a company", code="not_a_company_member")
    company = await service.get_company(membership.company_id)
    return MyCompany(company=CompanyResponse.from_company(company), role=membership.role)


@router.get("/business/members")
async def list_members(admin: CompanyAdmin) -> list[MemberResponse]:
    return await _members(admin[0])


@router.post("/business/members", status_code=201)
async def add_member(body: MemberCreate, admin: CompanyAdmin) -> MemberResponse:
    company, user = admin
    membership, member = await service.add_member(
        company, phone=body.phone, name=body.name, role=body.role, by=user
    )
    return _member(membership, member)


@router.post("/business/members/{user_id}/remove", status_code=204)
async def remove_member(user_id: str, admin: CompanyAdmin) -> None:
    company, user = admin
    if user_id == str(user.id):
        raise Forbidden("an admin cannot remove themselves", code="cannot_remove_self")
    await service.remove_member(company, user_id, by=user)


@router.get("/business/bookings")
async def company_bookings(admin: CompanyAdmin, month: Month) -> list[BookingResponse]:
    """Every booking billed to the company with a pickup in the month (UTC)."""
    company = admin[0]
    year, number = (int(part) for part in month.split("-"))
    start = datetime(year, number, 1)
    end = datetime(year + number // 12, number % 12 + 1, 1)
    bookings = (
        await Booking.find(
            {"company_id": str(company.id), "scheduled_at": {"$gte": start, "$lt": end}}
        )
        .sort("scheduled_at")
        .to_list()
    )
    return [BookingResponse.from_booking(b) for b in bookings]


@router.get("/business/statement")
async def company_statement(admin: CompanyAdmin, month: Month) -> Statement:
    return await service.statement(admin[0], month)


# --- Operations -------------------------------------------------------------


@router.get("/ops/companies", tags=["ops"])
async def list_companies(user: Ops) -> list[CompanyResponse]:
    companies = await Company.find_all().sort("name").to_list()
    return [CompanyResponse.from_company(c) for c in companies]


@router.post("/ops/companies", status_code=201, tags=["ops"])
async def create_company(body: CompanyCreate, user: Ops) -> CompanyResponse:
    company = Company(**body.model_dump())
    await company.insert()
    await audit.record(
        user, "company.create", "company", str(company.id), body.model_dump(mode="json")
    )
    return CompanyResponse.from_company(company)


@router.patch("/ops/companies/{company_id}", tags=["ops"])
async def update_company(company_id: str, body: CompanyUpdate, user: Ops) -> CompanyResponse:
    company = await service.get_company(company_id)
    for field, value in body:
        if value is not None:
            setattr(company, field, value)
    await company.save()
    changes = body.model_dump(mode="json", exclude_none=True)
    await audit.record(user, "company.update", "company", company_id, changes)
    return CompanyResponse.from_company(company)


@router.get("/ops/companies/{company_id}/members", tags=["ops"])
async def ops_list_members(company_id: str, user: Ops) -> list[MemberResponse]:
    return await _members(await service.get_company(company_id))


@router.post("/ops/companies/{company_id}/members", status_code=201, tags=["ops"])
async def ops_add_member(company_id: str, body: MemberCreate, user: Ops) -> MemberResponse:
    company = await service.get_company(company_id)
    membership, member = await service.add_member(
        company, phone=body.phone, name=body.name, role=body.role, by=user
    )
    return _member(membership, member)


@router.post("/ops/companies/{company_id}/members/{user_id}/remove", status_code=204, tags=["ops"])
async def ops_remove_member(company_id: str, user_id: str, user: Ops) -> None:
    await service.remove_member(await service.get_company(company_id), user_id, by=user)


@router.get("/ops/companies/{company_id}/statement", tags=["ops"])
async def ops_statement(company_id: str, user: Ops, month: Month) -> Statement:
    return await service.statement(await service.get_company(company_id), month)
