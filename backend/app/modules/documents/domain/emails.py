"""The emails that carry a statement or a receipt. Pure: data in, subject and bodies out.

Each email has an HTML body and a plain-text body. The HTML is tables with inline styles so
it looks the same in Gmail, Outlook and phone mail apps, and it has no images: nothing is
fetched when it is opened, so it cannot track anyone and no client blocks it.

A statement email carries no figures. The figures are in the attached PDF, so a message that
shows on a locked phone says nothing about what the client holds. A receipt shows the amount
received, because that is what a receipt is.

Every value is escaped before it goes into HTML. Links go only to Finvest's own app.
"""

import html
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.modules.documents.domain import formatting
from app.modules.documents.domain.receipts import Receipt
from app.modules.documents.domain.statements import Period, StatementKind

APP = "https://app.finvestug.com"
DOCUMENTS_LINK = f"{APP}/documents"

_BLACK = "#0D0D0D"
_INK = "#141414"
_MUTED = "#5E5E5A"
_LIME = "#B6EB0A"
_GROUND = "#F4F5F0"
_LINE = "#E3E6DD"
_FONT = "'Plus Jakarta Sans', 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


@dataclass(frozen=True, slots=True)
class Rendered:
    subject: str
    text: str
    html: str


def _e(value: str) -> str:
    return html.escape(value, quote=True)


def _paragraphs(lines: Sequence[str]) -> str:
    return "".join(
        f'<p style="margin:0 0 16px;font-size:16px;line-height:1.55;color:{_INK}">{_e(line)}</p>'
        for line in lines
    )


def _button(label: str, link: str) -> str:
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 24px">'
        f'<tr><td style="border-radius:24px;background:{_BLACK}">'
        f'<a href="{_e(link)}" style="display:inline-block;padding:14px 28px;font-family:{_FONT};'
        f'font-size:15px;font-weight:700;color:#FFFFFF;text-decoration:none;border-radius:24px">'
        f"{_e(label)}</a></td></tr></table>"
    )


def _attachment(name: str, detail: str) -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:0 0 24px;border:1px solid {_LINE};border-radius:16px">'
        '<tr><td width="64" style="padding:14px 0 14px 16px" valign="middle">'
        '<div style="width:44px;height:44px;line-height:44px;border-radius:12px;'
        f"background:{_GROUND};"
        f'text-align:center;font-size:11px;font-weight:800;color:{_BLACK}">PDF</div></td>'
        '<td style="padding:14px 16px 14px 8px" valign="middle">'
        f'<div style="font-size:15px;font-weight:700;color:{_INK}">{_e(name)}</div>'
        f'<div style="font-size:13px;color:{_MUTED};padding-top:2px">{_e(detail)}</div>'
        "</td></tr></table>"
    )


def _facts(rows: Sequence[tuple[str, str]]) -> str:
    cells = "".join(
        f'<tr><td style="padding:12px 0;border-top:1px solid {_LINE};font-size:14px;'
        f'color:{_MUTED}">'
        f"{_e(label)}</td>"
        f'<td align="right" style="padding:12px 0;border-top:1px solid {_LINE};font-size:14px;'
        f'font-weight:700;color:{_INK}">{_e(value)}</td></tr>'
        for label, value in rows
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:0 0 24px;border-bottom:1px solid {_LINE}">{cells}</table>'
    )


