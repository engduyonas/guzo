from typing import Annotated

import phonenumbers
from pydantic import AfterValidator

from guzo.config import get_settings


def normalize_phone(raw: str, default_region: str | None = None) -> str:
    """Return the number in E.164 (+2519..., +1202...). Foreign numbers need a leading +."""
    region = default_region or get_settings().default_phone_region
    try:
        parsed = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException as exc:
        raise ValueError("invalid phone number") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("invalid phone number")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


PhoneNumber = Annotated[str, AfterValidator(normalize_phone)]
