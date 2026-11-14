"""The designed look of every Finvest email: a black band with the wordmark, a white card, and
the safety footer. Pure: text in, HTML out.

Every email Finvest sends is built from these parts, so a sign-in code, a withdrawal notice and
a monthly statement look like they come from the same company. The design is section 9,
"Emails", of the Finvest 2.0 design canvas.

The HTML is tables with inline styles so it looks the same in Gmail, Outlook and phone mail
apps, and it has no images: nothing is fetched when it is opened, so it cannot track anyone
and no client blocks it.

Every part escapes what it is given. A caller passes plain text, never markup, except the
`body` of `page`, which is made of these parts.
"""

import html
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Links:
    """Where the links in an email point: the client app and the public website.

    The worker builds these from its settings (`Settings.email_links`), so an email sent by a
    staging worker opens staging, not production. `PRODUCTION` is what they are when nothing
    says otherwise.
    """

    app: str
    site: str

    def in_app(self, path: str) -> str:
        """A link to a page of the client app, such as `/security`."""
        return f"{self.app}{path}"


PRODUCTION = Links(app="https://app.finvestug.com", site="https://finvestug.com")

BLACK = "#0D0D0D"
INK = "#141414"
MUTED = "#5E5E5A"
LIME = "#B6EB0A"
GROUND = "#F4F5F0"
LINE = "#E3E6DD"
PILL = "#E9F7B5"
PILL_INK = "#2A3800"
FONT = "'Plus Jakarta Sans', 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

FOOTER_TEXT = (
    "Finvest will never ask for your password, PIN or a code by email, phone or text. "
    "If a message asks for one, it is not from us.\n\nFinvest, Kampala, Uganda. finvestug.com\n"
)


def escape(value: str) -> str:
    return html.escape(value, quote=True)


def paragraphs(lines: Sequence[str]) -> str:
    return "".join(
        f'<p style="margin:0 0 16px;font-size:16px;line-height:1.55;color:{INK}">{escape(line)}</p>'
        for line in lines
    )


def small(line: str) -> str:
    """A quieter line, for what matters less than the message itself."""
    return (
        f'<p style="margin:0 0 16px;font-size:13px;line-height:1.6;color:{MUTED}">'
        f"{escape(line)}</p>"
    )


def button(label: str, link: str) -> str:
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 24px">'
        f'<tr><td style="border-radius:24px;background:{BLACK}">'
        f'<a href="{escape(link)}" style="display:inline-block;padding:14px 28px;'
        f"font-family:{FONT};font-size:15px;font-weight:700;color:#FFFFFF;"
        f'text-decoration:none;border-radius:24px">{escape(label)}</a></td></tr></table>'
    )


def link_fallback(link: str) -> str:
    """The link written out, for a mail app that does not show the button. Not a link itself,
    so the email holds one place to click."""
    return (
        f'<p style="margin:0 0 16px;font-size:13px;line-height:1.6;color:{MUTED}">'
        "If the button does not work, copy this into your browser:<br>"
        f'<span style="color:{INK};word-break:break-all">{escape(link)}</span></p>'
    )


def code(value: str, caption: str) -> str:
    """A one-time code, large and spaced so it is easy to read and type."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:0 0 20px;background:{GROUND};border-radius:16px">'
        '<tr><td align="center" style="padding:26px 16px">'
        f'<div style="font-size:36px;font-weight:800;letter-spacing:8px;color:{INK};'
        f'font-family:{FONT}">{escape(value)}</div>'
        f'<div style="font-size:14px;color:{MUTED};padding-top:6px">{escape(caption)}</div>'
        "</td></tr></table>"
    )


def callout(heading: str, text: str) -> str:
    """The black box that says what to do if something was not the reader's doing."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:0 0 24px;background:{BLACK};border-radius:16px">'
        '<tr><td style="padding:18px 20px">'
        f'<div style="font-size:15px;font-weight:800;color:{LIME}">{escape(heading)}</div>'
        f'<div style="font-size:15px;line-height:1.55;color:#FFFFFF;padding-top:4px">'
        f"{escape(text)}</div></td></tr></table>"
    )


