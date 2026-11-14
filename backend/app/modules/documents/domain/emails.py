"""The emails that carry a statement, a receipt or a tax certificate. Pure: data in, subject
and bodies out.

Each email has an HTML body and a plain-text body. The HTML is built from the parts in
`app.shared.email_layout`, the look every Finvest email shares.

A statement or tax certificate email carries no figures. The figures are in the attached PDF,
so a message that shows on a locked phone says nothing about what the client holds. A receipt
shows the amount received, because that is what a receipt is.

The attached PDF is locked with the client's date of birth, so whoever else reads the mailbox
cannot open it. Each email says how to open it. The copy in the app, behind sign-in, is not
locked.

Every value is escaped before it goes into HTML. Links go only to Finvest's own app.
"""

from dataclasses import dataclass
from datetime import datetime

from app.modules.documents.domain import formatting
from app.modules.documents.domain.investment_notes import EndedNote, Ending, InvestmentNote
from app.modules.documents.domain.receipts import Receipt
from app.modules.documents.domain.statements import Period, StatementKind
from app.shared import email_layout as layout

DOCUMENTS_PATH = "/documents"
LOCKED = "Password protected"
# Said the same way in every email, so a client learns it once.
HOW_TO_OPEN = (
    "To open the PDF, type your date of birth as eight digits: day, month, then year. For "
    "5 March 1990, that is 05031990. In the app, under Documents, it opens without a password."
)


@dataclass(frozen=True, slots=True)
class Rendered:
    subject: str
    text: str
    html: str


def statement_email(
    *,
    kind: StatementKind,
    first_name: str,
    period: Period,
    account_number: str,
    file_name: str,
    requested_at: datetime | None = None,
    links: layout.Links = layout.PRODUCTION,
) -> Rendered:
    """The email a statement PDF is attached to: the monthly one or one the client asked for."""
    account = formatting.account_number(account_number)
    if kind is StatementKind.MONTHLY:
        subject = f"Your Finvest statement for {period.label}"
        title = f"Your {period.label} statement"
        lines = [
            f"Hello {first_name},",
            f"Your statement for {period.label} is attached. It shows what you held at the start "
            "and end of the month, what you paid in, the interest you earned and what was paid "
            "out to you, for every investment under your account.",
        ]
        closing = [
            "We send a statement after every month in which you held an investment. You can "
            "also make one for any dates you choose in the app, under Documents.",
        ]
    else:
        if requested_at is None:
            raise ValueError("A requested statement says when it was asked for")
        subject = f"Your Finvest statement, {period.label}"
        title = "Here is the statement you asked for"
        lines = [
            f"Hello {first_name},",
            f"The statement for {formatting.day(period.start)} to {formatting.day(period.end)} "
            f"that you asked for in the app on {formatting.moment(requested_at)} is attached. "
            "You can also download it from Documents in the app.",
        ]
        closing = [
            "If you did not ask for this statement, change your password in the app and tell "
            "us through Help straight away.",
        ]
    detail = f"Account {account}"
    body = (
        layout.paragraphs(lines)
        + layout.attachment(file_name, f"{detail} · {LOCKED}")
        + layout.paragraphs([HOW_TO_OPEN])
        + layout.button("Open Documents", links.in_app(DOCUMENTS_PATH))
        + layout.paragraphs(closing)
    )
    text = "\n\n".join(
        [
            *lines,
            f"Attached: {file_name} ({detail}, {LOCKED.lower()})",
            HOW_TO_OPEN,
            f"Open Documents: {links.in_app(DOCUMENTS_PATH)}",
            *closing,
        ]
    )
    return Rendered(
        subject,
        text + "\n\n" + layout.FOOTER_TEXT,
        layout.page(
            preheader=f"Your statement is attached as a PDF. {detail}.",
            title=title,
            body=body,
            links=links,
        ),
    )


