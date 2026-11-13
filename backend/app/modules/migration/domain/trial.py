"""The trial import: read both sheets, check them against each other, list every problem. Pure.

Nothing is saved by a trial. It answers one question: if this file were imported today, what
would be wrong with it? Staff fix the spreadsheet and run it again until no error is left.

The checks across rows are the ones a single row cannot see: the same person entered twice,
an investment whose client is not in the clients sheet, a reference used for two things. The
summary counts what the file holds, and the finance check compares those figures with the old
system's own totals.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from app.modules.investments.domain.products import SEED_PRODUCTS, Product
from app.modules.migration.domain.sheet import (
    ClientRow,
    InvestmentRow,
    InvestmentStatus,
    Problem,
    Severity,
    Sheet,
    parse_clients,
    parse_investments,
)
from app.shared.money import Currency, Money

_SEEDED: Mapping[str, Product] = {product.code: product for product in SEED_PRODUCTS}


@dataclass(frozen=True, slots=True)
class Summary:
    """What the rows without errors add up to.

    A row with an error is left out of every figure, and so is an investment whose client row
    has one. The figures therefore grow towards the old system's totals as the file is fixed.
    """

    clients: int
    verified_identities: int
    # Clients who will be asked to redo the ID check when they claim.
    clients_to_recheck: int
    clients_without_phone: int
    running_investments: int
    pending_orders: int
    # Principal of running investments, one amount per currency that has any.
    invested: tuple[Money, ...]
    # Interest before tax the old system says running investments have earned, per currency.
    interest_before_tax: tuple[Money, ...]


@dataclass(frozen=True, slots=True)
class TrialReport:
    as_of: date
    clients: tuple[ClientRow, ...]
    investments: tuple[InvestmentRow, ...]
    problems: tuple[Problem, ...]
    summary: Summary

    @property
    def errors(self) -> tuple[Problem, ...]:
        return tuple(p for p in self.problems if p.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Problem, ...]:
        return tuple(p for p in self.problems if p.severity is Severity.WARNING)

    @property
    def clean(self) -> bool:
        """Whether the final import may run: no error is left. Warnings do not block it."""
        return not self.errors


def run_trial(
    clients_csv: str,
    investments_csv: str,
    *,
    as_of: date,
    products: Mapping[str, Product] = _SEEDED,
) -> TrialReport:
    """Check both sheets. `as_of` is the day the old system's figures were taken."""
    clients = parse_clients(clients_csv, as_of=as_of)
    investments = parse_investments(investments_csv, as_of=as_of, products=products)
    problems = [*clients.problems, *investments.problems]

    problems += _repeated_references(Sheet.CLIENTS, "legacy_client_ref", clients.references)
    problems += _repeated_references(
        Sheet.INVESTMENTS, "legacy_investment_ref", investments.references
    )
    repeated_clients = {ref for ref, rows in clients.references.items() if len(rows) > 1}
    repeated_investments = {ref for ref, rows in investments.references.items() if len(rows) > 1}

    # A person entered twice would get two accounts, and the phone is how a client signs in.
    problems += _shared(clients.rows, "phone", "duplicate_phone", lambda c: c.phone)
    problems += _shared(
        clients.rows,
        "id_number",
        "duplicate_id",
        lambda c: (c.id_type, c.id_number) if c.id_type and c.id_number else None,
    )
    problems += _shared(
        clients.rows,
        "legacy_account_number",
        "duplicate_account_number",
        lambda c: c.legacy_account_number,
    )
    # Families do share an email address, and two people can share a name and a birthday.
    problems += _shared(
        clients.rows, "email", "duplicate_email", lambda c: c.email, Severity.WARNING
    )
    problems += _shared(
        clients.rows,
        "surname",
        "possible_duplicate_person",
        lambda c: (
            (c.surname.casefold(), c.given_names.casefold(), c.date_of_birth)
            if c.date_of_birth
            else None
        ),
        Severity.WARNING,
    )

    # A clients sheet refused for its columns is one problem, not one per investment.
    clients_refused = any(problem.row is None for problem in clients.problems)
    with_investments: set[str] = set()
    for investment in investments.rows:
        with_investments.add(investment.legacy_client_ref)
        if not clients_refused and investment.legacy_client_ref not in clients.references:
            problems.append(
                Problem(Sheet.INVESTMENTS, investment.row, "legacy_client_ref", "client_unknown")
            )
    problems += [
        Problem(
            Sheet.CLIENTS,
            client.row,
            None,
            "client_without_investments",
            Severity.WARNING,
        )
        for client in clients.rows
        if client.legacy_client_ref not in with_investments
    ]

    good_clients = tuple(c for c in clients.rows if c.legacy_client_ref not in repeated_clients)
    known = {client.legacy_client_ref for client in good_clients}
    good_investments = tuple(
        i
        for i in investments.rows
        if i.legacy_investment_ref not in repeated_investments and i.legacy_client_ref in known
    )
    return TrialReport(
        as_of=as_of,
        clients=good_clients,
        investments=good_investments,
        problems=tuple(sorted(problems, key=_order)),
        summary=_summary(good_clients, good_investments, as_of),
    )


def _order(problem: Problem) -> tuple[str, int, str, str]:
    return (problem.sheet.value, problem.row or 0, problem.column or "", problem.code)


def _repeated_references(
    sheet: Sheet, column: str, references: Mapping[str, tuple[int, ...]]
) -> list[Problem]:
    return [
        Problem(sheet, row, column, "duplicate_reference")
        for rows in references.values()
        if len(rows) > 1
        for row in rows
    ]


def _shared(
    clients: Iterable[ClientRow],
    column: str,
    code: str,
    key: Callable[[ClientRow], Hashable | None],
    severity: Severity = Severity.ERROR,
) -> list[Problem]:
    """A problem on every client row that shares `key` with another row."""
    groups: dict[Hashable, list[int]] = {}
    for client in clients:
        value = key(client)
        if value is not None:
            groups.setdefault(value, []).append(client.row)
    return [
        Problem(Sheet.CLIENTS, row, column, code, severity)
        for rows in groups.values()
        if len(rows) > 1
        for row in rows
    ]


def _summary(
    clients: tuple[ClientRow, ...], investments: tuple[InvestmentRow, ...], as_of: date
) -> Summary:
    invested: dict[Currency, Money] = {}
    interest: dict[Currency, Money] = {}
    running = 0
    for investment in investments:
        if investment.status is not InvestmentStatus.RUNNING:
            continue
        running += 1
        currency = investment.principal.currency
        invested[currency] = invested.get(currency, Money.zero(currency)) + investment.principal
        earned = investment.interest_before_tax or Money.zero(currency)
        interest[currency] = interest.get(currency, Money.zero(currency)) + earned
    return Summary(
        clients=len(clients),
        verified_identities=sum(1 for client in clients if client.id_verified),
        clients_to_recheck=sum(1 for client in clients if client.needs_id_check(as_of)),
        clients_without_phone=sum(1 for client in clients if client.phone is None),
        running_investments=running,
        pending_orders=len(investments) - running,
        invested=tuple(invested[currency] for currency in Currency if currency in invested),
        interest_before_tax=tuple(
            interest[currency] for currency in Currency if currency in interest
        ),
    )
