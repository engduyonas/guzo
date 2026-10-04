from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

from guzo.common.clock import utcnow
from guzo.config import get_settings
from guzo.errors import Unauthorized
from guzo.identity.models import User

_ALGORITHM = "HS256"
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (Argon2Error, InvalidHashError):
        return False


def create_access_token(user: User) -> str:
    settings = get_settings()
    now = utcnow()
    claims = {
        "sub": str(user.id),
        "role": user.role.value,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[_ALGORITHM],
            # Expiry is what matters. Rejecting a token "issued in the future" only
            # breaks sign-in when two servers' clocks disagree by a second.
            options={"verify_iat": False, "require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise Unauthorized("invalid or expired token") from exc
