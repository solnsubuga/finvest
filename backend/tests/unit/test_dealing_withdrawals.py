from datetime import date

import pytest

from app.infrastructure.jobs import PermanentJobError
from app.modules.dealing.domain.liquidity import BufferRules, InvalidCash
from app.modules.dealing.domain.withdrawals import (
    CASH_CODES,
    CLAIM_CODES,
    Decision,
    FundingStatus,
    Source,
    buffer_from,
    decide,
    funded_by_sale,
    released_from_queue,
)
from app.modules.dealing.jobs import _withdrawal
from app.shared.money import Currency, Money

M = 1_000_000
RULES = BufferRules(
    date(2026, 1, 1),
    Money(150 * M, Currency.UGX),
    Money(100 * M, Currency.UGX),
    Money(5 * M, Currency.UGX),
    2,
)


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


def figures(cash: int = 0, owed: int = 0) -> tuple[dict[str, Money], dict[str, Money]]:
    cash_by_code = {code: ugx(0) for code in CASH_CODES} | {CASH_CODES[0]: ugx(cash)}
    claims = {code: ugx(0) for code in CLAIM_CODES} | {CLAIM_CODES[0]: ugx(owed)}
    return cash_by_code, claims


def test_the_buffer_is_cash_less_what_it_already_stands_behind() -> None:
    cash, claims = figures(cash=300 * M, owed=40 * M)
    desk = buffer_from(Currency.UGX, cash, claims, ugx(60 * M))
    assert (desk.buffer, desk.short) == (ugx(200 * M), ugx(0))


def test_cash_below_what_clients_are_owed_is_short_not_a_negative_buffer() -> None:
    cash, claims = figures(cash=50 * M, owed=40 * M)
    desk = buffer_from(Currency.UGX, cash, claims, ugx(20 * M))
    assert (desk.buffer, desk.short) == (ugx(0), ugx(10 * M))


def test_every_cash_and_claim_account_is_counted() -> None:
    cash, claims = figures()
    del claims[CLAIM_CODES[-1]]
    with pytest.raises(InvalidCash):
        buffer_from(Currency.UGX, cash, claims, ugx(0))
    cash, claims = figures()
    with pytest.raises(InvalidCash):
        buffer_from(Currency.USD, cash, claims, Money(0, Currency.USD))


@pytest.mark.parametrize(
    ("amount", "buffer", "waiting", "decision"),
    [
        # The buffer seen already counts the withdrawal, so the buffer before it is the sum.
        (5 * M, 95 * M, False, Decision(Source.BUFFER, FundingStatus.FUNDED)),
        (5 * M, 95 * M - 1, False, Decision(Source.BUFFER, FundingStatus.WAITING)),
        (1, 150 * M, True, Decision(Source.BUFFER, FundingStatus.WAITING)),
        (5 * M + 1, 500 * M, False, Decision(Source.SALE, FundingStatus.WAITING)),
        (5 * M + 1, 0, True, Decision(Source.SALE, FundingStatus.WAITING)),
    ],
)
def test_where_a_withdrawal_is_funded_from(
    amount: int, buffer: int, waiting: bool, decision: Decision
) -> None:
    assert decide(ugx(amount), ugx(buffer), RULES, others_waiting_for_buffer=waiting) == decision


@pytest.mark.parametrize(
    ("queue", "buffer", "released"),
    [
        ([5 * M, 1 * M], 95 * M, 1),
        ([5 * M, 5 * M], 95 * M, 2),
        ([1 * M, 5 * M], 95 * M, 0),
        ([], 200 * M, 0),
        ([1 * M, 2 * M], 99 * M, 2),
    ],
)
def test_the_queue_is_released_in_order_while_the_buffer_allows(
    queue: list[int], buffer: int, released: int
) -> None:
    assert released_from_queue([ugx(a) for a in queue], ugx(buffer), RULES) == released


@pytest.mark.parametrize(
    ("waiting", "proceeds", "funded"),
    [
        ([20 * M, 15 * M, 10 * M], 39 * M, 2),
        ([20 * M, 15 * M, 10 * M], 45 * M, 3),
        ([20 * M, 1 * M], 19 * M, 0),
        ([], 10 * M, 0),
        ([10 * M], 10 * M, 1),
    ],
)
def test_a_sale_funds_waiting_withdrawals_in_order_up_to_what_it_raised(
    waiting: list[int], proceeds: int, funded: int
) -> None:
    assert funded_by_sale([ugx(a) for a in waiting], ugx(proceeds)) == funded


def test_a_sale_never_funds_another_currency_or_from_negative_proceeds() -> None:
    with pytest.raises(InvalidCash):
        funded_by_sale([Money(1, Currency.USD)], ugx(10))
    with pytest.raises(InvalidCash):
        funded_by_sale([ugx(1)], ugx(-1))


def test_the_job_reads_the_withdrawal_from_the_event() -> None:
    payload = {"data": {"investment_id": "inv-1", "currency": "UGX", "amount_minor": 500}}
    assert _withdrawal(payload) == ("inv-1", ugx(500))


@pytest.mark.parametrize(
    ("data", "code"),
    [
        ({"currency": "UGX", "amount_minor": 500}, "investment_id_missing"),
        ({"investment_id": "inv-1", "currency": "UGX"}, "amount_invalid"),
        ({"investment_id": "inv-1", "currency": "UGX", "amount_minor": "500"}, "amount_invalid"),
        ({"investment_id": "inv-1", "currency": "GBP", "amount_minor": 500}, "amount_invalid"),
        ({"investment_id": "inv-1", "currency": "UGX", "amount_minor": True}, "amount_invalid"),
    ],
)
def test_the_job_refuses_an_event_without_a_withdrawal(data: dict[str, object], code: str) -> None:
    with pytest.raises(PermanentJobError) as error:
        _withdrawal({"data": data})
    assert error.value.code == code
