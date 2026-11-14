"""How a client's name is shown: proper case, given names first.

An identity document prints names in capitals and often puts the surname first ("NAKATO",
"AMARA JOY"). The check keeps them exactly as printed, because that is the KYC record and what
payouts compare against. Everywhere a person reads the name, it is shown as "Amara Joy Nakato".

Only a word written all in capitals or all in small letters is re-cased. A word that already
mixes the two ("McDonald", "deSouza") was written that way on purpose and is kept.
"""

import re

# A word splits into parts at a hyphen or an apostrophe: "SSALI-KATO" is "Ssali-Kato",
# "O'NEIL" is "O'Neil". Each part is capitalised on its own.
_PARTS = re.compile(r"([\-'])")


def _word(word: str) -> str:
    if not (word.isupper() or word.islower()):
        return word
    return "".join(part[:1].upper() + part[1:].lower() for part in _PARTS.split(word))


def proper_case(name: str) -> str:
    """One name in proper case, with spacing tidied: "  amara   JOY " is "Amara Joy"."""
    return " ".join(_word(word) for word in name.split())


def full_name(surname: str, given_names: str) -> str:
    """The name to show: given names first, then the surname. "NAKATO", "AMARA" is
    "Amara Nakato"."""
    return " ".join(part for part in (proper_case(given_names), proper_case(surname)) if part)


def first_name(surname: str, given_names: str) -> str:
    """The name to greet someone by: the first given name, or the surname if there is none."""
    given = proper_case(given_names).split(" ")[0]
    return given or proper_case(surname)
