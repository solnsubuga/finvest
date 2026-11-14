import dataclasses
import re
from datetime import date, datetime

import pytest

from app.modules.documents.domain.emails import (
    receipt_email,
    statement_email,
    tax_certificate_email,
)
from app.modules.documents.domain.receipts import Receipt
from app.modules.documents.domain.statements import (
    Activity,
    ActivityKind,
    Holding,
    Period,
    Statement,
    StatementKind,
    summarise,
)
from app.modules.documents.pdf import receipt_pdf, statement_pdf, tax_certificate_pdf
from app.modules.tax import service as tax
from app.shared.business_day import KAMPALA
from app.shared.email_layout import Links
from app.shared.money import Currency, Money


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


def statement(lines: int = 3, *, name: str = "Amara Nakato") -> Statement:
    period = Period(date(2026, 1, 1), date(2026, 12, 31))
    activity = tuple(
        Activity(
            date(2026, 1 + n % 12, 1 + n % 28),
            ActivityKind.PAID_IN,
            "Payment received, MTN Mobile Money",
            f"FV-{n}",
            ugx(1_000 + n),
        )
        for n in range(lines)
    )
    activity = tuple(sorted(activity, key=lambda item: item.on))
    closing = sum((item.amount for item in activity), ugx(0))
    return Statement(
        reference="ST-8fK2xQ7m",
        kind=StatementKind.REQUESTED,
        client_name=name,
        account_number="1001004827",
        period=period,
        issued_on=date(2027, 1, 2),
        summaries=(summarise(Currency.UGX, opening=ugx(0), closing=closing, activity=activity),),
        holdings=(
            Holding(
                "91 Day Treasury Bill",
                "IN-1",
                date(2026, 1, 1),
                date(2026, 4, 2),
                "Running",
                ugx(1_000_000),
                ugx(26_490),
                ugx(1_026_490),
            ),
        ),
        activity=activity,
    )


def receipt(name: str = "Amara Nakato") -> Receipt:
    return Receipt(
        reference="RC-Vt5nC1zd",
        client_name=name,
        account_number="1001004827",
        amount=ugx(1_000_000),
        received_at=datetime(2026, 11, 13, 15, 32, tzinfo=KAMPALA),
        method="MTN Mobile Money",
        paid_from="number ending 456",
        provider_reference="MM-1794573-5678",
        payment_reference="FV-O9cK2mXa",
        paid_for="91 Day Treasury Bill",
    )


def pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page\b", pdf))


def test_a_statement_pdf_is_a_pdf_and_the_same_every_time() -> None:
    first = statement_pdf(statement())
    assert first.startswith(b"%PDF-")
    assert first == statement_pdf(statement())
    assert pages(first) == 1


def test_a_long_statement_runs_onto_more_pages() -> None:
    assert pages(statement_pdf(statement(lines=80))) >= 3


def test_an_empty_statement_still_draws() -> None:
    empty = Statement(
        reference="ST-1",
        kind=StatementKind.MONTHLY,
        client_name="Amara Nakato",
        account_number="1001004827",
        period=Period(date(2026, 10, 1), date(2026, 10, 31)),
        issued_on=date(2026, 11, 1),
        summaries=(summarise(Currency.UGX, opening=ugx(0), closing=ugx(0), activity=()),),
        holdings=(),
        activity=(),
    )
    assert statement_pdf(empty).startswith(b"%PDF-")


def test_names_outside_basic_latin_are_drawn() -> None:
    assert statement_pdf(statement(name="Ññámbi Kïrabo-Ōkello")).startswith(b"%PDF-")
    assert receipt_pdf(receipt(name="Ññámbi Kïrabo")).startswith(b"%PDF-")


def test_a_receipt_pdf_is_one_page_and_the_same_every_time() -> None:
    first = receipt_pdf(receipt())
    assert first == receipt_pdf(receipt())
    assert pages(first) == 1