def _page(*, preheader: str, title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light only">'
        f"<title>{_e(title)}</title></head>"
        f'<body style="margin:0;padding:0;background:{_GROUND};font-family:{_FONT}">'
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">{_e(preheader)}</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{_GROUND}">'
        '<tr><td align="center" style="padding:24px 12px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:600px;background:#FFFFFF;border-radius:20px;overflow:hidden">'
        f'<tr><td style="background:{_BLACK};padding:22px 28px">'
        '<span style="font-size:22px;font-weight:800;color:#FFFFFF;letter-spacing:-0.3px">'
        "finvest</span>"
        f'<span style="font-size:22px;font-weight:800;color:{_LIME}">.</span></td></tr>'
        f'<tr><td style="padding:32px 28px 8px;font-family:{_FONT}">'
        f'<h1 style="margin:0 0 20px;font-size:24px;line-height:1.25;font-weight:800;color:{_INK}">'
        f"{_e(title)}</h1>{body}</td></tr>"
        f'<tr><td style="padding:20px 28px 28px;border-top:1px solid {_LINE};font-size:12px;'
        f'line-height:1.6;color:{_MUTED};font-family:{_FONT}">'
        "Finvest will never ask for your password, PIN or a code by email, phone or text. "
        "If a message asks for one, it is not from us.<br>"
        f'Finvest, Kampala, Uganda. <a href="https://finvestug.com" style="color:{_MUTED}">'
        "finvestug.com</a></td></tr>"
        "</table></td></tr></table></body></html>"
    )


_TEXT_FOOTER = (
    "Finvest will never ask for your password, PIN or a code by email, phone or text. "
    "If a message asks for one, it is not from us.\n\nFinvest, Kampala, Uganda. finvestug.com\n"
)


def statement_email(
    *,
    kind: StatementKind,
    first_name: str,
    period: Period,
    account_number: str,
    file_name: str,
    requested_at: datetime | None = None,
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
        _paragraphs(lines)
        + _attachment(file_name, detail)
        + _button("Open Documents", DOCUMENTS_LINK)
        + _paragraphs(closing)
    )
    text = "\n\n".join(
        [*lines, f"Attached: {file_name} ({detail})", f"Open Documents: {DOCUMENTS_LINK}", *closing]
    )
    return Rendered(
        subject,
        text + "\n\n" + _TEXT_FOOTER,
        _page(preheader=f"Your statement is attached as a PDF. {detail}.", title=title, body=body),
    )


def receipt_email(*, first_name: str, receipt: Receipt, file_name: str) -> Rendered:
    """The email sent for every payment Finvest receives, with the receipt PDF attached."""
    amount = formatting.money(receipt.amount)
    facts = [
        ("Paid for", receipt.paid_for),
        ("Paid by", receipt.method),
        ("Paid from", receipt.paid_from),
        ("Received", formatting.moment(receipt.received_at)),
        ("Receipt number", receipt.reference),
    ]
    lines = [f"Hello {first_name},", "Thank you. We received your payment."]
    hero = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:0 0 20px;background:{_GROUND};border-radius:16px">'
        '<tr><td align="center" style="padding:24px 16px">'
        f'<div style="display:inline-block;font-size:12px;font-weight:700;background:#E9F7B5;'
        f'color:#2A3800;border-radius:999px;padding:4px 10px">Received</div>'
        f'<div style="font-size:34px;font-weight:800;letter-spacing:-0.8px;color:{_INK};'
        f'padding-top:10px">{_e(amount)}</div>'
        f'<div style="font-size:14px;color:{_MUTED};padding-top:4px">'
        f"Paid for {_e(receipt.paid_for)}</div></td></tr></table>"
    )
    closing = [
        "Your receipt is attached as a PDF, and you can find this payment under Activity in "
        "the app. If anything here looks wrong, tell us through Help in the app.",
    ]
    body = (
        _paragraphs(lines)
        + hero
        + _facts(facts)
        + _attachment(file_name, f"Account {formatting.account_number(receipt.account_number)}")
        + _paragraphs(closing)
    )
    text = "\n\n".join(
        [
            *lines,
            f"Amount: {amount}",
            *(f"{label}: {value}" for label, value in facts),
            f"Attached: {file_name}",
            *closing,
        ]
    )
    return Rendered(
        f"Receipt: we received {amount}",
        text + "\n\n" + _TEXT_FOOTER,
        _page(
            preheader=f"{amount} received for {receipt.paid_for}. Your receipt is attached.",
            title="Payment received",
            body=body,
        ),
    )
