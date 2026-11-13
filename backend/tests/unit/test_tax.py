"""Tax periods, withholding totals, the yearly certificate, the URA export and the rules for
recording a payment to URA. Pure."""

from datetime import date, timedelta

import pytest

from app.modules.tax.domain.exports import HEADER, PayeeRow, amount, payee_csv
from app.modules.tax.domain.periods import (
    BadPeriod,
    TaxYear,
    due_on,
    month_end,
    month_of,
    months_back,
    next_month,
    parse_month,
    parse_tax_year,
    tax_year_of,
)
from app.modules.tax.domain.remittances import (
    Proposal,
    Refusal,
    RemittanceStatus,
    check_decision,
    check_outstanding,
    check_proposal,
    posting,
)
from app.modules.tax.domain.withholding import (
    Certificate,
    CertificateLine,
    CertificateTotal,
    DoesNotAddUp,
    InvestmentTax,
    certificate,
    check,
    from_ledger,
    payees,
    summaries,
)
from app.shared.money import Currency, Money


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


def usd(minor: int) -> Money:
    return Money(minor, Currency.USD)


# Periods ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "first", "last", "due"),
    [
        (date(2026, 11, 13), date(2026, 11, 1), date(2026, 11, 30), date(2026, 12, 15)),
        (date(2026, 12, 31), date(2026, 12, 1), date(2026, 12, 31), date(2027, 1, 15)),
        (date(2028, 2, 10), date(2028, 2, 1), date(2028, 2, 29), date(2028, 3, 15)),
        (date(2027, 2, 1), date(2027, 2, 1), date(2027, 2, 28), date(2027, 3, 15)),
    ],
)
def test_a_month_runs_first_to_last_day_and_is_due_on_the_15th_after(
    day: date, first: date, last: date, due: date
) -> None:
    assert month_of(day) == first
    assert month_end(first) == last
    assert due_on(first) == due
    assert next_month(first) == last + timedelta(days=1)


def test_months_back_crosses_years() -> None:
    assert months_back(date(2026, 11, 1), 11) == date(2025, 12, 1)
    assert months_back(date(2026, 1, 1), 1) == date(2025, 12, 1)
    assert months_back(date(2026, 1, 1), 0) == date(2026, 1, 1)
    assert months_back(date(2026, 1, 1), 25) == date(2023, 12, 1)


@pytest.mark.parametrize("text", ["2026-11", "2000-01", "2030-12"])
def test_a_month_is_read_from_its_label(text: str) -> None:
    month = parse_month(text)
    assert f"{month.year:04d}-{month.month:02d}" == text and month.day == 1


@pytest.mark.parametrize("text", ["2026-13", "2026-00", "2026-1", "26-11", "1999-12", "", "x"])
def test_anything_else_is_not_a_month(text: str) -> None:
    with pytest.raises(BadPeriod):
        parse_month(text)


@pytest.mark.parametrize(
    ("day", "starts"),
    [
        (date(2026, 7, 1), 2026),
        (date(2026, 6, 30), 2025),
        (date(2027, 6, 30), 2026),
        (date(2026, 12, 31), 2026),
        (date(2027, 1, 1), 2026),
    ],
)
def test_the_tax_year_runs_from_1_july_to_30_june(day: date, starts: int) -> None:
    year = tax_year_of(day)
    assert year == TaxYear(starts)
    assert year.contains(day)
    assert year.first_day == date(starts, 7, 1)
    assert year.last_day == date(starts + 1, 6, 30)


def test_a_tax_year_is_written_as_uras_label() -> None:
    assert TaxYear(2026).label == "2026-27"
    assert TaxYear(2099).label == "2099-00"
    assert parse_tax_year("2026-27") == TaxYear(2026)
    for bad in ("2026-28", "2026", "2026/27", "1999-00", "2026-2"):
        with pytest.raises(BadPeriod):
            parse_tax_year(bad)


# Withholding ------------------------------------------------------------------------------------


def item(
    investment_id: str,
    client_id: str,
    gross: Money,
    tax: Money,
    *,
    start: date | None = date(2026, 8, 1),
) -> InvestmentTax:
    return InvestmentTax(
        investment_id=investment_id,
        client_id=client_id,
        product_name="91 Day Treasury Bill",
        start_date=start,
        maturity_date=None,
        gross=gross,
        tax=tax,
    )


