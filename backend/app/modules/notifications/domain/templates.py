"""The words of every email and text message Finvest sends. Pure text in, text out.

The templates here carry a code or a set-up link and are fixed in code. `client_notice` is the
one exception: it is the envelope for a notification whose words come from `wording.py`, which
staff edit with a second person's approval. It carries no code and no link of its own.

Each template lists the variables it needs. Every value is escaped before it goes into HTML,
so nothing a person typed can add markup or a link.

This file owns only the messages that carry a secret or guard an account: sign-in, signup,
reset and claim codes, the notices of a new sign-in and of a changed number, the link that
proves an email address, and the staff set-up link. They are fixed in code so that nobody can
edit them into a phishing message, and so that they work before anything else is set up. Every
other client notice is worded in `domain/wording.py`, which staff edit with a second person
approving. Neither file adds text of the other kind.
"""

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, get_args

Template = Literal[
    "signup_code",
    "sign_in_code",
    "password_reset_code",
    "claim_code",
    "payout_account_code",
    "phone_changed",
    "email_verification",
    "new_sign_in",
    "staff_enrolment",
    "client_notice",
]
TEMPLATES: tuple[Template, ...] = get_args(Template)

_VARIABLES: dict[Template, frozenset[str]] = {
    "signup_code": frozenset({"code", "minutes"}),
    "sign_in_code": frozenset({"code", "minutes"}),
    "password_reset_code": frozenset({"code", "minutes"}),
    "claim_code": frozenset({"code", "minutes"}),
    "payout_account_code": frozenset({"code", "minutes"}),
    "phone_changed": frozenset({"ending"}),
    "email_verification": frozenset({"link", "hours"}),
    "new_sign_in": frozenset({"device", "when"}),
    "staff_enrolment": frozenset({"name", "link", "hours"}),
    # A notification's words, already filled in: the title and body for an email.
    "client_notice": frozenset({"title", "body"}),
}
# Templates whose text message needs other variables than their email.
_SMS_VARIABLES: dict[Template, frozenset[str]] = {
    "client_notice": frozenset({"sms"}),
}
MAX_VALUE = 2000


class InvalidTemplate(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Rendered:
    subject: str
    text: str
    html: str


def check(template: str, variables: Mapping[str, object], *, sms: bool = False) -> dict[str, str]:
    """The variables as text, or `InvalidTemplate` if the template or a variable is wrong."""
    if template not in TEMPLATES:
        raise InvalidTemplate("Unknown email template")
    wanted = _SMS_VARIABLES.get(template, _VARIABLES[template]) if sms else _VARIABLES[template]
    if set(variables) != wanted:
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
    "claim_code": "Your code to claim your Finvest account is {code}. It works for {minutes} "
    "minutes. Never share it. Finvest staff will never ask for it.",
    # The SMS words of the notification, which already start with "Finvest:".
    "client_notice": "{sms}",
    "payout_account_code": "Your Finvest code to add a new account for your withdrawals is "
    "{code}. It works for {minutes} minutes. Never share it. If this was not you, call us.",
    "new_sign_in": "Your Finvest account was signed in to on {device}, {when}. If this was "
    "not you, change your password now.",
    "phone_changed": "The mobile number on your Finvest account was changed to one ending "
    "{ending}. If you did not ask for this, contact Finvest support now.",
}


def render_sms(template: str, variables: Mapping[str, object]) -> str:
    """The text of an SMS. Short enough for two message parts, and plain GSM characters."""
    values = check(template, variables, sms=True)
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
    if template in ("signup_code", "claim_code", "payout_account_code"):
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
    if template == "client_notice":
        return _message(
            values["title"],
            [
                values["body"],
                "Open the Finvest app to see it. You can choose how we reach you in Profile, "
                "under Notifications.",
            ],
        )
    if template == "new_sign_in":
        return _message(
            "A new sign-in to your Finvest account",
            [
                f"Your Finvest account was signed in to on {values['device']}, {values['when']}.",
                "If this was you, there is nothing to do. If it was not, change your password "
                "now and sign out the devices you do not know from the Security screen.",
            ],
        )
    if template == "email_verification":
        return _message(
            "Confirm your email address for Finvest",
            [
                "Open this link to confirm that this email address is yours. Finvest sends "
                "your statements and receipts only to an address you have confirmed:",
                values["link"],
                f"The link works once, for {values['hours']} hours. If you did not ask for "
                "this, you can ignore this email.",
            ],
            link=values["link"],
        )
    if template == "phone_changed":
        return _message(
            "Your Finvest mobile number was changed",
            [
                f"The mobile number on your Finvest account was changed to one ending "
                f"{values['ending']}.",
                "For your safety you were signed out everywhere, and for the next 24 hours "
                "money can be paid out only to accounts you already use.",
                "If you did not ask for this, contact Finvest support now.",
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
