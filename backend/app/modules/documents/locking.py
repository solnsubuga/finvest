"""Lock the copy of a PDF that is emailed, with the client's date of birth.

An email can be read by whoever else uses the mailbox, or forwarded by mistake, so the
attached PDF asks for a password: the client's date of birth as eight digits, day, month and
year (`05031990`), as many banks in Uganda do. The copy kept in the app, behind sign-in, is not
locked, and the stored file is never changed.

The lock is AES-256. The owner password, which would let someone change the PDF's
permissions, is random and thrown away, because no one needs it. The password is never logged
or stored.
"""

import secrets
from datetime import date
from io import BytesIO

from pypdf import PdfReader, PdfWriter


def password_for(date_of_birth: date) -> str:
    """The password a client types: the date of birth as DDMMYYYY."""
    return f"{date_of_birth:%d%m%Y}"


def lock(content: bytes, password: str) -> bytes:
    """The same PDF, opened only with `password`."""
    if not password:
        raise ValueError("A locked PDF needs a password")
    writer = PdfWriter(clone_from=PdfReader(BytesIO(content)))
    writer.encrypt(
        user_password=password,
        owner_password=secrets.token_urlsafe(32),
        algorithm="AES-256",
    )
    out = BytesIO()
    writer.write(out)
    return out.getvalue()
