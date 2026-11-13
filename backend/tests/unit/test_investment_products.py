from dataclasses import replace

import pytest

from app.modules.investments.domain.products import (
    SEED_PRODUCTS,
    InterestPaid,
    InvalidProduct,
    Product,
    ProductKind,
    ProductStatus,
    Refusal,
    check_new_investment,
)
from app.shared.money import Currency, Money


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


BILL = SEED_PRODUCTS[0]


def test_the_catalogue_is_seeded_with_the_three_original_products() -> None:
    assert [
        (p.code, p.name, p.currency, p.term_days, p.minimum.to_major()) for p in SEED_PRODUCTS
    ] == [
        ("TREA-91", "91 Day Treasury Bill", Currency.UGX, 91, "100000"),
        ("TREA-182", "182 Day Treasury Bill", Currency.UGX, 182, "200000"),
        ("USD-BOND", "USD Fixed Income", Currency.USD, None, "500.00"),
    ]


def test_treasury_bills_are_open_and_the_dollar_product_waits_for_dollar_payments() -> None:
    assert [p.status for p in SEED_PRODUCTS] == [
        ProductStatus.LIVE,
        ProductStatus.LIVE,
        ProductStatus.PAUSED,
    ]


@pytest.mark.parametrize(
    ("amount", "refusal"),
    [
        (ugx(100_000), None),
        (ugx(5_000_000_000), None),
        (ugx(99_999), Refusal.BELOW_MINIMUM),
        (ugx(0), Refusal.AMOUNT_NOT_POSITIVE),
        (ugx(-100_000), Refusal.AMOUNT_NOT_POSITIVE),
        (Money(100_000_00, Currency.USD), Refusal.CURRENCY_NOT_SUPPORTED),
    ],
)
def test_an_investment_must_meet_the_minimum_in_the_product_currency(
    amount: Money, refusal: Refusal | None
) -> None:
    assert check_new_investment(BILL, amount) is refusal


@pytest.mark.parametrize("status", [ProductStatus.DRAFT, ProductStatus.PAUSED])
def test_only_a_live_product_takes_new_investments(status: ProductStatus) -> None:
    product = replace(BILL, status=status)
    assert check_new_investment(product, ugx(1_000_000)) is Refusal.PRODUCT_NOT_OPEN


@pytest.mark.parametrize(
    "change",
    [
        {"code": "trea-91"},
        {"code": "TREA 91"},
        {"code": "-TREA"},
        {"code": "T" * 25},
        {"name": ""},
        {"name": " 91 Day Treasury Bill"},
        {"name": "x" * 81},
        {"term_days": 0},
        {"term_days": 3651},
        {"term_days": 91.0},
        {"minimum": ugx(0)},
        {"minimum": Money(100, Currency.USD)},
        {"term_days": None},  # an open term cannot pay interest at maturity
    ],
)
def test_a_product_that_contradicts_itself_is_refused(change: dict[str, object]) -> None:
    with pytest.raises(InvalidProduct):
        replace(BILL, **change)  # type: ignore[arg-type]


def test_an_open_term_product_pays_interest_on_a_schedule() -> None:
    product = Product(
        code="USD-OPEN",
        name="Open dollars",
        kind=ProductKind.FIXED_INCOME,
        currency=Currency.USD,
        term_days=None,
        minimum=Money(100_00, Currency.USD),
        interest_paid=InterestPaid.MONTHLY,
    )
    assert not product.has_fixed_term
    assert BILL.has_fixed_term
