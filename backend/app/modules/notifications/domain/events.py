"""The catalogue of things Finvest tells a client about. Pure: no I/O.

This is the contract other modules build against. Each event has a name, the kind of alert it
is (which decides the channels a client can choose for it), and the exact variables its
wording needs. A module raises an event by calling `app.modules.notifications.contract.notify`
with one of these names. See docs/notifications.md.
"""

from dataclasses import dataclass
from enum import StrEnum


class Category(StrEnum):
    """The kinds of alert a client sets channels for."""

    MONEY = "money"
    INVESTMENTS = "investments"
    EARNINGS = "earnings"
    ACCOUNT = "account"
    SECURITY = "security"
    SUPPORT = "support"
    NEWS = "news"


class Channel(StrEnum):
    """Every notification is kept in the app. The other three are the client's choice."""

    PUSH = "push"
    SMS = "sms"
    EMAIL = "email"


@dataclass(frozen=True, slots=True)
class EventSpec:
    name: str
    category: Category
    # Every one of these must be given, and nothing else.
    variables: tuple[str, ...]
    # The module that raises it. Documentation only.
    raised_by: str


def _event(name: str, category: Category, raised_by: str, *variables: str) -> EventSpec:
    return EventSpec(name, category, variables, raised_by)


_SPECS = (
    # Money in.
    _event("payment_confirmed", Category.MONEY, "payments", "amount", "method"),
    _event("payment_failed", Category.MONEY, "payments", "amount", "method"),
    # Money out.
    _event("payout_processing", Category.MONEY, "payouts", "amount", "destination"),
    _event("payout_sent", Category.MONEY, "payouts", "amount", "destination"),
    _event("payout_failed", Category.MONEY, "payouts", "amount", "destination"),
    # Staff declined a withdrawal held for review because it is large.
    _event("payout_declined", Category.MONEY, "payouts", "amount", "destination"),
    # A paid withdrawal came back from the bank or network. Never the amount.
    _event("payout_returned", Category.MONEY, "payouts", "destination"),
    # Investments.
    _event("investment_started", Category.INVESTMENTS, "investments", "amount", "product"),
    _event("investment_maturing", Category.INVESTMENTS, "investments", "product", "date"),
    _event("investment_matured", Category.INVESTMENTS, "investments", "amount", "product"),
    # Cashing out a running investment before it matures. The amount is what the client gets.
    _event(
        "investment_withdrawal_requested", Category.INVESTMENTS, "investments", "amount", "product"
    ),
    _event("investment_withdrawal_paid", Category.INVESTMENTS, "investments", "amount", "product"),
    # Sending an investment to another client. `person` is the other client's short name, the
    # first given name and the surname's initial. Never the amount in the words.
    _event("investment_sent", Category.INVESTMENTS, "investments", "amount", "product", "person"),
    _event(
        "investment_received", Category.INVESTMENTS, "investments", "amount", "product", "person"
    ),
    # Large: waiting for staff before it moves.
    _event("investment_transfer_held", Category.INVESTMENTS, "investments", "product", "person"),
    _event(
        "investment_transfer_declined", Category.INVESTMENTS, "investments", "product", "person"
    ),
    # It could not be done, for example the investment matured first. Nothing moved.
    _event("investment_transfer_failed", Category.INVESTMENTS, "investments", "product", "person"),
    _event("interest_earned", Category.EARNINGS, "investments", "amount", "count"),
    # The account.
    _event("kyc_approved", Category.ACCOUNT, "kyc"),
    _event("kyc_retake_requested", Category.ACCOUNT, "kyc"),
    _event("kyc_rejected", Category.ACCOUNT, "kyc"),
    _event("payout_destination_approved", Category.ACCOUNT, "payouts", "destination"),
    _event("payout_destination_refused", Category.ACCOUNT, "payouts", "destination"),
    _event("statement_ready", Category.ACCOUNT, "statements", "period"),
    # Security. A client cannot switch these off.
    _event("new_sign_in", Category.SECURITY, "identity", "device"),
    _event("password_changed", Category.SECURITY, "identity"),
    _event("second_step_changed", Category.SECURITY, "identity", "change"),
    # Help.
    _event("support_reply", Category.SUPPORT, "support"),
    _event("complaint_received", Category.SUPPORT, "support", "reference", "date"),
    _event("complaint_closed", Category.SUPPORT, "support", "reference"),
    # Announcements are written by staff, so they carry their own words.
    _event("announcement", Category.NEWS, "support", "title", "message"),
)

EVENTS: dict[str, EventSpec] = {spec.name: spec for spec in _SPECS}


class UnknownEvent(ValueError):
    """The event is not in the catalogue. Add it here, with its wording, before raising it."""


def event(name: str) -> EventSpec:
    try:
        return EVENTS[name]
    except KeyError:
        raise UnknownEvent("Unknown notification event") from None
