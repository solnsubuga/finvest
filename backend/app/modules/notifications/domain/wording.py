"""The words of every notification, and the rules an edited wording must pass. Pure: no I/O.

Each event has three texts: a title and a body, shown in the app, in a push notification and
in an email, and a shorter text for SMS. Staff can change them in the console. A placeholder
such as `{amount}` is replaced by a value the raising module supplies. Values are text,
whole numbers, `Money` or dates, and are formatted here so every message writes an amount or
a date the same way.

A wording never shows a tax rate, and can only link to Finvest's own site.

Who owns which words: this file owns every notice to a client (payments, payouts, investments,
identity checks, security alerts, help and so on), which staff may change with a second
person's approval. `domain/templates.py` owns only the messages that carry a code or a set-up
link (sign-in and signup codes, password reset, the staff set-up link), fixed in code so that
no staff change can turn them into a phishing message. Neither file adds text of the other kind.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from app.modules.notifications.domain.events import EVENTS, EventSpec
from app.shared.money import Currency, Money

MAX_TITLE = 60
MAX_BODY = 300
MAX_SMS = 300
# What is stored and sent, after the placeholders are filled. The columns of migration 0090
# hold these, and one SMS send accepts two message parts.
MAX_RENDERED_TITLE = 120
MAX_RENDERED_BODY = 2000
MAX_RENDERED_SMS = 320
MAX_VALUE = 240
OWN_SITE = "finvestug.com"
LINK_REMOVED = "[link removed]"

Value = str | int | Money | date

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
# Anything a phone or a mail client would turn into a link: a scheme, a `www.`, or a bare
# name ending in letters, such as `evil.example/finvest`.
_LINK = re.compile(r"(?i)(?:https?://|www\.)[^\s]+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b(?:[/?#][^\s]*)?")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_SYMBOLS = {Currency.UGX: "UGX ", Currency.USD: "$"}


@dataclass(frozen=True, slots=True)
class Wording:
    title: str
    body: str
    sms: str


@dataclass(frozen=True, slots=True)
class Rendered:
    title: str
    body: str
    sms: str


class InvalidWording(ValueError):
    """An edited wording broke a rule. `problems` pairs a field with a code."""

    def __init__(self, problems: list[tuple[str, str]]) -> None:
        super().__init__("The wording is not valid")
        self.problems = problems


class InvalidVariables(ValueError):
    """The raising module gave the wrong variables. A programming error, not a client's."""


_NEVER_SHARE = "Finvest staff will never ask for your password, PIN or codes."

