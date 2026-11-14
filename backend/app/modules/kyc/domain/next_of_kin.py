"""Next of kin: the people Finvest contacts if it cannot reach a client, and their shares.

A client names one to five people. Each has a full name as on their ID, a relationship, a
mobile number and an ID number, and a share of what the client holds, in basis points. The
shares add up to exactly 10,000 (100%), so a share is a whole number and never a fraction.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from app.modules.identity.domain.accounts import InvalidIdentifier, normalize_phone

MAX_PEOPLE = 5
WHOLE = 10_000
MAX_NAME = 100
_ID_NUMBER = re.compile(r"[A-Z0-9]{5,32}")


class Relationship(StrEnum):
    SPOUSE = "spouse"
    CHILD = "child"
    PARENT = "parent"
    SIBLING = "sibling"
    OTHER_RELATIVE = "other_relative"
    FRIEND = "friend"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Person:
    full_name: str
    relationship: Relationship
    phone: str
    id_number: str
    share_bps: int


@dataclass(frozen=True, slots=True)
class PersonProblem:
    # The person's place in the list, or None for a problem with the list as a whole.
    index: int | None
    field: str
    code: str


def clean(person: Person) -> Person:
    """The person as stored: spaces tidied, the number in E.164, the ID number in capitals.

    A phone number that cannot be read is kept as typed, for `problems` to report.
    """
    try:
        phone = normalize_phone(person.phone)
    except InvalidIdentifier:
        phone = person.phone.strip()
    return Person(
        full_name=" ".join(person.full_name.split()),
        relationship=person.relationship,
        phone=phone,
        id_number=re.sub(r"[\s-]", "", person.id_number).upper(),
        share_bps=person.share_bps,
    )


def problems(people: list[Person], client_phone: str | None = None) -> list[PersonProblem]:
    """Everything wrong with a cleaned list, so the client can fix it all at once.

    An empty list is allowed: it removes everyone. Otherwise the shares add up to 100%.
    """
    found: list[PersonProblem] = []
    if len(people) > MAX_PEOPLE:
        found.append(PersonProblem(None, "people", "too_many"))
    seen_ids: set[str] = set()
    for index, person in enumerate(people):
        if not person.full_name or len(person.full_name) > MAX_NAME:
            found.append(PersonProblem(index, "full_name", "required"))
        elif len(person.full_name.split()) < 2:
            found.append(PersonProblem(index, "full_name", "full_name_needed"))
        try:
            normalize_phone(person.phone)
        except InvalidIdentifier:
            found.append(PersonProblem(index, "phone", "invalid_phone"))
        else:
            if client_phone is not None and person.phone == client_phone:
                found.append(PersonProblem(index, "phone", "your_own_number"))
        if _ID_NUMBER.fullmatch(person.id_number) is None:
            found.append(PersonProblem(index, "id_number", "invalid_id_number"))
        elif person.id_number in seen_ids:
            found.append(PersonProblem(index, "id_number", "named_twice"))
        seen_ids.add(person.id_number)
        if not 1 <= person.share_bps <= WHOLE:
            found.append(PersonProblem(index, "share_bps", "invalid_share"))
    if people and sum(person.share_bps for person in people) != WHOLE:
        found.append(PersonProblem(None, "share_bps", "shares_must_total_100"))
    return found
