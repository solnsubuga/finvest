"""Draw a statement or a receipt as an A4 PDF, in Finvest's black and lime.

Pure apart from reading the bundled font files: data in, bytes out, no network and no clock.
The same statement always gives the same bytes, so a stored PDF can be checked against its
figures. Plus Jakarta Sans is embedded under the SIL Open Font License (fonts/OFL.txt).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app.modules.documents.domain import formatting
from app.modules.documents.domain.receipts import Receipt
from app.modules.documents.domain.statements import (
    ActivityKind,
    Statement,
    Summary,
)
from app.shared.money import Money

FONTS = Path(__file__).parent / "fonts"
FAMILY = "Jakarta"

# Brand colours from packages/tokens.
BLACK = (13, 13, 13)
INK = (20, 20, 20)
MUTED = (94, 94, 90)
LIME = (182, 235, 10)
LIME_SOFT = (233, 247, 181)
LIME_INK = (42, 56, 0)
GROUND = (244, 245, 240)
LINE = (227, 230, 221)
RULE = (238, 240, 234)
MARK = (34, 34, 34)
WHITE = (255, 255, 255)

MARGIN = 16.0
PAGE_W = 210.0
CONTENT_W = PAGE_W - 2 * MARGIN
FOOTER_SPACE = 22.0


class _Page(FPDF):
    def __init__(self, *, title: str, footer_left: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.footer_left = footer_left
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_auto_page_break(auto=False)
        self.add_font(FAMILY, "", str(FONTS / "PlusJakartaSans-Regular.ttf"))
        self.add_font(FAMILY, "B", str(FONTS / "PlusJakartaSans-Bold.ttf"))
        self.add_font(FAMILY, "I", str(FONTS / "PlusJakartaSans-ExtraBold.ttf"))  # extra bold
        self.set_title(title)
        self.set_author("Finvest")
        self.set_creator("Finvest")
        self.alias_nb_pages()

    def footer(self) -> None:
        self.set_y(-14)
        self.set_draw_color(*LINE)
        self.set_line_width(0.2)
        self.line(MARGIN, self.get_y() - 2, PAGE_W - MARGIN, self.get_y() - 2)
        self.font(7.5, colour=MUTED)
        self.cell(CONTENT_W * 0.75, 4, self.footer_left)
        self.cell(CONTENT_W * 0.25, 4, f"Page {self.page_no()} of {{nb}}", align="R")

    def font(self, size: float, *, weight: str = "", colour: tuple[int, int, int] = INK) -> None:
        self.set_font(FAMILY, weight, size)
        self.set_text_color(*colour)

    def room_left(self) -> float:
        return self.h - FOOTER_SPACE - self.get_y()


def _logo(pdf: _Page, x: float, y: float, size: float) -> None:
    """The mark: a white rising line ending in a lime dot on a dark rounded square."""
    unit = size / 64
    pdf.set_fill_color(*MARK)
    pdf.rect(x, y, size, size, style="F", round_corners=True, corner_radius=18 * unit)
    pdf.set_draw_color(*WHITE)
    pdf.set_line_width(7 * unit)
    points = [(14, 44), (25, 32), (34, 40), (43, 29)]
    pdf.polyline([(x + px * unit, y + py * unit) for px, py in points], style="D")
    for px, py in points:  # round the joins and ends
        pdf.set_fill_color(*WHITE)
        pdf.circle(x + px * unit, y + py * unit, 3.5 * unit, style="F")
    pdf.set_fill_color(*LIME)
    pdf.circle(x + 49 * unit, y + 20 * unit, 6.5 * unit, style="F")


def _band(pdf: _Page, title: str, subtitle: str) -> None:
    """The black band across the top of the first page."""
    pdf.set_fill_color(*BLACK)
    pdf.rect(0, 0, PAGE_W, 40, style="F")
    _logo(pdf, MARGIN, 12, 14)
    pdf.set_xy(MARGIN + 17, 14.5)
    pdf.font(17, weight="I", colour=WHITE)
    pdf.cell(40, 9, "finvest")
    pdf.set_xy(PAGE_W - MARGIN - 100, 12)
    pdf.font(17, weight="I", colour=WHITE)
    pdf.cell(100, 8, title, align="R")
    pdf.set_xy(PAGE_W - MARGIN - 100, 20.5)
    pdf.font(10, weight="B", colour=LIME)
    pdf.cell(100, 6, subtitle, align="R")
    pdf.set_y(50)


@dataclass(frozen=True)
class _Fact:
    label: str
    value: str


def _facts(pdf: _Page, left: Sequence[_Fact], right: Sequence[_Fact]) -> None:
    top = pdf.get_y()
    half = CONTENT_W / 2
    bottom = top
    for column, facts in ((0, left), (1, right)):
        pdf.set_y(top)
        for fact in facts:
            pdf.set_x(MARGIN + column * half)
            pdf.font(7.5, weight="B", colour=MUTED)
            pdf.cell(half, 4, fact.label.upper(), new_x=XPos.LEFT, new_y=YPos.NEXT)
            pdf.font(11, weight="B")
            pdf.cell(half, 6, fact.value, new_x=XPos.LEFT, new_y=YPos.NEXT)
            pdf.ln(2)
        bottom = max(bottom, pdf.get_y())
    pdf.set_y(bottom + 2)


def _heading(pdf: _Page, text: str) -> None:
    pdf.font(12, weight="I")
    pdf.cell(CONTENT_W, 8, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)


def _summary(pdf: _Page, statement: Statement, summary: Summary) -> None:
    """The figures that add up, in a soft box, ending with the value at the end in bold."""
    rows: list[tuple[str, Money, str]] = [
        (f"Value on {formatting.day(statement.period.start)}", summary.opening, ""),
        ("Paid in", summary.paid_in, "+"),
        ("Interest earned, after tax", summary.earned, "+"),
        ("Paid out", summary.paid_out, "-"),
    ]
    if not summary.corrections.is_zero:
        rows.append(("Corrections", summary.corrections, ""))
    row_h = 8.0
    box_h = 8 + row_h * len(rows) + 14
    top = pdf.get_y()
    pdf.set_fill_color(*GROUND)
    pdf.rect(MARGIN, top, CONTENT_W, box_h, style="F", round_corners=True, corner_radius=4)
    y = top + 4
    for label, amount, sign in rows:
        pdf.set_xy(MARGIN + 6, y)
        pdf.font(10, colour=MUTED)
        pdf.cell(CONTENT_W / 2, row_h, label)
        pdf.set_xy(MARGIN + CONTENT_W / 2, y)
        pdf.font(10, weight="B")
        shown = (
            formatting.money(amount)
            if not sign or amount.is_zero
            else sign + formatting.money(amount)
        )
        pdf.cell(CONTENT_W / 2 - 6, row_h, shown, align="R")
        y += row_h
    pdf.set_draw_color(*LINE)
    pdf.set_line_width(0.3)
    pdf.line(MARGIN + 6, y + 1.5, MARGIN + CONTENT_W - 6, y + 1.5)
    pdf.set_fill_color(*LIME)
    pdf.rect(MARGIN + 6, y + 5, 1.6, 8, style="F")
    pdf.set_xy(MARGIN + 10, y + 4)
    pdf.font(11.5, weight="I")
    pdf.cell(CONTENT_W / 2, 10, f"Value on {formatting.day(statement.period.end)}")
    pdf.set_xy(MARGIN + CONTENT_W / 2, y + 4)
    pdf.font(15, weight="I")
    pdf.cell(CONTENT_W / 2 - 6, 10, formatting.money(summary.closing), align="R")
    pdf.set_y(top + box_h + 8)


@dataclass(frozen=True)
class _Column:
    title: str
    width: float  # share of the content width
    align: str = "L"


def _table_header(pdf: _Page, columns: Sequence[_Column]) -> None:
    pdf.font(7.5, weight="B", colour=MUTED)
    for column in columns:
        pdf.cell(CONTENT_W * column.width, 7, column.title.upper(), align=column.align)
    pdf.ln(7)
    pdf.set_draw_color(*INK)
    pdf.set_line_width(0.3)
    pdf.line(MARGIN, pdf.get_y(), PAGE_W - MARGIN, pdf.get_y())


def _table(
    pdf: _Page,
    columns: Sequence[_Column],
    rows: Sequence[Sequence[tuple[str, str]]],
    *,
    continued: str,
) -> None:
    """Rows of (text, weight) cells, with a line between rows and the header on each new page.

    A cell's text may hold one line break: the second line is drawn small and muted.
    """
    _table_header(pdf, columns)
    for row in rows:
        two_lines = any("\n" in text for text, _ in row)
        height = 12.0 if two_lines else 8.0
        if pdf.room_left() < height:
            pdf.add_page()
            pdf.set_y(MARGIN)
            pdf.font(9, weight="B", colour=MUTED)
            pdf.cell(CONTENT_W, 6, continued, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            _table_header(pdf, columns)
        top = pdf.get_y()
        x = MARGIN
        for column, (text, weight) in zip(columns, row, strict=True):
            first, _, second = text.partition("\n")
            pdf.set_xy(x, top + (1.5 if two_lines else 1))
            pdf.font(9, weight=weight)
            pdf.cell(CONTENT_W * column.width, 6, first, align=column.align)
            if second:
                pdf.set_xy(x, top + 6.5)
                pdf.font(7.5, colour=MUTED)
                pdf.cell(CONTENT_W * column.width, 4, second, align=column.align)
            x += CONTENT_W * column.width
        pdf.set_y(top + height)
        pdf.set_draw_color(*RULE)
        pdf.set_line_width(0.2)
        pdf.line(MARGIN, pdf.get_y(), PAGE_W - MARGIN, pdf.get_y())
    pdf.ln(6)


def _note(pdf: _Page, text: str) -> None:
    if pdf.room_left() < 14:
        pdf.add_page()
    pdf.font(8.5, colour=MUTED)
    pdf.multi_cell(CONTENT_W, 4.5, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _not_final(pdf: _Page, first_open: date) -> None:
    """One line, in a box, saying from which day the figures may still change."""
    top = pdf.get_y()
    pdf.set_fill_color(*GROUND)
    pdf.rect(MARGIN, top, CONTENT_W, 15, style="F", round_corners=True, corner_radius=2.5)
    pdf.set_xy(MARGIN + 5, top + 3)
    pdf.font(8.5, weight="B")
    pdf.multi_cell(
        CONTENT_W - 10,
        4.5,
        f"Not final from {formatting.day(first_open)}. Entries from that day on are still "
        "being checked and may change. Ask for a new statement later for final figures.",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_y(top + 19)


def _fixed_date(pdf: _Page, on: datetime) -> None:
    # The same document always gives the same bytes.
    pdf.set_creation_date(on)


def statement_pdf(statement: Statement) -> bytes:
    pdf = _Page(
        title=f"Finvest statement, {statement.period.label}",
        footer_left=(
            f"Statement {statement.reference}  ·  "
            f"Account {formatting.account_number(statement.account_number)}"
            "  ·  finvestug.com"
        ),
    )
    _fixed_date(pdf, datetime.combine(statement.issued_on, time(), tzinfo=UTC))
    pdf.add_page()
    _band(pdf, "Statement", statement.period.label)
    _facts(
        pdf,
        [
            _Fact("Prepared for", statement.client_name),
            _Fact("Account number", formatting.account_number(statement.account_number)),
        ],
        [
            _Fact(
                "Period",
                f"{formatting.day(statement.period.start)} to "
                f"{formatting.day(statement.period.end)}",
            ),
            _Fact("Issued", formatting.day(statement.issued_on)),
        ],
    )
    if statement.not_final_from is not None:
        _not_final(pdf, statement.not_final_from)
    for summary in statement.summaries:
        if len(statement.summaries) > 1:
            _heading(pdf, f"In {summary.currency.value}")
        _summary(pdf, statement, summary)

    _heading(pdf, "Your investments")
    if statement.holdings:
        _table(
            pdf,
            [
                _Column("Investment", 0.29),
                _Column("Started", 0.13),
                _Column("Matures", 0.14),
                _Column("Invested", 0.15, "R"),
                _Column("Earned", 0.13, "R"),
                _Column("Value at end", 0.16, "R"),
            ],
            [
                [
                    (f"{holding.product}\n{holding.status} · {holding.reference}", "B"),
                    (formatting.day(holding.started_on) if holding.started_on else "-", ""),
                    (formatting.day(holding.matures_on) if holding.matures_on else "-", ""),
                    (formatting.money(holding.invested), ""),
                    (formatting.money(holding.earned_in_period), ""),
                    (formatting.money(holding.value_at_end), "B"),
                ]
                for holding in statement.holdings
            ],
            continued="Your investments, continued",
        )
    else:
        _note(pdf, "You held no investments in this period.")
        pdf.ln(4)

    if pdf.room_left() < 40:
        pdf.add_page()
    _heading(pdf, "What happened")
    if statement.activity:
        _table(
            pdf,
            [
                _Column("Date", 0.14),
                _Column("Description", 0.40),
                _Column("Reference", 0.16),
                _Column("Money in", 0.15, "R"),
                _Column("Money out", 0.15, "R"),
            ],
            [
                [
                    (formatting.day(line.on), ""),
                    (line.description, "B"),
                    (line.reference, ""),
                    (formatting.money(line.amount) if line.amount.is_positive else "", "B"),
                    (formatting.money(-line.amount) if line.amount.is_negative else "", "B"),
                ]
                for line in statement.activity
            ],
            continued="What happened, continued",
        )
    else:
        _note(pdf, "Nothing was paid in, earned or paid out in this period.")
        pdf.ln(4)

    earned_lines = any(line.kind is ActivityKind.EARNED for line in statement.activity)
    _note(
        pdf,
        "All figures are after tax. "
        + (
            "Interest is earned every day and shown here as one line for each investment "
            "and month. "
            if earned_lines
            else ""
        )
        + "This statement is issued by Finvest from its own books. To check that it is genuine, "
        f"contact Finvest and quote {statement.reference}.",
    )
    return bytes(pdf.output())


def receipt_pdf(receipt: Receipt) -> bytes:
    pdf = _Page(
        title=f"Finvest receipt {receipt.reference}",
        footer_left=f"Receipt {receipt.reference}  ·  finvestug.com",
    )
    _fixed_date(pdf, receipt.received_at)
    pdf.add_page()
    _band(pdf, "Receipt", formatting.day(receipt.received_at.date()))

    top = pdf.get_y()
    pdf.set_fill_color(*GROUND)
    pdf.rect(MARGIN, top, CONTENT_W, 40, style="F", round_corners=True, corner_radius=4)
    pdf.set_xy(MARGIN, top + 6)
    pill = "Received"
    pdf.font(8.5, weight="B", colour=LIME_INK)
    pill_w = pdf.get_string_width(pill) + 8
    pdf.set_fill_color(*LIME_SOFT)
    pdf.rect(
        PAGE_W / 2 - pill_w / 2, top + 6, pill_w, 6, style="F", round_corners=True, corner_radius=3
    )
    pdf.set_xy(PAGE_W / 2 - pill_w / 2, top + 6)
    pdf.cell(pill_w, 6, pill, align="C")
    pdf.set_xy(MARGIN, top + 15)
    pdf.font(26, weight="I")
    pdf.cell(CONTENT_W, 13, formatting.money(receipt.amount), align="C")
    pdf.set_xy(MARGIN, top + 29)
    pdf.font(10, colour=MUTED)
    pdf.cell(CONTENT_W, 6, f"Paid for {receipt.paid_for}", align="C")
    pdf.set_y(top + 48)

    facts = [
        ("Received", formatting.moment(receipt.received_at)),
        ("Received from", receipt.client_name),
        ("Account number", formatting.account_number(receipt.account_number)),
        ("Paid by", receipt.method),
    ]
    if receipt.paid_from:
        facts.append(("Paid from", receipt.paid_from))
    if receipt.provider_reference:
        facts.append((f"{receipt.method} reference", receipt.provider_reference))
    facts += [
        ("Finvest payment reference", receipt.payment_reference),
        ("Receipt number", receipt.reference),
    ]
    for label, value in facts:
        y = pdf.get_y()
        pdf.font(10, colour=MUTED)
        pdf.cell(CONTENT_W / 2, 10, label)
        pdf.font(10, weight="B")
        pdf.cell(CONTENT_W / 2, 10, value, align="R")
        pdf.set_draw_color(*RULE)
        pdf.set_line_width(0.2)
        pdf.line(MARGIN, y + 10, PAGE_W - MARGIN, y + 10)
        pdf.set_xy(MARGIN, y + 10)
    pdf.ln(8)
    _note(
        pdf,
        "Finvest received this payment and put it to work in the investment named above. "
        "This receipt is issued from Finvest's own books. To check that it is genuine, "
        f"contact Finvest and quote {receipt.reference}.",
    )
    return bytes(pdf.output())