def test_the_ledger_signs_become_interest_and_tax() -> None:
    # interest.expense is debited with interest; tax.withholding_payable is credited with tax.
    assert from_ledger(ugx(31_164), ugx(-4_674)) == (ugx(31_164), ugx(4_674))
    # An unwind reverses both.
    assert from_ledger(ugx(-100), ugx(15)) == (ugx(-100), ugx(-15))


def test_payees_add_up_each_clients_investments_and_drop_the_ones_that_net_to_nothing() -> None:
    lines = payees(
        [
            item("i1", "b", ugx(1_000), ugx(150)),
            item("i2", "a", ugx(500), ugx(75)),
            item("i3", "b", ugx(200), ugx(30)),
            item("i4", "c", ugx(300), ugx(45)),
            item("i5", "c", ugx(-300), ugx(-45)),
        ],
        Currency.UGX,
    )
    assert [(p.client_id, p.gross, p.tax, p.net) for p in lines] == [
        ("a", ugx(500), ugx(75), ugx(425)),
        ("b", ugx(1_200), ugx(180), ugx(1_020)),
    ]


def test_a_month_owes_what_it_withheld_less_what_was_paid_or_is_waiting() -> None:
    nov, oct_, sep = date(2026, 11, 1), date(2026, 10, 1), date(2026, 9, 1)
    found = summaries(
        [sep, nov, oct_],
        {oct_: ugx(10_000), nov: ugx(4_000), sep: ugx(-50)},
        {oct_: ugx(6_000)},
        {oct_: ugx(1_000)},
        Currency.UGX,
    )
    assert [s.month for s in found] == [nov, oct_, sep]
    assert [s.outstanding for s in found] == [ugx(4_000), ugx(3_000), ugx(-50)]
    assert found[1].due_on == date(2026, 11, 15)


# The certificate --------------------------------------------------------------------------------


def make(items: list[InvestmentTax]) -> Certificate:
    return certificate(
        client_id="a",
        holder_name="Amara Nakato",
        account_number="1001004827",
        tin=None,
        tax_year=TaxYear(2026),
        items=items,
    )


def test_a_certificate_lists_each_investment_and_totals_each_currency() -> None:
    made = make(
        [
            item("i2", "a", ugx(31_164), ugx(4_674), start=date(2026, 9, 1)),
            item("i1", "a", ugx(1_000), ugx(150), start=date(2026, 8, 1)),
            item("u1", "a", usd(725), usd(36)),
            item("x9", "someone else", ugx(9_999), ugx(1_499)),
            item("i0", "a", ugx(0), ugx(0)),
        ]
    )
    assert [line.investment_id for line in made.lines] == ["i1", "i2", "u1"]
    assert made.totals == (
        CertificateTotal(Currency.UGX, ugx(32_164), ugx(4_824), ugx(27_340)),
        CertificateTotal(Currency.USD, usd(725), usd(36), usd(689)),
    )
    assert not made.is_empty


def test_a_client_who_earned_nothing_gets_an_empty_certificate() -> None:
    made = make([])
    assert made.is_empty and made.totals == ()


def test_an_investment_undone_after_the_year_turned_gives_back_a_negative_line() -> None:
    made = make([item("i1", "a", ugx(-1_000), ugx(-150))])
    assert made.totals == (CertificateTotal(Currency.UGX, ugx(-1_000), ugx(-150), ugx(-850)),)


def test_an_investment_twice_is_refused() -> None:
    with pytest.raises(DoesNotAddUp):
        make([item("i1", "a", ugx(10), ugx(1)), item("i1", "a", ugx(10), ugx(1))])


def test_a_certificate_that_does_not_add_up_is_refused() -> None:
    good = make([item("i1", "a", ugx(1_000), ugx(150))])
    line = good.lines[0]
    wrong_line = CertificateLine(
        line.investment_id, line.product_name, None, None, ugx(1_000), ugx(150), ugx(851)
    )
    with pytest.raises(DoesNotAddUp):
        check(Certificate("a", "A", "1", None, TaxYear(2026), (wrong_line,), good.totals))
    with pytest.raises(DoesNotAddUp):
        check(
            Certificate(
                "a",
                "A",
                "1",
                None,
                TaxYear(2026),
                good.lines,
                (CertificateTotal(Currency.UGX, ugx(1_000), ugx(151), ugx(849)),),
            )
        )
    with pytest.raises(DoesNotAddUp):
        check(Certificate("a", "A", "1", None, TaxYear(2026), good.lines, ()))


