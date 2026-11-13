"""How a phone number and an email address are written before they are stored or looked up."""

import re

_PHONE = re.compile(r"\+[1-9][0-9]{7,14}")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+")
UGANDA = "+256"


class InvalidIdentifier(ValueError):
    pass


def normalize_phone(value: str) -> str:
    """E.164. A local Ugandan number (`0772 123 456`) is read as `+256772123456`."""
    digits = re.sub(r"[\s().-]", "", value)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    elif digits.startswith("0") and len(digits) == 10:
        digits = UGANDA + digits[1:]
    elif digits.startswith("256") and len(digits) == 12:
        digits = "+" + digits
    if _PHONE.fullmatch(digits) is None:
        raise InvalidIdentifier("phone")
    return digits


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 254 or _EMAIL.fullmatch(email) is None:
        raise InvalidIdentifier("email")
    return email
