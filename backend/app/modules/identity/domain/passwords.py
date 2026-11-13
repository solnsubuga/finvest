"""What makes a new password acceptable. Hashing lives in `identity.hashing`.

docs/rules.md: at least 10 characters and checked against leaked password lists. The check here
is a short built-in list of the passwords at the top of every leak, plus the patterns people
reach for (a common word with digits, a run, a keyboard walk, their own number or address).
A fuller leaked-password source can be added behind `too_common` once the owner picks one.
"""

import re
from collections.abc import Iterable
from itertools import pairwise

MIN_CHARACTERS = 10
MAX_CHARACTERS = 128

# Words at the top of leaked-password lists, and the ones people reach for here.
COMMON_WORDS = frozenset(
    """
    password passw0rd qwerty qwertyuiop asdf asdfgh asdfghjkl zxcvbnm qazwsx azerty
    letmein welcome hello iloveyou loveyou lovely love admin administrator root user login
    guest test testing default changeme secret master monkey dragon shadow sunshine princess
    football soccer arsenal chelsea liverpool manchester manutd barcelona
    superman batman trustno1 starwars whatever freedom computer internet
    finvest invest investment investor money shillings savings saving bank banking mobilemoney
    treasury bills abc abcd abcdef abc123 qwe qwe123 pass mypassword newpassword
    january february march april may june july august september october november december
    jesus christ god lord blessed heaven angel mukama katonda
    kampala uganda entebbe jinja mbarara gulu nairobi kenya africa
    family michael jennifer jordan charlie daniel thomas george
    """.split()  # noqa: SIM905 - a word list reads better as text
)
SEQUENCES = (
    "01234567890123456789",
    "abcdefghijklmnopqrstuvwxyz",
    "qwertyuiopasdfghjklzxcvbnm",
    "1qaz2wsx3edc4rfv5tgb6yhn7ujm",
)
SWAPS = str.maketrans(
    {"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}
)
_NOT_LETTERS = re.compile(r"[^a-z]+")
_EDGES = re.compile(r"^[^a-z@$]+|[^a-z]+$")


def _is_run(text: str) -> bool:
    """One character repeated, or a walk along a sequence in either direction."""
    if len(set(text)) <= 2:
        return True
    for sequence in SEQUENCES:
        if text in sequence or text in sequence[::-1]:
            return True
    steps = {ord(b) - ord(a) for a, b in pairwise(text)}
    return steps in ({1}, {-1})


def what_is_left(password: str) -> str:
    """The password with digits and symbols set aside and common letter swaps undone."""
    lowered = password.lower()
    letters = _NOT_LETTERS.sub("", lowered)
    if len(letters) >= 3:
        return letters
    return _NOT_LETTERS.sub("", lowered.translate(SWAPS))


def too_common(password: str) -> bool:
    lowered = password.lower()
    if _is_run(lowered):
        return True
    # "P@ssw0rd2024!": set the digits and symbols around the word aside, then undo the swaps.
    core = _NOT_LETTERS.sub("", _EDGES.sub("", lowered).translate(SWAPS))
    swapped = _NOT_LETTERS.sub("", lowered.translate(SWAPS))
    for candidate in (what_is_left(password), swapped, core):
        if candidate in COMMON_WORDS:
            return True
        # The same word twice, or two common words together: "passwordpassword", "welcomeadmin".
        for split in range(3, len(candidate) - 2):
            if candidate[:split] in COMMON_WORDS and candidate[split:] in COMMON_WORDS:
                return True
    return False


def _personal(password: str, about: Iterable[str | None]) -> bool:
    """Whether the password is mostly the account's own number, address or name."""
    rest = password.lower()
    removed = False
    for text in about:
        if not text:
            continue
        lowered = text.strip().lower()
        # Longest first, so a whole address is set aside before a piece of it.
        words = {lowered, lowered.lstrip("+"), lowered.split("@", 1)[0], lowered[-9:]}
        for word in sorted(words, key=lambda word: (-len(word), word)):
            if len(word) >= 4 and word in rest:
                rest = rest.replace(word, "")
                removed = True
    return removed and len(rest) < 6


PasswordProblem = str
"""A code the apps turn into a sentence: `too_short`, `too_long`, `leaked_password`."""


def password_problem(password: str, about: Iterable[str | None] = ()) -> PasswordProblem | None:
    """Why a new password is unacceptable, or None. `about` is the account's phone and email."""
    if len(password) < MIN_CHARACTERS:
        return "too_short"
    if len(password) > MAX_CHARACTERS or "\x00" in password:
        return "too_long"
    if too_common(password) or _personal(password, about):
        return "leaked_password"
    return None