# The export -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("money", "text"),
    [
        (ugx(26_490), "26490"),
        (ugx(0), "0"),
        (ugx(-15), "-15"),
        (usd(123_450), "1234.50"),
        (usd(5), "0.05"),
        (usd(-5), "-0.05"),
    ],
)
def test_amounts_are_written_from_integers(money: Money, text: str) -> None:
    assert amount(money) == text


def test_the_export_has_a_row_per_payee_and_never_starts_a_cell_with_a_formula() -> None:
    text = payee_csv(
        date(2026, 10, 1),
        [
            PayeeRow("NAKATO Amara", "1000123456", "1001004827", ugx(31_164), ugx(4_674)),
            PayeeRow("=HYPERLINK(1)", None, None, usd(725), usd(36)),
        ],
    )
    rows = text.split("\r\n")
    assert rows[0] == ",".join(HEADER)
    assert rows[1] == "2026-10,NAKATO Amara,1000123456,1001004827,UGX,31164,4674"
    assert rows[2] == "2026-10,'=HYPERLINK(1),,,USD,7.25,0.36"
    assert rows[3] == ""


# Remittances ------------------------------------------------------------------------------------

TODAY = date(2026, 11, 13)


def proposal(**changes: object) -> Proposal:
    values: dict[str, object] = {
        "month": date(2026, 10, 1),
        "amount": ugx(10_000),
        "ura_reference": "PRN2210000123",
        "bank_reference": "FT26313XYZ",
        "paid_on": date(2026, 11, 12),
        "note": "October withholding",
    }
    values.update(changes)
    return Proposal(**values)  # type: ignore[arg-type]


def test_a_payment_for_an_ended_month_with_both_references_is_accepted() -> None:
    assert check_proposal(proposal(), TODAY) is None
    assert check_proposal(proposal(paid_on=date(2026, 10, 1)), TODAY) is None


@pytest.mark.parametrize(
    ("changes", "refusal"),
    [
        ({"note": "  "}, Refusal.NOTE_REQUIRED),
        ({"note": "x" * 501}, Refusal.NOTE_REQUIRED),
        ({"amount": ugx(0)}, Refusal.AMOUNT_NOT_POSITIVE),
        ({"ura_reference": " "}, Refusal.URA_REFERENCE_REQUIRED),
        ({"bank_reference": ""}, Refusal.BANK_REFERENCE_REQUIRED),
        ({"bank_reference": "x" * 101}, Refusal.BANK_REFERENCE_REQUIRED),
        ({"month": date(2026, 11, 1)}, Refusal.MONTH_NOT_ENDED),
        ({"paid_on": date(2026, 11, 14)}, Refusal.PAID_IN_THE_FUTURE),
        ({"paid_on": date(2026, 9, 30)}, Refusal.PAID_BEFORE_THE_MONTH),
    ],
)
def test_a_payment_is_refused_when(changes: dict[str, object], refusal: Refusal) -> None:
    assert check_proposal(proposal(**changes), TODAY) is refusal


def test_the_month_has_ended_only_after_its_last_day() -> None:
    assert check_proposal(proposal(month=date(2026, 10, 1)), date(2026, 10, 31)) is (
        Refusal.MONTH_NOT_ENDED
    )


def test_no_more_than_the_month_still_owes() -> None:
    assert check_outstanding(ugx(100), ugx(100)) is None
    assert check_outstanding(ugx(101), ugx(100)) is Refusal.MORE_THAN_OUTSTANDING
    assert check_outstanding(ugx(1), ugx(-5)) is Refusal.MORE_THAN_OUTSTANDING


def test_a_second_person_decides_once() -> None:
    assert check_decision(RemittanceStatus.PROPOSED, "a", "b") is None
    assert check_decision(RemittanceStatus.PROPOSED, "a", "a") is Refusal.SAME_PERSON
    assert check_decision(RemittanceStatus.APPROVED, "a", "b") is Refusal.ALREADY_DECIDED
    assert check_decision(RemittanceStatus.DECLINED, "a", "b") is Refusal.ALREADY_DECIDED


def test_approval_takes_the_amount_from_the_tax_held_and_the_trust_account() -> None:
    draft = posting(
        "r1", ugx(10_000), date(2026, 11, 12), payable_account_id="tax", bank_account_id="bank"
    )
    assert draft.effective_date == date(2026, 11, 12)
    assert draft.source_type == "tax.remittance" and draft.source_id == "r1"
    assert draft.idempotency_key == "tax-remittance-r1"
    assert sum(line.amount.minor for line in draft.lines) == 0
    signed = {line.account_id: line.amount.minor for line in draft.lines}
    assert signed == {"tax": 10_000, "bank": -10_000}
