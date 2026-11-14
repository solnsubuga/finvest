"""The words of every email and text message Finvest sends. Pure text in, text out.

The templates here carry a code or a set-up link and are fixed in code. `client_notice` is the
one exception: it is the envelope for a notification whose words come from `wording.py`, which
staff edit with a second person's approval. It carries no code and no link of its own.

Each template lists the variables it needs. Every value is escaped before it goes into HTML,
so nothing a person typed can add markup or a link. Every email is drawn with the parts in
`app.shared.email_layout`, the look the statement and receipt emails share.

This file owns only the messages that carry a secret or guard an account: sign-in, signup,
reset and claim codes, the notices of a new sign-in, of a request for help getting in and of a
changed number, the link that proves an email address, the link that invites a Finvest 1.0
client to claim their account, and the staff set-up link. They are fixed in code so that nobody
can edit them into a phishing message, and so that they work before anything else is set up. Every
other client notice is worded in `domain/wording.py`, which staff edit with a second person
approving. Neither file adds text of the other kind.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from app.shared import email_layout as layout

Template = Literal[
    "signup_code",
    "sign_in_code",
    "password_reset_code",
    "claim_code",
    "payout_account_code",
    "phone_changed",
    "email_verification",
    "new_sign_in",
    "recovery_requested",
    "staff_enrolment",
    "claim_invitation",
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
    # Someone said they cannot get in and asked us to call them back.
    "recovery_requested": frozenset({"when"}),
    "staff_enrolment": frozenset({"name", "link", "hours"}),
    # The one-time link a client carried over from Finvest 1.0 claims their account with.
    "claim_invitation": frozenset({"link", "hours"}),
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
    "recovery_requested": "Someone asked Finvest for help getting into your account, {when}. "
    "If this was not you, you need do nothing: we never change your number without checking "
    "it is you.",
    "phone_changed": "The mobile number on your Finvest account was changed to one ending "
    "{ending}. If you did not ask for this, contact Finvest support now.",
    "claim_invitation": "Finvest has a new app, and your investments are in it. Claim your "
    "account with this link: {link} It works once, for {hours} hours. Finvest staff will never "
    "ask for this link.",
}


def render_sms(
    template: str, variables: Mapping[str, object], *, link_origins: Sequence[str] | None = None
) -> str:
    """The text of an SMS. Short enough for two message parts, and plain GSM characters.

    `link_origins` holds a link in the message to Finvest's own sites, as for an email.
    """
    values = check(template, variables, sms=True)
    if template not in _SMS:
        raise InvalidTemplate("This template is not sent by SMS")
    _check_link(values.get("link"), link_origins)
    return _SMS[template].format(**values)


def _check_link(link: str | None, link_origins: Sequence[str] | None) -> None:
    if (
        link is not None
        and link_origins is not None
        and not any(link.startswith(f"{origin}/") for origin in link_origins)
    ):
        raise InvalidTemplate("The link does not point to a Finvest site")


def render(
    template: str,
    variables: Mapping[str, object],
    *,
    link_origins: Sequence[str] | None = None,
    links: layout.Links = layout.PRODUCTION,
) -> Rendered:
    """The subject and bodies of an email, in the designed look every Finvest email shares.

    When `link_origins` is given, a link in the message must point into one of those sites.
    The worker always gives it, so a bug or a misused database role cannot send a Finvest
    email that links anywhere else. `links` are the app and website the email's own buttons
    and footer open; the worker gives the ones its settings name.
    """
    values = check(template, variables)
    _check_link(values.get("link"), link_origins)
    if template in ("signup_code", "claim_code", "payout_account_code"):
        raise InvalidTemplate("This template is not sent by email")
    return _EMAILS[template](values, links)


# Each email below is built twice from the same words: as designed HTML from the parts in
# `app.shared.email_layout`, and as plain text for a mail app that shows no HTML.


def _sign_in_code(values: dict[str, str], links: layout.Links) -> Rendered:
    minutes = f"Works once, for {values['minutes']} minutes"
    lines = ["Use this code to finish signing in to Finvest."]
    warning = (
        "Did not try to sign in?",
        "Someone may know your password. Change it in the app now. Finvest staff will never "
        "ask you for this code.",
    )
    return _email(
        links=links,
        subject="Your Finvest sign-in code",
        preheader=f"Your code works once, for {values['minutes']} minutes.",
        title="Your sign-in code",
        html=layout.paragraphs(lines)
        + layout.code(values["code"], minutes)
        + layout.callout(*warning),
        text=[*lines, f"Your code: {values['code']}", f"{minutes}.", " ".join(warning)],
    )


def _password_reset_code(values: dict[str, str], links: layout.Links) -> Rendered:
    minutes = f"Works once, for {values['minutes']} minutes"
    lines = ["Use this code in the app to choose a new password."]
    warning = (
        "Did not ask for this?",
        "You can ignore this email and your password stays as it is. Finvest staff will never "
        "ask you for this code.",
    )
    return _email(
        links=links,
        subject="Reset your Finvest password",
        preheader=f"Your code works once, for {values['minutes']} minutes.",
        title="Reset your password",
        html=layout.paragraphs(lines)
        + layout.code(values["code"], minutes)
        + layout.callout(*warning),
        text=[*lines, f"Your code: {values['code']}", f"{minutes}.", " ".join(warning)],
    )


def _new_sign_in(values: dict[str, str], links: layout.Links) -> Rendered:
    lines = ["Your account was just signed in on a new device."]
    rows = [("Device", values["device"]), ("When", values["when"])]
    after = ["If this was you, you do not need to do anything."]
    warning = (
        "Not you?",
        "Change your password now, then sign out every device you do not know under Sign in "
        "and security. Tell us through Help and we will check your account.",
    )
    return _email(
        links=links,
        subject="A new sign-in to your Finvest account",
        preheader=f"{values['device']}, {values['when']}.",
        title="New sign-in to your account",
        html=layout.paragraphs(lines)
        + layout.facts(rows)
        + layout.paragraphs(after)
        + layout.callout(*warning)
        + layout.button("Review your devices", links.in_app(SECURITY_PATH)),
        text=[
            *lines,
            *(f"{label}: {value}" for label, value in rows),
            *after,
            " ".join(warning),
            f"Review your devices: {links.in_app(SECURITY_PATH)}",
        ],
    )


def _recovery_requested(values: dict[str, str], links: layout.Links) -> Rendered:
    lines = [
        f"Someone asked Finvest for help getting into your account, {values['when']}.",
        "We will call them back and check who they are against your ID before we change anything.",
    ]
    note = (
        "Not you?",
        "You need do nothing. Your number, password and money stay as they are, and nothing "
        "changes without that check. Finvest staff will never ask you for a code or your PIN.",
    )
    return _email(
        links=links,
        subject="Someone asked for help getting into your Finvest account",
        preheader=f"Asked {values['when']}. Nothing changes without a check.",
        title="Someone asked for help getting in",
        html=layout.paragraphs(lines) + layout.callout(*note),
        text=[*lines, " ".join(note)],
    )


def _phone_changed(values: dict[str, str], links: layout.Links) -> Rendered:
    lines = [
        f"The mobile number on your Finvest account was changed to one ending {values['ending']}.",
        "For your safety you were signed out everywhere, and for the next 24 hours money can "
        "be paid out only to accounts you already use.",
    ]
    warning = ("Not you?", "Contact Finvest support now, through Help in the app.")
    return _email(
        links=links,
        subject="Your Finvest mobile number was changed",
        preheader=f"It now ends {values['ending']}.",
        title="Your mobile number was changed",
        html=layout.status("Number changed", f"Now ending {values['ending']}")
        + layout.paragraphs(lines)
        + layout.callout(*warning),
        text=[*lines, " ".join(warning)],
    )


def _email_verification(values: dict[str, str], links: layout.Links) -> Rendered:
    link = values["link"]
    lines = [
        "Confirm that this email address is yours. Finvest sends your statements and receipts "
        "only to an address you have confirmed.",
    ]
    after = [
        f"The link works once, for {values['hours']} hours. If you did not ask for this, you "
        "can ignore this email.",
    ]
    return _email(
        links=links,
        subject="Confirm your email address for Finvest",
        preheader=f"The link works once, for {values['hours']} hours.",
        title="Confirm your email address",
        html=layout.paragraphs(lines)
        + layout.button("Confirm my email", link)
        + layout.paragraphs(after)
        + layout.link_fallback(link),
        text=[*lines, link, *after],
    )


def _claim_invitation(values: dict[str, str], links: layout.Links) -> Rendered:
    link = values["link"]
    lines = [
        "Finvest has moved to a new app, and your account and investments are already in it. "
        "Open the link to claim your account.",
    ]
    after = [
        f"The link works once, for {values['hours']} hours. You will confirm your mobile "
        "number with a code we text you and choose a password. Finvest staff will never ask "
        "you for that code.",
        "If you were not a Finvest client, you can ignore this email.",
    ]
    return _email(
        links=links,
        subject="Claim your account on the new Finvest",
        preheader="Your account and investments are already in the new app.",
        title="Claim your account",
        html=layout.paragraphs(lines)
        + layout.button("Claim my account", link)
        + layout.paragraphs(after)
        + layout.link_fallback(link),
        text=[*lines, link, *after],
    )


def _staff_enrolment(values: dict[str, str], links: layout.Links) -> Rendered:
    link = values["link"]
    lines = [
        f"Hello {values['name']},",
        "You have been added to the Finvest back office. Open the link to choose a password "
        "and add a passkey or an authenticator app.",
    ]
    after = [
        f"The link works once, for {values['hours']} hours. Do not forward it. If you were not "
        "expecting this, tell your Finvest administrator.",
    ]
    return _email(
        links=links,
        subject="Set up your Finvest staff account",
        preheader=f"Your link works once, for {values['hours']} hours.",
        title="Set up your staff account",
        html=layout.paragraphs(lines)
        + layout.button("Set up my account", link)
        + layout.paragraphs(after)
        + layout.link_fallback(link),
        text=[*lines, link, *after],
    )


def _client_notice(values: dict[str, str], links: layout.Links) -> Rendered:
    """A notification by email: its title and body, and a way into the app to see the rest.

    The words carry no amounts, so the button opens the app, where the client sees them.
    """
    settings = (
        "You get this email because email is on for this kind of alert. You can choose how we "
        "reach you in the app, in Profile, under Notifications."
    )
    return _email(
        links=links,
        subject=values["title"],
        preheader=values["body"],
        title=values["title"],
        html=layout.paragraphs([values["body"]])
        + layout.button("Open Finvest", links.app)
        + layout.small(settings),
        text=[values["body"], f"Open Finvest: {links.app}", settings],
    )


SECURITY_PATH = "/security"

_EMAILS: dict[str, Callable[[dict[str, str], layout.Links], Rendered]] = {
    "sign_in_code": _sign_in_code,
    "password_reset_code": _password_reset_code,
    "new_sign_in": _new_sign_in,
    "recovery_requested": _recovery_requested,
    "phone_changed": _phone_changed,
    "email_verification": _email_verification,
    "claim_invitation": _claim_invitation,
    "staff_enrolment": _staff_enrolment,
    "client_notice": _client_notice,
}


def _email(
    *, links: layout.Links, subject: str, preheader: str, title: str, html: str, text: list[str]
) -> Rendered:
    return Rendered(
        subject,
        "\n\n".join([title, *text]) + "\n\n" + layout.FOOTER_TEXT,
        layout.page(preheader=preheader, title=title, body=html, links=links),
    )
