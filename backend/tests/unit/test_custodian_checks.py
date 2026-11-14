"""The custodian statement check's pure rules, the file-to-text step and the readers."""

import io
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from openpyxl import Workbook
from pydantic import SecretStr, ValidationError

from app.modules.reconciliation import claude_reader
from app.modules.reconciliation.claude_reader import ClaudeStatementReader
from app.modules.reconciliation.custodian_api import sniff
from app.modules.reconciliation.custodian_reading import DevelopmentStubReader, as_text
from app.modules.reconciliation.custodian_settings import CustodianReaderSettings
from app.modules.reconciliation.domain.custodian import (
    XLSX,
    AmountScale,
    CountedRow,
    CustodianCheckError,
    Held,
    ItemKind,
    ParsedRow,
    ReaderRefused,
    ReaderUnavailable,
    ReadRow,
    StatementFile,
    compare,
    may_sign_off,
    parse_row,
    unexplained,
)
from app.shared.money import Currency, Money

DAY = date(2026, 11, 13)
DEC = date(2026, 12, 3)
FEB = date(2027, 2, 4)


def read(face: str, matures_on: str | None = "2026-12-03", currency: str | None = "UGX") -> ReadRow:
    return ReadRow(
        instrument="91 day bill",
        code=None,
        face_value=face,
        currency=currency,
        matures_on=matures_on,
        location="page 1",
        source_text=f"91 day bill {face}",
    )


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


# Rows -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("printed", "scale", "minor"),
    [
        ("1,250,000,000", AmountScale.ONE, 1_250_000_000),
        ("500000000.00", AmountScale.ONE, 500_000_000),
        ("1,250,000", AmountScale.THOUSAND, 1_250_000_000),
        ("12", AmountScale.MILLION, 12_000_000),
    ],
)
def test_a_printed_face_value_becomes_whole_shillings(
    printed: str, scale: AmountScale, minor: int
) -> None:
    assert parse_row(read(printed), scale) == ParsedRow(ugx(minor), DEC)


@pytest.mark.parametrize(
    "printed", ["1.250.000", "1500,00", "-500,000", "about 5m", "", "0", "1e9"]
)
def test_an_amount_that_cannot_be_read_exactly_is_never_guessed(printed: str) -> None:
    with pytest.raises(CustodianCheckError) as refused:
        parse_row(read(printed), AmountScale.ONE)
    assert refused.value.code == "row_amount_unreadable"


def test_a_fraction_of_a_shilling_is_refused() -> None:
    with pytest.raises(CustodianCheckError):
        parse_row(read("1,000,000.50"), AmountScale.ONE)


@pytest.mark.parametrize("matures_on", [None, "", "03/12/2026", "2026-02-30"])
def test_a_missing_or_unclear_maturity_is_unreadable(matures_on: str | None) -> None:
    with pytest.raises(CustodianCheckError) as refused:
        parse_row(read("1,000", matures_on=matures_on), AmountScale.ONE)
    assert refused.value.code == "row_maturity_unreadable"


@pytest.mark.parametrize(("printed", "currency"), [(None, "UGX"), ("Shs", "UGX"), ("usd", "USD")])
def test_currency_defaults_to_shillings(printed: str | None, currency: str) -> None:
    parsed = parse_row(read("1,000", currency=printed), AmountScale.ONE)
    assert parsed.face.currency.value == currency


def test_an_unknown_currency_is_unreadable() -> None:
    with pytest.raises(CustodianCheckError) as refused:
        parse_row(read("1,000", currency="KES"), AmountScale.ONE)
    assert refused.value.code == "row_currency_unknown"


# Comparison -------------------------------------------------------------------------------


def held(face: int, day: date = DEC, code: str = "TREA-91") -> Held:
    return Held(code, Currency.UGX, day, face)


def row(row_id: str, face: int, day: date = DEC) -> CountedRow:
    return CountedRow(row_id, ParsedRow(ugx(face), day))


def test_matching_holdings_need_no_note() -> None:
    items = compare(
        [held(600), held(400, code="TREA-182")],
        [row("a", 1_000)],
        statement_date=DAY,
        read_date=DAY,
    )
    assert [(i.kind, i.finvest_face_minor, i.custodian_face_minor) for i in items] == [
        (ItemKind.MATCH, 1_000, 1_000)
    ]
    assert items[0].product_codes == ("TREA-182", "TREA-91")
    assert not items[0].needs_note
    assert unexplained(items, {}) == ()