def receipt_email(
    *, first_name: str, receipt: Receipt, file_name: str, links: layout.Links = layout.PRODUCTION
) -> Rendered:
    """The email sent for every payment Finvest receives, with the receipt PDF attached."""
    amount = formatting.money(receipt.amount)
    facts = [
        ("Paid for", receipt.paid_for),
        ("Paid by", receipt.method),
        *([("Paid from", receipt.paid_from)] if receipt.paid_from else []),
        ("Received", formatting.moment(receipt.received_at)),
        ("Receipt number", receipt.reference),
    ]
    lines = [f"Hello {first_name},", "Thank you. We received your payment."]
    hero = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:0 0 20px;background:{layout.GROUND};border-radius:16px">'
        '<tr><td align="center" style="padding:24px 16px">'
        f'<div style="display:inline-block;font-size:12px;font-weight:700;background:{layout.PILL};'
        f'color:{layout.PILL_INK};border-radius:999px;padding:4px 10px">Received</div>'
        f'<div style="font-size:34px;font-weight:800;letter-spacing:-0.8px;color:{layout.INK};'
        f'padding-top:10px">{layout.escape(amount)}</div>'
        f'<div style="font-size:14px;color:{layout.MUTED};padding-top:4px">'
        f"Paid for {layout.escape(receipt.paid_for)}</div></td></tr></table>"
    )
    closing = [
        "Your receipt is attached as a PDF, and you can find this payment under Activity in "
        "the app. If anything here looks wrong, tell us through Help in the app.",
    ]
    body = (
        layout.paragraphs(lines)
        + hero
        + layout.facts(facts)
        + layout.attachment(
            file_name, f"Account {formatting.account_number(receipt.account_number)} · {LOCKED}"
        )
        + layout.paragraphs([HOW_TO_OPEN, *closing])
    )
    text = "\n\n".join(
        [
            *lines,
            f"Amount: {amount}",
            *(f"{label}: {value}" for label, value in facts),
            f"Attached: {file_name} ({LOCKED.lower()})",
            HOW_TO_OPEN,
            *closing,
        ]
    )
    return Rendered(
        f"Receipt: we received {amount}",
        text + "\n\n" + layout.FOOTER_TEXT,
        layout.page(
            preheader=f"{amount} received for {receipt.paid_for}. Your receipt is attached.",
            title="Payment received",
            body=body,
            links=links,
        ),
    )


def tax_certificate_email(
    *,
    first_name: str,
    tax_year: str,
    period: Period,
    account_number: str,
    file_name: str,
    links: layout.Links = layout.PRODUCTION,
) -> Rendered:
    """The email the yearly withholding tax certificate is attached to. No figures, no rate."""
    detail = f"Account {formatting.account_number(account_number)}"
    lines = [
        f"Hello {first_name},",
        f"Your withholding tax certificate for the {tax_year} tax year, "
        f"{formatting.day(period.start)} to {formatting.day(period.end)}, is attached. It shows "
        "the interest your investments earned in the year, the tax Finvest withheld from it "
        "and what you earned after tax.",
    ]
    closing = [
        "Keep it with your tax records. You can download it again at any time from Documents "
        "in the app.",
    ]
    body = (
        layout.paragraphs(lines)
        + layout.attachment(file_name, f"{detail} · {LOCKED}")
        + layout.paragraphs([HOW_TO_OPEN])
        + layout.button("Open Documents", links.in_app(DOCUMENTS_PATH))
        + layout.paragraphs(closing)
    )
    text = "\n\n".join(
        [
            *lines,
            f"Attached: {file_name} ({detail}, {LOCKED.lower()})",
            HOW_TO_OPEN,
            f"Open Documents: {links.in_app(DOCUMENTS_PATH)}",
            *closing,
        ]
    )
    return Rendered(
        f"Your Finvest tax certificate for {tax_year}",
        text + "\n\n" + layout.FOOTER_TEXT,
        layout.page(
            preheader=f"Your withholding tax certificate is attached as a PDF. {detail}.",
            title=f"Your {tax_year} tax certificate",
            body=body,
            links=links,
        ),
    )