def test_a_statement_email_carries_no_figures() -> None:
    for kind, requested_at in (
        (StatementKind.MONTHLY, None),
        (StatementKind.REQUESTED, datetime(2026, 11, 13, 15, 40, tzinfo=KAMPALA)),
    ):
        mail = statement_email(
            kind=kind,
            first_name="Amara",
            period=Period(date(2026, 10, 1), date(2026, 10, 31)),
            account_number="1001004827",
            file_name="Finvest statement October 2026.pdf",
            requested_at=requested_at,
        )
        for body in (mail.subject, mail.text, mail.html):
            assert "UGX" not in body
            assert "$" not in body
            assert "%" not in body.replace("100%", "")
        assert "1001 004 827" in mail.text
        assert "https://app.finvestug.com/documents" in mail.html


def test_a_requested_statement_email_says_when_it_was_asked_for() -> None:
    mail = statement_email(
        kind=StatementKind.REQUESTED,
        first_name="Amara",
        period=Period(date(2026, 8, 1), date(2026, 11, 13)),
        account_number="1001004827",
        file_name="statement.pdf",
        requested_at=datetime(2026, 11, 13, 12, 40, tzinfo=KAMPALA),
    )
    assert "13 Nov 2026, 12:40" in mail.text
    assert "did not ask" in mail.text
    with pytest.raises(ValueError, match="asked for"):
        statement_email(
            kind=StatementKind.REQUESTED,
            first_name="Amara",
            period=Period(date(2026, 8, 1), date(2026, 11, 13)),
            account_number="1001004827",
            file_name="statement.pdf",
        )


def test_every_link_in_an_email_goes_to_finvest() -> None:
    mails = [
        receipt_email(first_name="Amara", receipt=receipt(), file_name="receipt.pdf"),
        statement_email(
            kind=StatementKind.MONTHLY,
            first_name="Amara",
            period=Period(date(2026, 10, 1), date(2026, 10, 31)),
            account_number="1001004827",
            file_name="statement.pdf",
        ),
    ]
    for mail in mails:
        links = re.findall(r'href="([^"]+)"', mail.html)
        assert links
        assert all(
            re.fullmatch(r"https://(app\.)?finvestug\.com(/[a-z]*)?", link) for link in links
        )
        assert "<img" not in mail.html


def test_values_are_escaped_in_the_html() -> None:
    mail = receipt_email(first_name="<b>Amara</b>", receipt=receipt(), file_name="a&b.pdf")
    assert "<b>Amara" not in mail.html
    assert "&lt;b&gt;Amara" in mail.html
    assert "a&amp;b.pdf" in mail.html


def test_a_receipt_email_shows_the_amount_and_the_receipt_number() -> None:
    mail = receipt_email(first_name="Amara", receipt=receipt(), file_name="receipt.pdf")
    assert mail.subject == "Receipt: we received UGX 1,000,000"
    assert "RC-Vt5nC1zd" in mail.text
    assert "number ending 456" in mail.html
    assert "772" not in mail.html


def test_a_statement_over_days_that_are_not_final_says_so_and_still_draws() -> None:
    final = statement()
    open_days = dataclasses.replace(
        final, kind=StatementKind.REQUESTED, not_final_from=date(2026, 12, 20)
    )
    drawn = statement_pdf(open_days)
    assert drawn.startswith(b"%PDF-")
    assert drawn != statement_pdf(final)
    assert pages(drawn) == 1


# The yearly tax certificate ---------------------------------------------------------------------


def certificate(lines: int = 2, *, tin: str | None = "1000123456") -> tax.Certificate:
    made = tuple(
        tax.CertificateLine(
            investment_id=f"-Nabc00000000000{n:04d}",
            product_name="91 Day Treasury Bill",
            start_date=date(2026, 7, 1 + n % 28),
            maturity_date=date(2026, 9, 30),
            gross=ugx(31_164 + n),
            tax=ugx(4_674),
            net=ugx(26_490 + n),
        )
        for n in range(lines)
    )
    return tax.Certificate(
        client_id="-Nabc0000000000000B1",
        holder_name="Amara Nakato",
        account_number="1001004827",
        tin=tin,
        tax_year=tax.TaxYear(2026),
        lines=made,
        totals=(
            tax.CertificateTotal(
                Currency.UGX,
                sum((line.gross for line in made), ugx(0)),
                sum((line.tax for line in made), ugx(0)),
                sum((line.net for line in made), ugx(0)),
            ),
        ),
    )


