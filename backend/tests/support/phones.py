"""Phone numbers for test clients, never the same twice in one test run.

A client's number is unique in the database, and every test in a run shares one database.
Eight random digits drawn for each of thousands of clients would repeat somewhere in most runs
(the birthday problem), and the test that drew the repeat fails with `phone_taken`.
"""

import secrets

_given: set[str] = set()


def unique_phone() -> str:
    """A Ugandan mobile number, +2567 and eight digits, not given before in this run."""
    while True:
        number = f"+2567{secrets.randbelow(10**8):08d}"
        if number not in _given:
            _given.add(number)
            return number