def test_every_kind_of_difference_is_listed_in_a_stable_order() -> None:
    items = compare(
        [held(1_000), held(2_000, day=FEB), held(5, day=date(2026, 11, 20))],
        [
            row("a", 900),
            row("b", 300, day=date(2027, 5, 6)),
            CountedRow("c", None, problem="row_amount_unreadable"),
            CountedRow("d", None, left_out=True),
        ],
        statement_date=DAY,
        read_date=date(2026, 11, 12),
    )
    assert [(i.key, i.kind) for i in items] == [
        ("maturity:UGX:2026-11-20", ItemKind.MISSING_AT_CUSTODIAN),
        ("maturity:UGX:2026-12-03", ItemKind.AMOUNT_DIFFERS),
        ("maturity:UGX:2027-02-04", ItemKind.MISSING_AT_CUSTODIAN),
        ("maturity:UGX:2027-05-06", ItemKind.NOT_IN_FINVEST),
        ("row:c", ItemKind.ROW_UNREADABLE),
        ("statement_date", ItemKind.STATEMENT_DATE_DIFFERS),
    ]
    assert items[1].difference_minor == -100
    assert items[3].difference_minor == 300
    assert unexplained(items, {"maturity:UGX:2026-12-03": "Sold on the 12th", "row:c": " "}) == (
        "maturity:UGX:2026-11-20",
        "maturity:UGX:2027-02-04",
        "maturity:UGX:2027-05-06",
        "row:c",
        "statement_date",
    )


def test_a_statement_with_no_date_is_a_difference() -> None:
    items = compare([], [], statement_date=DAY, read_date=None)
    assert [i.kind for i in items] == [ItemKind.STATEMENT_DATE_UNREAD]


def test_whoever_sends_a_check_never_signs_it_off() -> None:
    with pytest.raises(CustodianCheckError) as refused:
        may_sign_off(submitted_by="a", signer="a")
    assert refused.value.code == "check_needs_second_person"
    with pytest.raises(CustodianCheckError):
        may_sign_off(submitted_by=None, signer="a")
    may_sign_off(submitted_by="a", signer="b")


# Files -------------------------------------------------------------------------------------


def workbook(*rows: tuple[object, ...]) -> bytes:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Holdings"
    for each in rows:
        sheet.append(list(each))
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_a_spreadsheet_is_read_as_numbered_text_without_rounding() -> None:
    data = workbook(
        ("Instrument", "Face value", "Maturity"),
        ("91 day bill", 1_250_000_000, datetime(2026, 12, 3)),
        (None, None, None),
        ("Bond", 1.5, "2030-01-01"),
        ("Huge", 1e17, None),
    )
    assert as_text(StatementFile(XLSX, data)).splitlines() == [
        "Sheet Holdings row 1: Instrument | Face value | Maturity",
        "Sheet Holdings row 2: 91 day bill | 1250000000 | 2026-12-03",
        "Sheet Holdings row 4: Bond | [number not exact] | 2030-01-01",
        "Sheet Holdings row 5: Huge | [number not exact] | ",
    ]


def test_a_csv_is_read_as_numbered_text() -> None:
    data = b'Instrument,Face\n\n91 day bill,"1,000,000"\n'
    assert as_text(StatementFile("text/csv", data)).splitlines() == [
        "Row 1: Instrument | Face",
        "Row 3: 91 day bill | 1,000,000",
    ]


def test_a_damaged_spreadsheet_is_refused() -> None:
    with pytest.raises(ReaderRefused) as refused:
        as_text(StatementFile(XLSX, b"PK\x03\x04 not really"))
    assert refused.value.code == "statement_not_a_spreadsheet"


