"""The screening provider port. Pure: no I/O.

A provider is asked about one person at a time: is anyone of this name, and where we know it
this date of birth and country, on a sanctions list, a list of politically exposed persons or in
adverse media. Which lists are searched and how close a name must be to count is the provider's
own configuration (a Sigma360 "filter set"), not ours, so the port carries no threshold.

Screening only reads, so asking twice is harmless: a request that failed is simply asked again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol


class MatchCategory(StrEnum):
    SANCTIONS = "sanctions"
    PEP = "pep"  # a politically exposed person, or their family or close associate
    ADVERSE_MEDIA = "adverse_media"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ScreeningSubject:
    """Who to screen, from the client's identity check. Never the ID number."""

    surname: str
    given_names: str
    date_of_birth: date | None
    # ISO 3166-1 alpha-2 country that issued the document. For a passport this is the client's
    # nationality; for a Ugandan national ID it says only that they live in Uganda.
    document_country: str | None

    def __post_init__(self) -> None:
        if not self.surname.strip() and not self.given_names.strip():
            raise ValueError("A subject has a name")
        if self.document_country is not None and (
            len(self.document_country) != 2 or not self.document_country.isalpha()
        ):
            raise ValueError("A country is two letters")

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.given_names.strip(), self.surname.strip()) if part)


@dataclass(frozen=True, slots=True)
class Match:
    """One listed entity the provider thinks may be the client."""

    # The provider's own ID for the listed entity, stable across screens, so a match staff
    # already ruled out is recognised the next time.
    entity_id: str
    # The listed name, not the client's.
    name: str
    category: MatchCategory
    # How close the provider says the match is, as a whole percentage.
    strength: int
    # The lists or sources it was found in, such as "OFAC SDN" or "UN Security Council".
    sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.entity_id or len(self.entity_id) > 200:
            raise ValueError("A match has the provider's entity ID")
        if type(self.strength) is not int or not 0 <= self.strength <= 100:
            raise ValueError("Strength is a whole percentage from 0 to 100")


@dataclass(frozen=True, slots=True)
class ScreeningResult:
    matches: tuple[Match, ...]
    # The provider's reference for this search, for staff to find it in the provider's console.
    reference: str | None = None


class ProviderUnavailable(Exception):
    """The provider did not answer. Asking again later is safe."""


class ProviderRefused(Exception):
    """The provider will not screen this subject, for example a name it cannot search."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ScreeningProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def screen(self, subject: ScreeningSubject, *, key: str) -> ScreeningResult:
        """Screen one person. `key` is the same on every retry of the same screen."""
        ...
