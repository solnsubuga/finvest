"""Money steps shared by the journeys: invest, pay by mobile money or bank transfer, and read
the books the way staff and the client would."""

import secrets
from datetime import date, timedelta

from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity, InvestmentStatus
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec
from app.modules.reconciliation import statements
from app.modules.reconciliation.domain.statements import CsvFormat, StatementSource, parse_csv
from app.shared.actors import SYSTEM
from app.shared.business_day import business_date
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.journey import Person, World

RECEIPT = b"%PDF-1.7\n" + secrets.token_bytes(2048)
BANK_FORMAT = CsvFormat(
    value_date="Date",
    date_format="%Y-%m-%d",
    narrative="Details",
    reference="Reference",
    money_in="In",
    money_out="Out",
)


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


def idempotency() -> dict[str, str]:
    return {"Idempotency-Key": f"key-{push_id()}"}


def today(world: World) -> date:
    return business_date(world.clock.now())


async def invest(
    world: World,
    client: Person,
    amount: int,
    *,
    product_code: str = "TREA-91",
    at_maturity: AtMaturity | None = None,
) -> investments.Investment:
    """The client reviews and confirms an investment. Investing has no routes yet, so this
    calls the investing service as the client, on the API's connection."""
    async with world.api_database.actor_transaction(client.actor) as session:
        return await investments.request_investment(
            session,
            client.actor,
            idempotency_key=f"key-{push_id()}",
            product_code=product_code,
            amount=ugx(amount),
            at_maturity=at_maturity,
            clock=world.clock,
        )


async def investment(world: World, investment_id: str) -> investments.Investment:
    async with world.worker_database.actor_transaction(SYSTEM) as session:
        return await investments.get_investment(session, investment_id)


async def pay_by_mobile_money(world: World, client: Person, investment_id: str) -> dict:  # type: ignore[type-arg]
    """Pay over HTTP; the worker sends the prompt to ioTec and reads the result."""
    paid = await client.http.post(
        f"/client/investments/{investment_id}/payments/mobile-money",
        json={"network": "mtn"},
        headers=idempotency(),
    )
    assert paid.status_code == 201, paid.text
    await world.settle(within=timedelta(minutes=1))
    payment = await client.http.get(f"/client/payments/{paid.json()['id']}")
    assert payment.status_code == 200, payment.text
    return payment.json()  # type: ignore[no-any-return]


async def import_bank_statement(
    world: World,
    importer: Person,
    day: date,
    lines: list[tuple[str, int]],
    *,
    opening: int,
    account_reference: str = "trust",
) -> None:
    """Staff upload the trust bank account's statement for `day`: (reference, signed amount)."""
    rows = "".join(
        f"{day.isoformat()},Transfer,{reference},{amount if amount > 0 else ''},"
        f"{-amount if amount < 0 else ''}\n"
        for reference, amount in lines
    )
    content = f"Date,Details,Reference,In,Out\n{rows}"
    parsed = parse_csv(
        content,
        BANK_FORMAT,
        source=StatementSource.BANK,
        account_reference=account_reference,
        statement_date=day,
        opening_balance=ugx(opening),
        closing_balance=ugx(opening + sum(amount for _, amount in lines)),
    )
    async with world.api_database.actor_transaction(importer.actor) as session:
        await statements.import_statement(
            session, importer.actor, parsed, statements.file_digest(content.encode()), world.clock
        )


class TrustBank:
    """The trust bank account's statements, one a day, in order, as the bank sends them.

    Statements are imported one day after another, each opening on the last one's closing
    balance, so journeys share this one account. A transfer a client makes is put on the next
    day's statement; the day close fills in the quiet days after.
    """

    FIRST_DAY_BACK = 6

    def __init__(self) -> None:
        self.next_day: date | None = None
        self.closing = 0
        self.lines: dict[date, list[tuple[str, int]]] = {}

    async def receive(self, world: World, importer: Person, reference: str, amount: int) -> date:
        """A transfer of `amount` arrives. Returns the day it is on the statement."""
        day = self.next_day or today(world) - timedelta(days=self.FIRST_DAY_BACK)
        assert day < today(world), "more transfers than days before today"
        await self.statement(world, importer, day, [(reference, amount)])
        return day

    async def statement(
        self, world: World, importer: Person, day: date, lines: list[tuple[str, int]]
    ) -> None:
        await import_bank_statement(world, importer, day, lines, opening=self.closing)
        self.lines[day] = lines
        self.closing += sum(amount for _, amount in lines)
        self.next_day = day + timedelta(days=1)


async def pay_by_bank_transfer(
    world: World,
    client: Person,
    investment_id: str,
    amount: int,
    *,
    sent_on: date,
    matcher: Person,
    approver: Person,
) -> dict:  # type: ignore[type-arg]
    """The client tells us about a transfer, with its receipt; one person matches it to the
    statement line and a different person approves the match; the worker credits it."""
    sent = await client.http.post(
        f"/client/investments/{investment_id}/payments/bank-transfer",
        data={
            "amount_minor": str(amount),
            "sent_on": sent_on.isoformat(),
            "bank_name": "Stanbic Bank",
            "transaction_number": f"FT{secrets.randbelow(10**10):010d}",
        },
        files={"receipt": ("receipt.pdf", RECEIPT, "application/pdf")},
        headers=idempotency(),
    )
    assert sent.status_code == 201, sent.text
    payment_id = sent.json()["id"]
    assert sent.json()["status"] == "in_review"

    detail = await matcher.http.get(f"/staff/payments/bank-transfers/{payment_id}")
    assert detail.status_code == 200, detail.text
    candidates = [line for line in detail.json()["candidates"] if line["amount"]["minor"] == amount]
    assert candidates, detail.json()
    matched = await matcher.http.post(
        f"/staff/payments/bank-transfers/{payment_id}/review",
        json={"outcome": "matched", "statement_line_id": candidates[0]["id"]},
        headers=idempotency(),
    )
    assert matched.status_code == 200, matched.text
    # The person who matched it may not approve it.
    own = await matcher.http.post(
        f"/staff/payments/bank-transfers/{payment_id}/approve", headers=idempotency()
    )
    assert own.status_code == 403, own.text
    approved = await approver.http.post(
        f"/staff/payments/bank-transfers/{payment_id}/approve", headers=idempotency()
    )
    assert approved.status_code == 200, approved.text
    await world.settle(within=timedelta(seconds=30))
    payment = await client.http.get(f"/client/payments/{payment_id}")
    return payment.json()  # type: ignore[no-any-return]


async def balance(
    world: World,
    code: str,
    *,
    owner: str | None = None,
    reference: str | None = None,
    kind: AccountKind = AccountKind.LIABILITY,
) -> int:
    """An account's balance, opening it (empty) if nothing was ever posted to it."""
    async with world.worker_database.actor_transaction(SYSTEM) as session:
        account = await ledger.open_account(
            session,
            AccountSpec(code, kind, Currency.UGX, code, owner_client_id=owner, reference=reference),
            world.clock,
        )
        return (await ledger.get_balance(session, account.id)).minor


def is_active(investment: investments.Investment) -> bool:
    return investment.status is InvestmentStatus.ACTIVE
