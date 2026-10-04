"""Create an operations account: python -m guzo.identity.cli ops@example.com "Name"."""

import argparse
import asyncio
import getpass

import pyotp

from guzo.config import get_settings
from guzo.db import close_db, init_db
from guzo.identity.models import Role, User
from guzo.identity.security import hash_password


async def create_ops(email: str, name: str, password: str) -> tuple[User, str]:
    """Insert an ops user and return it with the TOTP provisioning URI."""
    secret = pyotp.random_base32()
    user = User(
        role=Role.OPS,
        email=email.lower(),
        name=name,
        password_hash=hash_password(password),
        totp_secret=secret,
        is_verified=True,
    )
    await user.insert()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Guzo Ops")
    return user, uri


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Create a Guzo operations account")
    parser.add_argument("email")
    parser.add_argument("name")
    args = parser.parse_args()
    password = getpass.getpass("Password: ")
    if len(password) < 12:
        raise SystemExit("password must be at least 12 characters")
    await init_db(get_settings())
    try:
        _, uri = await create_ops(args.email, args.name, password)
    finally:
        await close_db()
    print("Created. Add this to an authenticator app (shown once):")
    print(uri)


if __name__ == "__main__":
    asyncio.run(_main())
