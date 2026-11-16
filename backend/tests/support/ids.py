"""Bounds for reading a shared table in ID order from one test's own rows."""

from app.shared.push_ids import PUSH_CHARS


def just_before(push_id: str) -> str:
    """The push ID one before `push_id`, so that `id > just_before(x)` starts exactly at x.

    Cutting the last character off is not enough. Once any code asks for an ID by a clock ahead
    of the real time, every later ID in the process keeps that timestamp and counts up in its
    last characters, so IDs made by earlier tests can share all but the last one with this
    test's.
    """
    digits = [PUSH_CHARS.index(char) for char in push_id]
    for i in range(len(digits) - 1, -1, -1):
        if digits[i] > 0:
            digits[i] -= 1
            break
        digits[i] = len(PUSH_CHARS) - 1
    else:
        raise ValueError("no push ID comes before the first one")
    return "".join(PUSH_CHARS[digit] for digit in digits)