DEFAULTS: dict[str, Wording] = {
    "payment_confirmed": Wording(
        "Payment received",
        "We received your payment from {method}.",
        "Finvest: we received your payment from {method}.",
    ),
    "payment_failed": Wording(
        "Payment did not go through",
        "Your payment from {method} did not go through. No money was taken. You can try again in "
        "the app.",
        "Finvest: your payment from {method} did not go through. No money was taken.",
    ),
    "payout_processing": Wording(
        "Withdrawal is being processed",
        "Your withdrawal to {destination} is being processed. We will confirm when it is sent.",
        "Finvest: your withdrawal to {destination} is being processed.",
    ),
    "payout_sent": Wording(
        "Withdrawal sent",
        "Your withdrawal has been sent to {destination}.",
        "Finvest: your withdrawal has been sent to {destination}.",
    ),
    "payout_failed": Wording(
        "Withdrawal could not be sent",
        "We could not send your withdrawal to {destination}. Your money is safe and our team is "
        "on it. You do not need to do anything.",
        "Finvest: we could not send your withdrawal to {destination}. Your money is safe and our "
        "team is on it.",
    ),
    "payout_returned": Wording(
        "Withdrawal came back",
        "Your withdrawal to {destination} came back to us. Your money is safe with us. Choose an "
        "account in the app to send it again.",
        "Finvest: your withdrawal to {destination} came back to us. Your money is safe. Open the "
        "app to send it again.",
    ),
    "payout_declined": Wording(
        "Withdrawal not sent",
        "We did not send your withdrawal to {destination}. Our team declined it after review. "
        "See the details in the app, or message us there if you have a question.",
        "Finvest: we did not send your withdrawal to {destination}. See the app for details.",
    ),
    "investment_started": Wording(
        "Investment started",
        "Your money is now in a {product}.",
        "Finvest: your money is now invested in a {product}.",
    ),
    "investment_maturing": Wording(
        "Your investment matures soon",
        "Your {product} matures on {date}. Choose to reinvest or cash out in the app.",
        "Finvest: your {product} matures on {date}. Choose to reinvest or cash out in the app.",
    ),
    "investment_matured": Wording(
        "Investment matured",
        "Your {product} has matured. See what it paid in the app.",
        "Finvest: your {product} has matured. See what it paid in the app.",
    ),
    "investment_withdrawal_requested": Wording(
        "Early withdrawal requested",
        "We have your request to cash out your {product} early. We will tell you when the "
        "money is sent.",
        "Finvest: we have your request to cash out your {product} early. We will tell you "
        "when the money is sent.",
    ),
    "investment_withdrawal_paid": Wording(
        "Early withdrawal sent",
        "The money from your {product} has been sent to you. See the amount in the app.",
        "Finvest: the money from your {product} has been sent to you. See the amount in the app.",
    ),
    "investment_sent": Wording(
        "Investment sent",
        "Your {product} went to {person}. See what you kept in the app.",
        "Finvest: your {product} went to {person}. See what you kept in the app.",
    ),
    "investment_received": Wording(
        "You were sent an investment",
        "{person} sent you an investment in the {product}. It is earning for you now. See it "
        "in the app.",
        "Finvest: {person} sent you an investment in the {product}. It is earning for you now.",
    ),
    "investment_transfer_held": Wording(
        "Checking your transfer",
        "We are checking your transfer of your {product} to {person} before it goes. "
        "We will tell you when it is done.",
        "Finvest: we are checking your transfer to {person} before it goes.",
    ),
    "investment_transfer_declined": Wording(
        "Transfer not made",
        "We did not make your transfer of your {product} to {person}. Your investment is as "
        "it was. Ask us in the app if you need to know more.",
        "Finvest: we did not make your transfer to {person}. Your investment is as it was.",
    ),
    "investment_transfer_failed": Wording(
        "Transfer not made",
        "Your transfer of your {product} to {person} could not be made, so your investment is "
        "as it was. See why in the app.",
        "Finvest: your transfer to {person} could not be made. Your investment is as it was.",
    ),
    "interest_earned": Wording(
        "Interest earned today",
        "You earned interest today, after tax. See the amount in the app.",
        "Finvest: you earned interest today, after tax. See the amount in the app.",
    ),
    "goal_top_up_due": Wording(
        "Your goal top-up is due",
        "Your monthly saving towards {goal} is due on {date}. See how much to save and pay in "
        "the app.",
        "Finvest: your monthly saving towards {goal} is due on {date}. Pay in the app.",
    ),
    "goal_reached": Wording(
        "You reached your goal",
        "Well done. {goal} has reached its target. It keeps earning until its investments "
        "mature, and you choose what happens then in the app.",
        "Finvest: well done, {goal} has reached its target. See it in the app.",
    ),
    "kyc_approved": Wording(
        "Identity check approved",
        "Your account is fully open. You can now invest.",
        "Finvest: your identity check is approved. You can now invest.",
    ),
    "kyc_retake_requested": Wording(
        "We need a photo again",
        "One of your identity photos was not clear enough. Open the app to take it again.",
        "Finvest: we need one of your identity photos again. Open the app to take it.",
    ),
    "kyc_rejected": Wording(
        "Identity check not approved",
        "We could not approve your identity check. Open Help in the app and we will "
        "explain what happens next.",
        "Finvest: we could not approve your identity check. Open Help in the app for what "
        "happens next.",
    ),
    "organisation_invited": Wording(
        "You are asked to sign for {organisation}",
        "{person} asked you to sign for {organisation} on Finvest. Open it in the app to join "
        "or decline.",
        "Finvest: {person} asked you to sign for {organisation}. Open the app to join or decline.",
    ),
    "organisation_returned": Wording(
        "Please change your application",
        "We need something changed in the application for {organisation} before we can open "
        "it. See what in the app.",
        "Finvest: please change the application for {organisation}. See what in the app.",
    ),
    "organisation_rejected": Wording(
        "Application not approved",
        "We could not open an account for {organisation}. Open Help in the app and we will "
        "explain what happens next.",
        "Finvest: we could not open an account for {organisation}. Open Help in the app.",
    ),
    "organisation_approved": Wording(
        "{organisation} is open",
        "The account for {organisation} is open, with its own account number. See it in the app.",
        "Finvest: the account for {organisation} is open. See its account number in the app.",
    ),
    "payout_destination_approved": Wording(
        "Payout account approved",
        "{destination} is approved. You can withdraw to it after the 24 hour wait.",
        "Finvest: {destination} is approved for withdrawals after the 24 hour wait.",
    ),
    "payout_destination_refused": Wording(
        "Payout account not approved",
        "We could not approve {destination}. A payout account must be in your own name.",
        "Finvest: we could not approve {destination}. A payout account must be in your own name.",
    ),
    "statement_ready": Wording(
        "{period} statement is ready",
        "Open it in Documents or have it emailed to you.",
        "Finvest: your {period} statement is ready in the app.",
    ),
    "new_sign_in": Wording(
        "New sign-in to your account",
        "Your account was signed in on {device}. If this was not you, change your password "
        "now and contact us.",
        "Finvest: your account was signed in on {device}. If this was not you, change your "
        "password now.",
    ),
    "password_changed": Wording(
        "Your password was changed",
        "If this was not you, contact us straight away. " + _NEVER_SHARE,
        "Finvest: your password was changed. If this was not you, contact us straight away.",
    ),
    "second_step_changed": Wording(
        "Sign-in settings changed",
        "{change}. If this was not you, contact us straight away.",
        "Finvest: {change}. If this was not you, contact us straight away.",
    ),
    "support_reply": Wording(
        "New message from Finvest",
        "Our team replied to you. Open Help to read it.",
        "Finvest: our team replied to you. Open Help in the app to read it.",
    ),
    "complaint_received": Wording(
        "Complaint {reference} received",
        "We have logged your complaint and will reply by {date}. You can follow it in Help.",
        "Finvest: we have logged your complaint {reference} and will reply by {date}.",
    ),
    "complaint_closed": Wording(
        "Complaint {reference} closed",
        "We have recorded the outcome of your complaint. Open Help to read it and tell us "
        "if you do not accept it.",
        "Finvest: complaint {reference} is closed. Open Help in the app to read the outcome.",
    ),
    "announcement": Wording("{title}", "{message}", "Finvest: {message}"),
}

