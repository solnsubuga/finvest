"""The words of every email and text message Finvest sends. Pure text in, text out.

Each template lists the variables it needs. Every value is escaped before it goes into HTML,
so nothing a person typed can add markup or a link.

This file owns only the messages that carry a secret or open an account: sign-in, signup and
reset codes and the staff set-up link. They are fixed in code so that nobody can edit them into
a phishing message, and so that they work before anything else is set up. Every other client
notice is worded in `domain/wording.py`, which staff edit with a second person approving.
Neither file adds text of the other kind.
"""

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, get_args

Template = Literal["signup_code", "sign_in_code", "password_reset_code", "staff_enrolment"]
TEMPLATES: tuple[Template, ...] = get_args(Template)

_VARIABLES: dict[Template, frozenset[str]] = {
    "signup_code": frozenset({"code", "minutes"}),
    "sign_in_code": frozenset({"code", "minutes"}),
    "password_reset_code": frozenset({"code", "minutes"}),
    "staff_enrolment": frozenset({"name", "link", "hours"}),
}
MAX_VALUE = 2000


class InvalidTemplate(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Rendered:
    subject: str
    text: str
    html: str


def check(template: str, variables: Mapping[str, object]) -> dict[str, str]:
    """The variables as text, or `InvalidTemplate` if the template or a variable is wrong."""
    if template not in TEMPLATES:
        raise InvalidTemplate("Unknown email template")
    if set(variables) != _VARIABLES[template]:
        raise InvalidTemplate("The email template was given the wrong variables")
    values = {name: str(value) for name, value in variables.items()}
    if any(not value or len(value) > MAX_VALUE for value in values.values()):
        raise InvalidTemplate("An email variable is empty or too long")
    return values


_SMS: dict[str, str] = {
    "signup_code": "Your Finvest code is {code}. It works for {minutes} minutes. "
    "Never share it. Finvest staff will never ask for it.",
    "sign_in_code": "Your Finvest sign-in code is {code}. It works for {minutes} minutes. "
    "Never share it. If this was not you, change your password.",
    "password_reset_code": "Your Finvest password reset code is {code}. It works for "
    "{minutes} minutes. Never share it.",
}


def render_sms(template: str, variables: Mapping[str, object]) -> str:
    """The text of an SMS. Short enough for two message parts, and plain GSM characters."""
    values = check(template, variables)
    if template not in _SMS:
        raise InvalidTemplate("This template is not sent by SMS")
    return _SMS[template].format(**values)


def render(
    template: str, variables: Mapping[str, object], *, link_origins: Sequence[str] | None = None
) -> Rendered:
    """The subject and bodies of an email.

    When `link_origins` is given, a link in the message must point into one of those sites.
    The worker always gives it, so a bug or a misused database role cannot send a Finvest
    email that links anywhere else.
    """
    values = check(template, variables)
    link = values.get("link")
    if (
        link is not None
        and link_origins is not None
        and not any(link.startswith(f"{origin}/") for origin in link_origins)
    ):
        raise InvalidTemplate("The link does not point to a Finvest site")
    if template == "signup_code":
        raise InvalidTemplate("This template is not sent by email")
    if template == "sign_in_code":
        return _message(
            "Your Finvest sign-in code",
            [
                f"Your Finvest sign-in code is {values['code']}.",
                f"It works once, for {values['minutes']} minutes.",
                "If you did not try to sign in, change your password. Finvest staff will never "
                "ask you for this code.",
            ],
        )
    if template == "password_reset_code":
        return _message(
            "Reset your Finvest password",
            [
                f"Your code to reset your Finvest password is {values['code']}.",
                f"It works once, for {values['minutes']} minutes.",
                "If you did not ask for this, you can ignore this email. Finvest staff will "
                "never ask you for this code.",
            ],
        )
    return _message(
        "Set up your Finvest staff account",
        [
            f"Hello {values['name']},",
            "You have been added to the Finvest back office. Open this link to choose a "
            "password and add a passkey or an authenticator app:",
            values["link"],
            f"The link works once, for {values['hours']} hours. Do not forward it.",
        ],
        link=values["link"],
    )


def _message(subject: str, paragraphs: list[str], *, link: str | None = None) -> Rendered:
    parts = []
    for paragraph in paragraphs:
        escaped = html.escape(paragraph)
        if link is not None and paragraph == link:
            escaped = f'<a href="{html.escape(link, quote=True)}">{escaped}</a>'
        parts.append(f"<p>{escaped}</p>")
    body = (
        '<!doctype html><html lang="en"><body style="font-family:sans-serif;font-size:16px;'
        f'line-height:1.5;color:#111">{"".join(parts)}<p>Finvest</p></body></html>'
    )
    return Rendered(subject, "\n\n".join([*paragraphs, "Finvest"]) + "\n", body)
