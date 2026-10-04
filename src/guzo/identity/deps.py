from collections.abc import Callable
from typing import Annotated

from beanie import PydanticObjectId
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from guzo.errors import Forbidden, Unauthorized
from guzo.identity.models import Role, User
from guzo.identity.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise Unauthorized("missing bearer token")
    claims = decode_access_token(credentials.credentials)
    user = await User.get(PydanticObjectId(claims["sub"]))
    if user is None or not user.is_active:
        raise Unauthorized("account not found or disabled")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(*roles: Role) -> Callable:
    async def dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise Forbidden("not allowed for this account")
        return user

    return dependency


Booker = Annotated[User, Depends(require_role(Role.BOOKER))]
Driver = Annotated[User, Depends(require_role(Role.DRIVER))]
Ops = Annotated[User, Depends(require_role(Role.OPS))]