# Staff write an announcement's words when they send it, so its wording is not a template.
EDITABLE: frozenset[str] = frozenset(EVENTS) - {"announcement"}


def _host(link: str) -> str:
    """The site a link goes to, the way a browser reads it."""
    rest = re.sub(r"(?i)^https?://", "", link)
    authority = re.split(r"[/?#]", rest, maxsplit=1)[0]
    host = authority.rsplit("@", 1)[-1].split(":", 1)[0].lower().rstrip(".")
    return host.removeprefix("www.")


def _is_outside(link: str) -> bool:
    host = _host(link)
    return host != OWN_SITE and not host.endswith("." + OWN_SITE)


def outside_links(text: str) -> list[str]:
    """Every link in `text` that goes anywhere but Finvest's own site."""
    return [match.group(0) for match in _LINK.finditer(text) if _is_outside(match.group(0))]


def format_value(value: Value) -> str:
    """One value as a client reads it. Refuses floats, so an amount is never rounded here.

    A text value is made safe, never refused for what it holds: a link to anywhere but
    Finvest's own site is removed, braces become brackets and a long value is cut. Refusing
    would fail the caller's transaction over the words of a message.
    """
    if isinstance(value, bool) or not isinstance(value, str | int | Money | date):
        raise InvalidVariables("A notification value is text, a whole number, Money or a date")
    if isinstance(value, Money):
        digits = value.currency.minor_digits
        whole, fraction = divmod(abs(value.minor), 10**digits)
        sign = "-" if value.minor < 0 else ""
        cents = f".{fraction:0{digits}d}" if digits else ""
        return f"{sign}{_SYMBOLS[value.currency]}{whole:,}{cents}"
    if isinstance(value, date):
        return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"
    if isinstance(value, int):
        return f"{value:,}"
    cleaned = " ".join(value.split()).replace("{", "(").replace("}", ")")
    cleaned = _LINK.sub(
        lambda match: LINK_REMOVED if _is_outside(match.group(0)) else match.group(0), cleaned
    )
    if not cleaned:
        # An empty name is a bug in the caller, and a message with a hole in it helps nobody.
        raise InvalidVariables("A notification value is empty")
    return _clamp(cleaned, MAX_VALUE)


