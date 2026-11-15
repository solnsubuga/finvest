"""Business ceilings shared by more than one module. Pure: no I/O.

The API's own ceiling on an amount (10^15 minor units) is there for integer safety. These are
the figures the business accepts, so an absurd amount is refused when it is asked for rather
than sitting as an unpaid investment or an unreachable goal.
"""

from app.shared.money import Currency, Money

# The largest single investment a client may ask for, and the largest goal target. A larger sum
# is split over several investments. About the same in both currencies.
LARGEST_SINGLE_AMOUNT: dict[Currency, Money] = {
    Currency.UGX: Money(5_000_000_000, Currency.UGX),
    Currency.USD: Money(150_000_000, Currency.USD),
}


def largest_single_amount(currency: Currency) -> Money:
    return LARGEST_SINGLE_AMOUNT[currency]
