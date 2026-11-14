from datetime import date

import pytest

from app.modules.documents.domain.emails import ended_note_email, investment_note_email
from app.modules.documents.domain.investment_notes import (
    AtMaturityFigures,
    EndedNote,
    Ending,
    InvestmentNote,
)
from app.modules.documents.pdf import ended_note_pdf, ended_what_happened, investment_note_pdf
from app.shared.money import Currency, Money


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


def note(*, fixed: bool = True, **changes: object) -> InvestmentNote:
    values: dict[str, object] = {
        "reference": "IN-0PAbc123",
        "client_name": "Amara NAKATO",
        "account_number": "1001004827",
        "investment_id": "0PAinv456xyz",
        "product_name": "91 Day Treasury Bill",
        "amount": ugx(5_000_000),
        "rate_percent": "10.63",
        "start_date": date(2026, 11, 14),
        "term_days": 91 if fixed else None,
        "maturity_date": date(2027, 2, 13) if fixed else None,
        "at_maturity": AtMaturityFigures(ugx(132_514), ugx(5_132_514)) if fixed else None,
        "then": "Roll over into a new investment" if fixed else None,
    }
    values.update(changes)
    return InvestmentNote(**values)  # type: ignore[arg-type]


def test_a_note_is_for_an_amount_above_zero_that_adds_up() -> None:
    with pytest.raises(ValueError, match="above zero"):
        note(amount=ugx(0))
    with pytest.raises(ValueError, match="plus the interest"):
        note(at_maturity=AtMaturityFigures(ugx(132_514), ugx(5_000_000)))
    with pytest.raises(ValueError, match="matures after it starts"):
        note(maturity_date=date(2026, 11, 14))
    with pytest.raises(ValueError, match="fixed term"):
        note(then=None)
    assert note(fixed=False).is_open_term


def test_a_note_pdf_is_a_pdf_and_the_same_every_time() -> None:
    for each in (note(), note(fixed=False)):
        first = investment_note_pdf(each, issued_on=date(2026, 11, 14))
        assert first.startswith(b"%PDF-")
        assert first == investment_note_pdf(each, issued_on=date(2026, 11, 14))


def test_a_note_email_carries_no_figures_and_says_how_to_open_the_pdf() -> None:
    for each in (note(), note(fixed=False)):
        mail = investment_note_email(first_name="Amara", note=each, file_name="note.pdf")
        assert mail.subject == "Your Finvest investment note: 91 Day Treasury Bill"
        for body in (mail.text, mail.html):
            assert "UGX" not in body and "10.63" not in body
            assert "05031990" in body
    fixed = investment_note_email(first_name="Amara", note=note(), file_name="note.pdf")
    assert "matures on 13 Feb 2027" in fixed.text


def ended(ending: Ending = Ending.PAID_OUT, **changes: object) -> EndedNote:
    values: dict[str, object] = {
        "reference": "IN-0PAend123",
        "client_name": "Amara NAKATO",
        "account_number": "1001004827",
        "investment_id": "0PAinv456xyz",
        "product_name": "91 Day Treasury Bill",
        "amount": ugx(5_000_000),
        "interest": ugx(132_514),
        "start_date": date(2026, 11, 14),
        "ended_on": date(2027, 2, 13),
        "ending": ending,
        "rolled_into_id": "0PAnew789abc" if ending is Ending.ROLLED_OVER else None,
    }
    values.update(changes)
    return EndedNote(**values)  # type: ignore[arg-type]


def test_an_end_note_comes_to_the_amount_plus_the_interest_and_names_a_rollover() -> None:
    assert ended().total == ugx(5_132_514)
    with pytest.raises(ValueError, match="above zero"):
        ended(amount=ugx(0))
    with pytest.raises(ValueError, match="never below zero"):
        ended(interest=ugx(-1))
    with pytest.raises(ValueError, match="on or after it starts"):
        ended(ended_on=date(2026, 11, 13))
    with pytest.raises(ValueError, match="names the investment it opened"):
        ended(Ending.PAID_OUT, rolled_into_id="0PAnew789abc")
    with pytest.raises(ValueError, match="names the investment it opened"):
        ended(Ending.ROLLED_OVER, rolled_into_id=None)


def test_an_end_note_pdf_is_a_pdf_and_the_same_every_time_for_every_ending() -> None:
    for ending in Ending:
        each = ended(ending)
        first = ended_note_pdf(each, issued_on=date(2027, 2, 13))
        assert first.startswith(b"%PDF-")
        assert first == ended_note_pdf(each, issued_on=date(2027, 2, 13))
    assert "0PAnew789abc" in ended_what_happened(ended(Ending.ROLLED_OVER))


def test_an_end_email_says_how_it_ended_with_no_figures() -> None:
    said = {
        Ending.PAID_OUT: "on its way",
        Ending.ROLLED_OVER: "rolled over",
        Ending.WAITING: "Choose in the app",
        Ending.WITHDRAWN: "withdrawn early",
    }
    for ending, words in said.items():
        mail = ended_note_email(first_name="Amara", note=ended(ending), file_name="note.pdf")
        assert words in mail.text
        for body in (mail.text, mail.html):
            assert "UGX" not in body and "05031990" in body
