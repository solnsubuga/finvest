"""The card sandbox: the last three digits of the total choose what happens."""

import pytest

from app.modules.payments.domain.cards import CancelResult, CardOrderRequest, OrderOpened
from app.modules.payments.domain.outcomes import Confirmed, Deferred, Pending, Rejected, Uncertain
from app.modules.payments.providers.card_sandbox import SandboxCards
from app.modules.payments.settings import CardSettings, Deployment, SandboxNotAllowed
from app.modules.payments.wiring import card_provider
from app.shared.money import Currency, Money
from tests.support.clock import FakeClock

RETURN = "http://localhost:8081/pay/card/return"


def order(total: int) -> CardOrderRequest:
    return CardOrderRequest("dep", Money(total, Currency.UGX), "Finvest", "+256772123456")


async def test_an_order_is_paid_after_a_few_seconds_with_a_test_card() -> None:
    clock = FakeClock()
    cards = SandboxCards(clock, return_url=RETURN)
    opened = await cards.open_order(order(1_035_000))
    assert isinstance(opened, OrderOpened) and opened.page_url.startswith(RETURN)
    reference = opened.provider_reference
    first = await cards.order_status(request_id="dep", provider_reference=reference)
    assert isinstance(first.reading, Pending)
    clock.advance(seconds=10)
    paid = await cards.order_status(request_id="dep", provider_reference=reference)
    assert paid.reading == Confirmed(reference, Money(1_035_000, Currency.UGX))
    assert paid.details is not None and paid.details.last_four == "1111"


@pytest.mark.parametrize(("total", "kind"), [(1_000_004, Uncertain), (1_000_005, Deferred)])
async def test_lost_and_busy_orders(total: int, kind: type) -> None:
    assert isinstance(
        await SandboxCards(FakeClock(), return_url=RETURN).open_order(order(total)), kind
    )


async def test_a_declined_card_and_an_abandoned_page() -> None:
    clock = FakeClock()
    cards = SandboxCards(clock, return_url=RETURN)
    declined = await cards.open_order(order(1_000_001))
    abandoned = await cards.open_order(order(1_000_003))
    assert isinstance(declined, OrderOpened) and isinstance(abandoned, OrderOpened)
    clock.advance(seconds=10)
    found = await cards.order_status(
        request_id="dep", provider_reference=declined.provider_reference
    )
    assert isinstance(found.reading, Rejected)
    assert (
        await cards.cancel_order(provider_reference=abandoned.provider_reference)
        is CancelResult.CANCELLED
    )


def test_the_card_sandbox_is_refused_in_production() -> None:
    with pytest.raises(SandboxNotAllowed):
        card_provider(
            CardSettings(provider="sandbox"), FakeClock(), Deployment(environment="production")
        )
    assert card_provider(CardSettings(), FakeClock()) is None
    assert (
        card_provider(
            CardSettings(provider="sandbox"), FakeClock(), Deployment(environment="staging")
        )
        is not None
    )