def _clamp(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def format_values(spec: EventSpec, variables: Mapping[str, Value]) -> dict[str, str]:
    if set(variables) != set(spec.variables):
        raise InvalidVariables("The event was given the wrong variables")
    return {name: format_value(value) for name, value in variables.items()}


def _fill(template: str, values: Mapping[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda match: values[match.group(1)], template)


def render(spec: EventSpec, wording: Wording, variables: Mapping[str, Value]) -> Rendered:
    """The three texts with every placeholder filled, each cut to what is stored and sent.

    Whatever the wording and the values, the result fits the `notifications` columns, so
    writing a notification can never fail the transaction it reports on.
    """
    values = format_values(spec, variables)
    return Rendered(
        _clamp(_fill(wording.title, values), MAX_RENDERED_TITLE),
        _clamp(_fill(wording.body, values), MAX_RENDERED_BODY),
        _clamp(_fill(wording.sms, values), MAX_RENDERED_SMS),
    )


def _field_problems(spec: EventSpec, field: str, value: str, limit: int) -> list[tuple[str, str]]:
    problems: list[tuple[str, str]] = []
    if not value.strip():
        return [(field, "required")]
    if len(value) > limit:
        problems.append((field, "too_long"))
    if value != value.strip() or any(ord(character) < 32 for character in value):
        problems.append((field, "invalid_characters"))
    names = _PLACEHOLDER.findall(value)
    if set(names) - set(spec.variables):
        problems.append((field, "unknown_placeholder"))
    if len(names) != len(set(names)):
        # Once each, so the length of what is sent is bounded by the length of what was read.
        problems.append((field, "repeated_placeholder"))
    if "{" in _PLACEHOLDER.sub("", value) or "}" in _PLACEHOLDER.sub("", value):
        problems.append((field, "stray_brace"))
    if outside_links(value):
        problems.append((field, "outside_link"))
    if "%" in value:
        # A client never sees a tax rate, and a rate in a message is out of date the day the
        # rate changes. Amounts come from the event as net figures.
        problems.append((field, "no_rates"))
    return problems


def check(event_name: str, wording: Wording) -> Wording:
    """`wording` if staff may save it for this event, or `InvalidWording` saying why not."""
    if event_name not in EDITABLE:
        raise InvalidWording([("event", "not_editable")])
    spec = EVENTS[event_name]
    problems = [
        *_field_problems(spec, "title", wording.title, MAX_TITLE),
        *_field_problems(spec, "body", wording.body, MAX_BODY),
        *_field_problems(spec, "sms", wording.sms, MAX_SMS),
    ]
    if not problems and not wording.sms.isascii():
        # Anything outside the basic alphabet makes a text message cost several.
        problems.append(("sms", "invalid_characters"))
    if problems:
        raise InvalidWording(problems)
    return wording


# An announcement's SMS is "Finvest: " and the message, and must fit the wordings' SMS limit.
MAX_ANNOUNCEMENT_TITLE = MAX_TITLE
MAX_ANNOUNCEMENT_MESSAGE = MAX_SMS - len("Finvest: ")


def check_announcement(title: str, message: str) -> None:
    """Nothing, if staff may send these words to every client, or `InvalidWording` saying why.

    The same rules as an edited wording: Finvest's own links only, no rates, basic letters in
    what can go by SMS. An announcement has no placeholders, so a brace is refused.
    """
    spec = EVENTS["announcement"]
    problems = [
        *_field_problems(spec, "title", title, MAX_ANNOUNCEMENT_TITLE),
        *_field_problems(spec, "message", message, MAX_ANNOUNCEMENT_MESSAGE),
    ]
    for field, value in (("title", title), ("message", message)):
        if ("{" in value or "}" in value) and (field, "stray_brace") not in problems:
            problems.append((field, "stray_brace"))
    if not any(field == "message" for field, _ in problems) and not message.isascii():
        problems.append(("message", "invalid_characters"))
    if problems:
        raise InvalidWording(problems)


def sample_values(spec: EventSpec) -> dict[str, Value]:
    """Stand-in values for a preview in the console."""
    samples: dict[str, Value] = {
        "amount": Money(1_000_000, Currency.UGX),
        "method": "MTN Mobile Money",
        "destination": "MTN Mobile Money",
        "product": "91 Day Treasury Bill",
        "date": date(2026, 11, 20),
        "count": 3,
        "period": "October",
        "device": "a new phone",
        "change": "A passkey was added to your account",
        "reference": "C-2611",
        "title": "A message from Finvest",
        "message": "Your investment matures soon.",
        "person": "Brian K.",
        "goal": "School fees 2027",
        "organisation": "Kampala Traders Ltd",
    }
    return {name: samples[name] for name in spec.variables}
