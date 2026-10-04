from typing import Literal

import pyotp
from fastapi import APIRouter, Request
from pydantic import BaseModel, EmailStr, Field
from pymongo.errors import DuplicateKeyError

from guzo.common.phone import PhoneNumber
from guzo.config import get_settings
from guzo.errors import Unauthorized
from guzo.identity import otp
from guzo.identity.deps import CurrentUser
from guzo.identity.models import Role, User, UserResponse
from guzo.identity.security import create_access_token, verify_password
from guzo.kv import rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


class OtpRequest(BaseModel):
    phone: PhoneNumber


class OtpRequested(BaseModel):
    phone: str
    expires_in: int


class OtpVerify(BaseModel):
    phone: PhoneNumber
    code: str = Field(pattern=r"^\d{6}$")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    # Only used the first time a number signs in.
    role: Literal[Role.BOOKER, Role.DRIVER] = Role.BOOKER


class OpsLogin(BaseModel):
    email: EmailStr
    password: str
    totp_code: str = Field(pattern=r"^\d{6}$")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    user: UserResponse


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _token(user: User) -> TokenResponse:
    return TokenResponse(access_token=create_access_token(user), user=UserResponse.from_user(user))


@router.post("/otp/request", status_code=202)
async def request_otp(body: OtpRequest, request: Request) -> OtpRequested:
    expires_in = await otp.request_code(body.phone, _client_ip(request))
    return OtpRequested(phone=body.phone, expires_in=expires_in)


@router.post("/otp/verify")
async def verify_otp(body: OtpVerify) -> TokenResponse:
    """Sign in with an SMS code. The first sign-in for a number registers the account."""
    await otp.verify_code(body.phone, body.code)
    user = await User.find_one(User.phone == body.phone)
    if user is None:
        user = User(
            phone=body.phone,
            name=body.name,
            role=body.role,
            # A booker is verified by proving the phone. A driver waits for ops vetting.
            is_verified=body.role == Role.BOOKER,
        )
        try:
            await user.insert()
        except DuplicateKeyError:
            user = await User.find_one(User.phone == body.phone)
    if not user.is_active or user.role == Role.OPS:
        raise Unauthorized("this account cannot sign in with a phone code")
    return _token(user)


@router.post("/ops/login")
async def ops_login(body: OpsLogin, request: Request) -> TokenResponse:
    """Operations sign-in: email, password and a TOTP code, all required."""
    settings = get_settings()
    email = body.email.lower()
    for key in (f"login:ip:{_client_ip(request)}", f"login:email:{email}"):
        await rate_limit(key, limit=settings.login_attempts_per_15_minutes, window_seconds=900)
    user = await User.find_one(User.email == email, User.role == Role.OPS)
    ok = (
        user is not None
        and user.is_active
        and user.password_hash is not None
        and user.totp_secret is not None
        and verify_password(body.password, user.password_hash)
        and pyotp.TOTP(user.totp_secret).verify(body.totp_code, valid_window=1)
    )
    if not ok:
        raise Unauthorized("invalid credentials")
    return _token(user)


@router.get("/me")
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.from_user(user)
