"""The code printed on a statement so a bank or embassy can check it is genuine on the website.

Twelve characters from an alphabet with no letters that read like digits, shown in three groups
of four (`K7Q2-9XMA-4TPD`). That is 60 bits, drawn from the operating system's random source,
so a code cannot be guessed, and the check route also limits attempts. Pure: the caller passes
the random bytes.
"""

from collections.abc import Callable

# Crockford's base 32: digits and letters without I, L, O and U.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
LENGTH = 12
GROUP = 4
# Read as the character it looks like, as Crockford's base 32 does.
_LOOKALIKES = {"O": "0", "I": "1", "L": "1"}
_IGNORED = frozenset(" -.\t")


def new_code(random_bytes: Callable[[int], bytes]) -> str:
    """A fresh code, without the dashes. Each byte gives 5 bits, so every character is equally
    likely."""
    return "".join(ALPHABET[byte & 0x1F] for byte in random_bytes(LENGTH))


def shown(code: str) -> str:
    """`K7Q29XMA4TPD` as `K7Q2-9XMA-4TPD`."""
    if normalised(code) != code:
        raise ValueError("Not a check code")
    return "-".join(code[i : i + GROUP] for i in range(0, LENGTH, GROUP))


def normalised(typed: str) -> str | None:
    """What someone typed, as a stored code: case, spaces and dashes ignored, and O, I and L
    read as 0, 1 and 1. None when it cannot be a code, so the caller asks nothing."""
    if len(typed) > 40:
        return None
    kept = [_LOOKALIKES.get(char, char) for char in typed.upper() if char not in _IGNORED]
    code = "".join(kept)
    if len(code) != LENGTH or any(char not in ALPHABET for char in code):
        return None
    return code
