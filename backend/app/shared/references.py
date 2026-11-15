"""Short references a client can read out: "FV-3K9QX-T2M7A" in place of a 20 character push ID.

A push ID is case sensitive, can start with a dash and is too long to quote on a phone call.
The short reference writes the push ID's last 8 characters (48 random bits) in Crockford
base32, which has no I, L, O or U and ignores case, as two groups of five after a prefix.

It is worked out from the ID each time it is shown, so nothing new is stored and an old record
gets one too. It can be turned back into those last 8 characters (`push_id_suffix`), so staff
can find the record: `right(id, 8) = suffix`, together with the client it belongs to. 48 random
bits is unique enough for that; it is a label, never a key, and proves nothing about who holds it.
"""

from __future__ import annotations

import re

from app.shared.push_ids import PUSH_CHARS, PUSH_ID_PATTERN

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
SUFFIX_LENGTH = 8  # push ID characters, 6 bits each
_DIGITS = 10  # Crockford characters, 5 bits each: 50 bits hold the 48
_PREFIX = re.compile(r"^[A-Z]{2,3}-?(?=[0-9A-Z]{5}-?[0-9A-Z]{5}$)")
_SHORT = re.compile(r"^([0-9A-Z]{5})-?([0-9A-Z]{5})$")
_READ_AS = {"I": "1", "L": "1", "O": "0"}


def short_reference(record_id: str, prefix: str = "FV") -> str:
    """ "FV-3K9QX-T2M7A" for a push ID. Anything that is not a push ID is shown as it is."""
    if re.fullmatch(PUSH_ID_PATTERN, record_id) is None:
        return record_id
    value = 0
    for char in record_id[-SUFFIX_LENGTH:]:
        value = value * 64 + PUSH_CHARS.index(char)
    digits = []
    for _ in range(_DIGITS):
        digits.append(CROCKFORD[value % 32])
        value //= 32
    code = "".join(reversed(digits))
    return f"{prefix}-{code[:5]}-{code[5:]}"


def push_id_suffix(reference: str) -> str | None:
    """The last 8 characters of the push ID a short reference was made from, or None.

    Takes what a person typed or read out: any case, spaces, with or without the prefix and the
    middle dash, and O, I or L for 0, 1 and 1.
    """
    typed = _PREFIX.sub("", "".join(c for c in reference.upper() if not c.isspace()))
    cleaned = "".join(_READ_AS.get(c, c) for c in typed)
    found = _SHORT.fullmatch(cleaned)
    if found is None:
        return None
    value = 0
    for char in found.group(1) + found.group(2):
        if char not in CROCKFORD:
            return None
        value = value * 32 + CROCKFORD.index(char)
    if value >= 64**SUFFIX_LENGTH:
        return None
    chars = []
    for _ in range(SUFFIX_LENGTH):
        chars.append(PUSH_CHARS[value % 64])
        value //= 64
    return "".join(reversed(chars))
