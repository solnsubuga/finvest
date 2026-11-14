"""Finance over HTTP: who may see the books, the month's figures, and the journal export."""

import secrets
from datetime import date, timedelta

import pytest

from app.modules.ledger import closing
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.shared.clock import SystemClock
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.integration.test_dealing_api import Api, api, code, signed_in_staff
from tests.support.database import TEST_SOURCE
from tests.support.investments import today
from tests.support.ledger import staff

pytestmark = pytest.mark.integration
__all__ = ["api"]

CLOCK = SystemClock()


async def test_finance_is_for_finance_staff(api: Api) -> None:
    _, finance = await signed_in_staff(api, "finance")
    _, operations = await signed_in_staff(api, "operations")
    refused = await api.http.get("/staff/finance", headers=operations)
    assert (refused.status_code, code(refused)) == (403, "forbidden")
    assert (await api.http.get("/staff/finance")).status_code == 401
    days = {"date_from": "2001-01-01", "date_to": "2001-01-01"}
    refused = await api.http.post("/staff/finance/journal.csv", json=days, headers=operations)
    assert (refused.status_code, code(refused)) == (403, "forbidden")
    # The export records itself, so it is never a GET a browser or proxy might repeat.
    assert (await api.http.get("/staff/finance/journal.csv", headers=finance)).status_code == 405
    future = today().replace(day=1) + timedelta(days=40)
    for month, problem in (("2026-13", "bad_month"), (f"{future:%Y-%m}", "month_in_future")):
        bad = await api.http.get(f"/staff/finance?month={month}", headers=finance)
        assert (bad.status_code, code(bad)) == (422, "validation_failed")
        assert bad.json()["error"]["fields"][0]["code"] == problem


async def test_the_screen_shows_the_months_books_and_finvests_income_and_costs(api: Api) -> None:
    suffix = secrets.token_hex(6)
    cash_code, income_code, cost_code = (
        f"test.cash_{suffix}",
        f"test.income_{suffix}",
        f"test.cost_{suffix}",
    )
    async with api.database.actor_transaction(staff()) as session:
        cash = await ledger.open_account(
            session, AccountSpec(cash_code, AccountKind.ASSET, Currency.UGX, "Cash"), CLOCK
        )
        income = await ledger.open_account(
            session,
            AccountSpec(income_code, AccountKind.INCOME, Currency.UGX, "Bill income"),
            CLOCK,
        )
        cost = await ledger.open_account(
            session, AccountSpec(cost_code, AccountKind.EXPENSE, Currency.UGX, "Fees"), CLOCK
        )
        for debit_to, credit_to, minor in ((cash.id, income.id, 9_000), (cost.id, cash.id, 700)):
            await ledger.post_entry(
                session,
                EntryDraft(
                    entry_type="test.finance",
                    effective_date=today(),
                    lines=(
                        debit(debit_to, Money(minor, Currency.UGX)),
                        credit(credit_to, Money(minor, Currency.UGX)),
                    ),
                    idempotency_key=f"test-{push_id()}",
                    source_type=TEST_SOURCE,
                    source_id=push_id(),
                ),
                CLOCK,
            )
    _, finance = await signed_in_staff(api, "finance")
    response = await api.http.get("/staff/finance?currency=UGX", headers=finance)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["month"] == f"{today():%Y-%m}"
    assert (body["date_from"], body["date_to"]) == (
        today().replace(day=1).isoformat(),
        today().isoformat(),
    )
    (line,) = [a for a in body["accounts"] if a["code"] == cash_code]
    assert line == {
        "code": cash_code,
        "name": "Cash",
        "kind": "asset",
        "opening": {"minor": 0, "currency": "UGX"},
        "debits": {"minor": 9_000, "currency": "UGX"},
        "credits": {"minor": 700, "currency": "UGX"},
        "closing": {"minor": 8_300, "currency": "UGX"},
    }
    assert {
        "code": income_code,
        "name": "Bill income",
        "amount": {"minor": 9_000, "currency": "UGX"},
    } in body["income"]["income"]
    assert {"code": cost_code, "name": "Fees", "amount": {"minor": 700, "currency": "UGX"}} in body[
        "income"
    ]["costs"]
    net = body["income"]["income_total"]["minor"] - body["income"]["cost_total"]["minor"]
    assert body["income"]["net"]["minor"] == net
    last_month = (today().replace(day=1) - timedelta(days=1)).replace(day=1)
    earlier = await api.http.get(f"/staff/finance?month={last_month:%Y-%m}", headers=finance)
    assert earlier.status_code == 200
    assert cash_code not in {a["code"] for a in earlier.json()["accounts"]}


async def test_a_closed_day_is_exported_once_asked_and_shows_as_sent(api: Api) -> None:
    exporter_id, finance = await signed_in_staff(api, "finance")
    closer = staff()
    async with api.database.actor_transaction(closer) as session:
        # Integration tests only close days in 2001, so close its first day if nothing is closed.
        closed = (
            await closing.closed_through(session)
            or (
                await closing.close_through(session, closer, date(2001, 1, 1), CLOCK)
            ).closed_through
        )
    day = closed.isoformat()
    open_day = today().isoformat()
    not_closed = await api.http.post(
        "/staff/finance/journal.csv",
        json={"date_from": open_day, "date_to": open_day},
        headers=finance,
    )
    assert (not_closed.status_code, code(not_closed)) == (409, "conflict")
    assert not_closed.json()["error"]["fields"][0]["code"] == "day_not_closed"
    before = (closed - timedelta(days=1)).isoformat()
    backwards = await api.http.post(
        "/staff/finance/journal.csv", json={"date_from": day, "date_to": before}, headers=finance
    )
    assert (backwards.status_code, code(backwards)) == (422, "validation_failed")
    assert backwards.json()["error"]["fields"][0]["code"] == "days_backwards"
    exported = await api.http.post(
        "/staff/finance/journal.csv",
        json={"currency": "UGX", "date_from": day, "date_to": day, "format": "csv"},
        headers=finance,
    )
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("text/csv")
    assert f'filename="finvest-journal-ugx-{day}.csv"' in exported.headers["content-disposition"]
    assert exported.text.startswith(
        "Date,Journal,Account code,Account name,Kind,Currency,Debit,Credit\r\n"
    )
    screen = (await api.http.get("/staff/finance", headers=finance)).json()
    assert screen["closed_through"] == day
    assert screen["sent_through"] >= day
    mine = [e for e in screen["exports"] if e["exported_by"] == exporter_id]
    assert mine and mine[0]["date_from"] == day and mine[0]["exported_by_name"] == "Ada Nakato"
    assert mine[0]["format"] == "csv"
