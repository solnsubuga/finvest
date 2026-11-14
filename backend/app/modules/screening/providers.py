"""Screening providers that need no account: one for a developer's own machine.

The real provider (Sigma360, chosen by the owner) has its own adapter. With no provider set up,
no adapter runs at all: the worker records each screen as `not_run` (see `jobs.py`).
"""

from dataclasses import dataclass

from app.modules.screening.domain.provider import (
    Match,
    MatchCategory,
    ScreeningResult,
    ScreeningSubject,
)


@dataclass(frozen=True)
class DevelopmentStubProvider:
    """Finds a listed entity for a few made-up surnames, so both paths can be tried by hand.

    `SANCTIONED` matches a sanctions list and `POLITICIAN` a list of politically exposed
    persons. Anyone else is clear. It checks nothing real, so it may not run in production.
    """

    name: str = "development-stub"

    async def screen(self, subject: ScreeningSubject, *, key: str) -> ScreeningResult:
        surname = subject.surname.strip().upper()
        if surname == "SANCTIONED":
            match = Match(
                entity_id="stub-sanctions-1",
                name=f"{subject.given_names} {subject.surname}",
                category=MatchCategory.SANCTIONS,
                strength=96,
                sources=("Stub sanctions list",),
            )
        elif surname == "POLITICIAN":
            match = Match(
                entity_id="stub-pep-1",
                name=f"{subject.given_names} {subject.surname}",
                category=MatchCategory.PEP,
                strength=88,
                sources=("Stub politically exposed persons",),
            )
        else:
            return ScreeningResult(matches=(), reference=f"stub:{key}")
        return ScreeningResult(matches=(match,), reference=f"stub:{key}")