@pytest.mark.parametrize(
    ("data", "content_type"),
    [
        (b"%PDF-1.7 ...", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n...", "image/png"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"PK\x03\x04...", XLSX),
        (b"a,b\n1,2\n", "text/csv"),
        (b"MZ\x00\x00", None),
    ],
)
def test_the_file_type_comes_from_its_bytes(data: bytes, content_type: str | None) -> None:
    assert sniff(data) == content_type


# Readers -----------------------------------------------------------------------------------


async def test_the_stub_reads_its_own_csv_layout() -> None:
    data = (
        b"instrument,code,face_value,currency,maturity_date,statement_date\n"
        b'91 day bill,UG123,"1,000,000",UGX,2026-12-03,2026-11-13\n'
    )
    reading = await DevelopmentStubReader().read(StatementFile("text/csv", data), key="k")
    assert reading.statement_date == "2026-11-13"
    assert reading.rows[0].face_value == "1,000,000"
    assert reading.rows[0].location == "Row 2"
    with pytest.raises(ReaderRefused):
        await DevelopmentStubReader().read(StatementFile("application/pdf", b"%PDF"), key="k")


def test_the_stub_never_runs_in_production() -> None:
    with pytest.raises(ValidationError):
        CustodianReaderSettings(custodian_reader="development-stub", environment="production")
    with pytest.raises(ValidationError):
        CustodianReaderSettings(custodian_reader="claude")
    assert CustodianReaderSettings().reader() is None
    configured = CustodianReaderSettings(
        custodian_reader="claude", anthropic_api_key=SecretStr("sk-test")
    )
    assert isinstance(configured.reader(), ClaudeStatementReader)
    assert "sk-test" not in repr(configured.reader())


ANSWER = {
    "statement_date": "2026-11-13",
    "custodian_name": "A custodian",
    "account_reference": "FIN-001",
    "amount_scale": "thousand",
    "rows": [
        {
            "instrument": "91 day bill",
            "code": "UG000123",
            "face_value": "1,250,000",
            "currency": "UGX",
            "matures_on": "2026-12-03",
            "location": "page 1",
            "source_text": "UG000123 91D 03/12/2026 1,250,000",
        }
    ],
}


@dataclass
class FakeStream:
    message: Any

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get_final_message(self) -> Any:
        return self.message


@dataclass
class FakeMessages:
    answer: Any = None
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def stream(self, **kwargs: Any) -> FakeStream:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return FakeStream(self.answer)


def message(text: str | None, stop_reason: str = "end_turn") -> Any:
    content = [] if text is None else [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(stop_reason=stop_reason, content=content, model="claude-opus-5-5")


def reader(messages: FakeMessages) -> ClaudeStatementReader:
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return ClaudeStatementReader(api_key=SecretStr(""), client=client)  # type: ignore[arg-type]


async def test_the_claude_reader_copies_rows_and_never_works_out_amounts() -> None:
    messages = FakeMessages(answer=message(json.dumps(ANSWER)))
    pdf = StatementFile("application/pdf", b"%PDF-1.7 statement")
    reading = await reader(messages).read(pdf, key="-Ncheck0000000000001")
    assert reading.amount_scale is AmountScale.THOUSAND
    assert reading.rows[0].face_value == "1,250,000"
    assert reading.reader == "claude:claude-opus-5-5"
    call = messages.calls[0]
    assert call["model"] == claude_reader.MODEL
    assert call["output_config"]["format"]["schema"] == claude_reader.SCHEMA
    assert call["output_config"]["effort"] == "high"
    assert call["fallbacks"] == "default"
    document = call["messages"][0]["content"][0]
    assert document["type"] == "document"
    assert document["source"]["media_type"] == "application/pdf"
    # The parsed amount is the domain's work, from the copied text.
    assert parse_row(reading.rows[0], reading.amount_scale).face == ugx(1_250_000_000)


async def test_a_spreadsheet_goes_to_claude_as_text() -> None:
    messages = FakeMessages(answer=message(json.dumps(ANSWER)))
    data = workbook(("91 day bill", 1_000))
    await reader(messages).read(StatementFile(XLSX, data), key="k")
    source = messages.calls[0]["messages"][0]["content"][0]["source"]
    assert source == {
        "type": "text",
        "media_type": "text/plain",
        "data": "Sheet Holdings row 1: 91 day bill | 1000",
    }


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (message(None, "refusal"), "reader_declined"),
        (message('{"rows": [', "max_tokens"), "statement_too_long"),
        (message("not json"), "reader_answer_invalid"),
        (message(json.dumps({**ANSWER, "extra": 1})), "reader_answer_invalid"),
        (
            message(json.dumps({**ANSWER, "rows": [ANSWER["rows"][0]] * 501})),
            "statement_too_many_rows",
        ),
        (message(None), "reader_gave_no_answer"),
    ],
)
async def test_a_bad_answer_is_refused_whole(answer: Any, code: str) -> None:
    with pytest.raises(ReaderRefused) as refused:
        await reader(FakeMessages(answer=answer)).read(StatementFile("image/png", b"x"), key="k")
    assert refused.value.code == code


def status_error(status: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request, json={"error": {"message": "secret"}})
    return anthropic.APIStatusError("failed", response=response, body=None)


@pytest.mark.parametrize("status", [500, 529, 408])
async def test_a_server_error_can_be_asked_again(status: int) -> None:
    with pytest.raises(ReaderUnavailable):
        await reader(FakeMessages(error=status_error(status))).read(
            StatementFile("image/png", b"x"), key="k"
        )


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "reader_not_authorised"),
        (413, "statement_too_large"),
        (400, "reader_rejected_request"),
    ],
)
async def test_a_client_error_is_refused(status: int, code: str) -> None:
    with pytest.raises(ReaderRefused) as refused:
        await reader(FakeMessages(error=status_error(status))).read(
            StatementFile("image/png", b"x"), key="k"
        )
    assert refused.value.code == code
    assert refused.value.__cause__ is None