def investment_note_email(
    *,
    first_name: str,
    note: InvestmentNote,
    file_name: str,
    links: layout.Links = layout.PRODUCTION,
) -> Rendered:
    """The email the note confirming an investment started is attached to. Like a statement
    email it carries no figures: they are in the PDF."""
    detail = f"Account {formatting.account_number(note.account_number)}"
    started = f"Your {note.product_name} started on {formatting.day(note.start_date)}"
    if note.maturity_date is not None:
        when = f"{started} and matures on {formatting.day(note.maturity_date)}."
    else:
        when = f"{started} and earns interest every day until you withdraw it."
    lines = [
        f"Hello {first_name},",
        f"{when} The attached note confirms what you invested, the yearly rate after tax and "
        "what you get back.",
    ]
    closing = [
        "You can follow how it is doing in the app, and find this note again under Documents.",
    ]
    body = (
        layout.paragraphs(lines)
        + layout.attachment(file_name, f"{detail} · {LOCKED}")
        + layout.paragraphs([HOW_TO_OPEN])
        + layout.button("Open Documents", links.in_app(DOCUMENTS_PATH))
        + layout.paragraphs(closing)
    )
    text = "\n\n".join(
        [
            *lines,
            f"Attached: {file_name} ({detail}, {LOCKED.lower()})",
            HOW_TO_OPEN,
            f"Open Documents: {links.in_app(DOCUMENTS_PATH)}",
            *closing,
        ]
    )
    return Rendered(
        f"Your Finvest investment note: {note.product_name}",
        text + "\n\n" + layout.FOOTER_TEXT,
        layout.page(
            preheader=f"Your {note.product_name} has started. The note is attached as a PDF.",
            title="Your investment has started",
            body=body,
            links=links,
        ),
    )


def ended_note_email(
    *,
    first_name: str,
    note: EndedNote,
    file_name: str,
    links: layout.Links = layout.PRODUCTION,
) -> Rendered:
    """The email the note confirming an investment ended is attached to. No figures."""
    detail = f"Account {formatting.account_number(note.account_number)}"
    day = formatting.day(note.ended_on)
    if note.ending is Ending.WITHDRAWN:
        happened = f"Your {note.product_name} was withdrawn early on {day}, as you asked."
        title = "Your investment was withdrawn"
    else:
        happened = f"Your {note.product_name} matured on {day}."
        title = "Your investment has matured"
    where = {
        Ending.PAID_OUT: "The money is on its way to the account you chose.",
        Ending.ROLLED_OVER: "It was rolled over into a new investment, as you chose.",
        Ending.WAITING: "Choose in the app where we should send the money.",
        Ending.WITHDRAWN: "The money is paid to the account you chose once it is sent.",
    }[note.ending]
    lines = [
        f"Hello {first_name},",
        f"{happened} {where} The attached note confirms what it earned after tax and what it "
        "came to.",
    ]
    closing = ["You can find this note again under Documents in the app."]
    body = (
        layout.paragraphs(lines)
        + layout.attachment(file_name, f"{detail} · {LOCKED}")
        + layout.paragraphs([HOW_TO_OPEN])
        + layout.button("Open Documents", links.in_app(DOCUMENTS_PATH))
        + layout.paragraphs(closing)
    )
    text = "\n\n".join(
        [
            *lines,
            f"Attached: {file_name} ({detail}, {LOCKED.lower()})",
            HOW_TO_OPEN,
            f"Open Documents: {links.in_app(DOCUMENTS_PATH)}",
            *closing,
        ]
    )
    return Rendered(
        f"Your Finvest investment note: {note.product_name} ended",
        text + "\n\n" + layout.FOOTER_TEXT,
        layout.page(
            preheader=f"{happened} The note is attached as a PDF.",
            title=title,
            body=body,
            links=links,
        ),
    )
