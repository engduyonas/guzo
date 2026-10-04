from beanie import PydanticObjectId
from bson.errors import InvalidId

from guzo.errors import NotFound


def object_id(value: str, what: str = "resource") -> PydanticObjectId:
    """Parse an id from a client. A malformed id is simply something that does not exist."""
    try:
        return PydanticObjectId(value)
    except (InvalidId, TypeError) as exc:
        raise NotFound(f"{what} not found") from exc
