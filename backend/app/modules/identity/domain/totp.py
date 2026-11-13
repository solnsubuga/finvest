"""Authenticator app codes (TOTP, RFC 6238): six digits from a shared secret and the time.

The defaults every authenticator app uses: SHA-1, six digits, a new code every 30 seconds.
A code from the step before or after is accepted to allow for a slow clock or slow typing.
"""

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import datetime
from urllib.parse import quote

PERIOD_SECONDS = 30
DIGITS = 6
DRIFT_STEPS = 1
SECRET_BYTES = 20


def new_secret() -> bytes:
    return secrets.token_bytes(SECRET_BYTES)


def step_at(moment: datetime) -> int:
    return int(moment.timestamp()) // PERIOD_SECONDS


def code_at(secret: bytes, step: int) -> str:
    digest = hmac.new(secret, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**DIGITS).zfill(DIGITS)


def matching_step(secret: bytes, code: str, now: datetime, last_step: int | None) -> int | None:
    """The time step `code` is valid for, or None.

    A step at or before `last_step` never matches, so a code that was accepted once, and any
    older one, cannot be used again by someone who watched it being typed.
    """
    digits = code.replace(" ", "")
    if len(digits) != DIGITS or not digits.isascii() or not digits.isdigit():
        return None
    current = step_at(now)
    found: int | None = None
    # Every candidate is compared, so timing does not show which one matched.
    for step in range(current - DRIFT_STEPS, current + DRIFT_STEPS + 1):
        matches = hmac.compare_digest(code_at(secret, step), digits)
        if matches and (last_step is None or step > last_step) and found is None:
            found = step
    return found


def secret_text(secret: bytes) -> str:
    """The secret as authenticator apps show and accept it: base32 without padding."""
    return base64.b32encode(secret).decode().rstrip("=")


def setup_uri(secret: bytes, account: str, issuer: str = "Finvest") -> str:
    """The `otpauth://` address a QR code carries, so the app is set up by scanning."""
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret_text(secret)}&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD_SECONDS}"
    )