def test_a_tax_certificate_pdf_is_a_pdf_and_the_same_every_time() -> None:
    first = tax_certificate_pdf(certificate(), reference="TC-1", issued_on=date(2027, 7, 2))
    assert first.startswith(b"%PDF-")
    assert first == tax_certificate_pdf(certificate(), reference="TC-1", issued_on=date(2027, 7, 2))
    assert pages(first) == 1


def test_a_tax_certificate_with_many_investments_or_no_tin_still_draws() -> None:
    long = tax_certificate_pdf(certificate(lines=60), reference="TC-1", issued_on=date(2027, 7, 2))
    assert pages(long) >= 2
    assert tax_certificate_pdf(
        certificate(tin=None), reference="TC-1", issued_on=date(2027, 7, 2)
    ).startswith(b"%PDF-")


def test_a_tax_certificate_email_carries_no_figures_and_no_rate() -> None:
    mail = tax_certificate_email(
        first_name="Amara",
        tax_year="2026-27",
        period=Period(date(2026, 7, 1), date(2027, 6, 30)),
        account_number="1001004827",
        file_name="Finvest tax certificate 2026-27.pdf",
    )
    assert mail.subject == "Your Finvest tax certificate for 2026-27"
    for body in (mail.subject, mail.text, mail.html):
        assert "UGX" not in body
        assert "$" not in body
        assert "%" not in body.replace("100%", "")
    assert "1 Jul 2026 to 30 Jun 2027" in mail.text
    assert "1001 004 827" in mail.text
    links = re.findall(r'href="([^"]+)"', mail.html)
    assert all(re.fullmatch(r"https://(app\.)?finvestug\.com(/[a-z]*)?", link) for link in links)


def test_document_emails_link_to_the_app_and_site_they_are_given() -> None:
    links = Links(app="https://app.staging.example", site="https://staging.example")
    period = Period(date(2026, 10, 1), date(2026, 10, 31))
    mails = [
        statement_email(
            kind=StatementKind.MONTHLY,
            first_name="Amara",
            period=period,
            account_number="1001004827",
            file_name="Finvest statement October 2026.pdf",
            links=links,
        ),
        tax_certificate_email(
            first_name="Amara",
            tax_year="2025/26",
            period=period,
            account_number="1001004827",
            file_name="Finvest tax certificate.pdf",
            links=links,
        ),
    ]
    for mail in mails:
        assert "https://app.staging.example/documents" in mail.html
        assert "https://app.staging.example/documents" in mail.text
        assert 'href="https://staging.example"' in mail.html
        assert "app.finvestug.com" not in mail.html


def test_every_document_email_says_the_pdf_opens_with_the_date_of_birth() -> None:
    period = Period(date(2026, 10, 1), date(2026, 10, 31))
    mails = [
        statement_email(
            kind=StatementKind.MONTHLY,
            first_name="Amara",
            period=period,
            account_number="1001004827",
            file_name="statement.pdf",
        ),
        receipt_email(first_name="Amara", receipt=receipt(), file_name="receipt.pdf"),
        tax_certificate_email(
            first_name="Amara",
            tax_year="2025-26",
            period=period,
            account_number="1001004827",
            file_name="certificate.pdf",
        ),
    ]
    for mail in mails:
        for body in (mail.text, mail.html):
            assert "date of birth" in body and "05031990" in body
            assert "Password protected" in body or "password protected" in body
        # The client's own date of birth is never written in the email.
        assert "1994" not in mail.text
