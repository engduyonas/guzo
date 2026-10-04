"""What a client needs to know about the service itself."""

from enum import StrEnum

from fastapi import APIRouter, Query
from pydantic import BaseModel

from guzo.config import get_settings
from guzo.errors import Unprocessable

router = APIRouter(prefix="/meta", tags=["meta"])

VERSION_PATTERN = r"^\d+(\.\d+){0,2}$"


class Platform(StrEnum):
    ANDROID = "android"
    IOS = "ios"


class ClientStatus(BaseModel):
    min_supported_version: str
    update_required: bool


def parse_version(version: str) -> tuple[int, int, int]:
    try:
        parts = [int(p) for p in version.split(".")]
    except ValueError as exc:
        raise Unprocessable("version must look like 1.2.3", code="invalid_version") from exc
    return tuple(parts + [0] * (3 - len(parts)))  # type: ignore[return-value]


@router.get("/client")
async def client_status(
    platform: Platform, version: str = Query(pattern=VERSION_PATTERN)
) -> ClientStatus:
    """Apps call this on start. `update_required` means this build must be updated to go on."""
    settings = get_settings()
    minimum = {
        Platform.ANDROID: settings.min_android_version,
        Platform.IOS: settings.min_ios_version,
    }[platform]
    return ClientStatus(
        min_supported_version=minimum,
        update_required=parse_version(version) < parse_version(minimum),
    )