def status(label: str, headline: str, detail: str | None = None) -> str:
    """A grey panel with a lime pill, a large line and an optional quieter one."""
    below = (
        f'<div style="font-size:14px;color:{MUTED};padding-top:4px">{escape(detail)}</div>'
        if detail
        else ""
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:0 0 20px;background:{GROUND};border-radius:16px">'
        '<tr><td align="center" style="padding:24px 16px">'
        f'<div style="display:inline-block;font-size:12px;font-weight:700;background:{PILL};'
        f'color:{PILL_INK};border-radius:999px;padding:4px 10px">{escape(label)}</div>'
        f'<div style="font-size:26px;font-weight:800;letter-spacing:-0.5px;color:{INK};'
        f'line-height:1.25;padding-top:10px">{escape(headline)}</div>'
        f"{below}</td></tr></table>"
    )


def facts(rows: Sequence[tuple[str, str]]) -> str:
    cells = "".join(
        f'<tr><td style="padding:12px 0;border-top:1px solid {LINE};font-size:14px;'
        f'color:{MUTED}">{escape(label)}</td>'
        f'<td align="right" style="padding:12px 0;border-top:1px solid {LINE};font-size:14px;'
        f'font-weight:700;color:{INK}">{escape(value)}</td></tr>'
        for label, value in rows
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:0 0 24px;border-bottom:1px solid {LINE}">{cells}</table>'
    )


def attachment(name: str, detail: str) -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:0 0 24px;border:1px solid {LINE};border-radius:16px">'
        '<tr><td width="64" style="padding:14px 0 14px 16px" valign="middle">'
        '<div style="width:44px;height:44px;line-height:44px;border-radius:12px;'
        f"background:{GROUND};"
        f'text-align:center;font-size:11px;font-weight:800;color:{BLACK}">PDF</div></td>'
        '<td style="padding:14px 16px 14px 8px" valign="middle">'
        f'<div style="font-size:15px;font-weight:700;color:{INK}">{escape(name)}</div>'
        f'<div style="font-size:13px;color:{MUTED};padding-top:2px">{escape(detail)}</div>'
        "</td></tr></table>"
    )


def page(*, preheader: str, title: str, body: str, links: Links = PRODUCTION) -> str:
    """The whole email around `body`: band, heading, body and footer. The footer links to
    `links.site`.

    `preheader` is the line a mail app shows after the subject in the inbox. It is hidden in
    the message itself.
    """
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light only">'
        f"<title>{escape(title)}</title></head>"
        f'<body style="margin:0;padding:0;background:{GROUND};font-family:{FONT}">'
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0">'
        f"{escape(preheader)}</div>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{GROUND}">'
        '<tr><td align="center" style="padding:24px 12px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:600px;background:#FFFFFF;border-radius:20px;overflow:hidden">'
        f'<tr><td style="background:{BLACK};padding:22px 28px">'
        '<span style="font-size:22px;font-weight:800;color:#FFFFFF;letter-spacing:-0.3px">'
        "finvest</span>"
        f'<span style="font-size:22px;font-weight:800;color:{LIME}">.</span></td></tr>'
        f'<tr><td style="padding:32px 28px 8px;font-family:{FONT}">'
        f'<h1 style="margin:0 0 20px;font-size:24px;line-height:1.25;font-weight:800;'
        f'color:{INK}">{escape(title)}</h1>{body}</td></tr>'
        f'<tr><td style="padding:20px 28px 28px;border-top:1px solid {LINE};font-size:12px;'
        f'line-height:1.6;color:{MUTED};font-family:{FONT}">'
        "Finvest will never ask for your password, PIN or a code by email, phone or text. "
        "If a message asks for one, it is not from us.<br>"
        f'Finvest, Kampala, Uganda. <a href="{escape(links.site)}" style="color:{MUTED}">'
        "finvestug.com</a></td></tr>"
        "</table></td></tr></table></body></html>"
    )
