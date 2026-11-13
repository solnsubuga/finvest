"""A receipt for money Finvest received from a client. Pure.

One receipt for each confirmed payment, issued once. It names what the payment was for and
where it came from, and never carries a full mobile money or bank account number: a number
is shown by its last digits only.
"""

from dataclasses import dataclass
from datetime import datetime

from app.modules.documents.domain import formatting
from app.shared.money import Money


@dataclass(frozen=True, slots=True)
class Receipt:
    reference: str  # "RC-" and the receipt's ID
    client_name: str
    account_number: str  # ten digits
    amount: Money
    received_at: datetime
    method: str  # "MTN Mobile Money" or "Bank transfer"
    # "number ending 456". None when we do not know the payer's number, as for a bank
    # transfer, and then the receipt leaves the line out.
    paid_from: str | None
    provider_reference: str | None  # the MTN, Airtel or bank reference, when there is one
    payment_reference: str  # the Finvest deposit ID
    paid_for: str  # "91 Day Treasury Bill"

    def __post_init__(self) -> None:
        formatting.account_number(self.account_number)
        if not self.amount.is_positive:
            raise ValueError("A receipt is for money received")
        formatting.moment(self.received_at)


def masked(number: str) -> str:
    """`number ending 456`: a payer's phone or account number shown by its last three digits."""
    digits = "".join(character for character in number if character.isdigit())
    if len(digits) < 6:
        raise ValueError("Too short to be a payer's number")
    return f"number ending {digits[-3:]}"
