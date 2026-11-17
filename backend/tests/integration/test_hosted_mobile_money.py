"""Mobile money that Pesapal collects on its hosted page, chosen on Settings, against real
PostgreSQL, the worker, the API and the ledger, with the fake Pesapal of the card tests.

This app has Pesapal and no ioTec, so it also shows the fallback: with no ioTec running, a
mobile money payment goes to Pesapal whatever Settings say.
"""

from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.payments import cards
from app.modules.payments.domain.deposits import DepositStatus as S
from app.shared.actors import SYSTEM
from tests.integration.test_card_deposits import (
    HASHER,
    Setup,
    as_deposit,
    entry_lines,
    key,
    pesapal,
    until,
)
from tests.integration.test_card_deposits import setup as card_setup  # noqa: F401 - a fixture
from tests.support.database import TestDatabase
from tests.support.investments import request as request_investment
from tests.support.payments import FakeCollections, drain
from tests.support.settings import set_setting

pytestmark = pytest.mark.integration

AMOUNT = 1_000_000
COLLECTOR = "mobile_money_collector"


@pytest.fixture
def setup(card_setup: Setup) -> Setup:  # noqa: F811 - the fixture, not the import
    """The card tests' app, worker and fake Pesapal, with no ioTec running."""
    return card_setup


async def start(setup: Setup) -> tuple[str, dict[str, object]]:
    async with setup.http() as http:
        client = await setup.client(http)
        investment = await request_investment(setup.database, client, setup.clock)
        started = await http.post(
            f"/client/investments/{investment.id}/payments/mobile-money",
            json={"network": "mtn"},
            headers=key(),
        )
        assert started.status_code == 201, started.text
        body = started.json()
        await drain(setup.worker)
        waiting = (await http.get(f"/client/payments/{body['id']}")).json()
    return body["id"], waiting


def pay_from(setup: Setup, deposit_id: str, phone: str) -> None:
    setup.fake.pay(deposit_id)
    order = setup.fake.orders[setup.fake.tracking_id(deposit_id)]
    order["payment_method"] = "MTN"
    order["payment_account"] = phone.removeprefix("+")


async def test_pesapal_collects_mobile_money_on_its_page(
    setup: Setup,
) -> None:
    await set_setting(
        setup.database, "mobile_money_collector", {"provider": "pesapal"}, setup.clock
    )
    deposit_id, waiting = await start(setup)
    assert waiting["method"] == "mtn" and waiting["status"] == "awaiting_approval"
    assert waiting["on_page"] is True
    assert str(waiting["page_url"]).startswith("https://pay.pesapal.test/")
    # No card charge, no card: Finvest bears Pesapal's mobile money fee.
    assert waiting["card_charge"] is None and waiting["card_page_url"] is None
    assert setup.fake.orders[setup.fake.tracking_id(deposit_id)]["amount"] == AMOUNT
    deposit = await as_deposit(setup, deposit_id)
    assert deposit.provider == "pesapal" and deposit.payer_msisdn is not None

    pay_from(setup, deposit_id, deposit.payer_msisdn)
    confirmed = await until(setup, deposit, S.CONFIRMED, S.FAILED, S.UNCERTAIN)
    assert confirmed.status is S.CONFIRMED
    assert await entry_lines(setup.system, deposit_id) == [
        ("client.awaiting_start", -AMOUNT),
        ("pesapal.balance", AMOUNT),
    ]
    assert setup.fake.sends_for(deposit_id) == 1


async def test_a_payment_from_another_number_counts_and_keeps_who_paid(setup: Setup) -> None:
    # The owner accepts money from any payer (2026-11-15): a relative's number pays.
    await set_setting(
        setup.database, "mobile_money_collector", {"provider": "pesapal"}, setup.clock
    )
    deposit_id, _ = await start(setup)
    deposit = await as_deposit(setup, deposit_id)
    pay_from(setup, deposit_id, "+256701999999")
    confirmed = await until(setup, deposit, S.CONFIRMED, S.FAILED, S.UNCERTAIN)
    assert (confirmed.status, confirmed.code) == (S.CONFIRMED, None)
    assert await entry_lines(setup.system, deposit_id) == [
        ("client.awaiting_start", -AMOUNT),
        ("pesapal.balance", AMOUNT),
    ]
    # The client's own number stays the deposit's; the order keeps the number that paid.
    assert confirmed.payer_msisdn == deposit.payer_msisdn
    async with setup.system.actor_transaction(SYSTEM) as session:
        order = await cards.find_order(session, deposit_id)
    assert order is not None and order.paid_from == "+256701999999"
    # It is kept once and never changed.
    with pytest.raises(DBAPIError, match="never changed"):
        async with setup.system.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "UPDATE payment_card_orders SET paid_from_msisdn = '+256772000000' "
                    "WHERE deposit_id = :id"
                ),
                {"id": deposit_id},
            )


async def test_a_payment_whose_payer_is_not_named_still_counts(setup: Setup) -> None:
    await set_setting(
        setup.database, "mobile_money_collector", {"provider": "pesapal"}, setup.clock
    )
    deposit_id, _ = await start(setup)
    deposit = await as_deposit(setup, deposit_id)
    setup.fake.pay(deposit_id)
    setup.fake.orders[setup.fake.tracking_id(deposit_id)]["payment_method"] = "MTN"
    confirmed = await until(setup, deposit, S.CONFIRMED, S.FAILED, S.UNCERTAIN)
    assert confirmed.status is S.CONFIRMED
    async with setup.system.actor_transaction(SYSTEM) as session:
        order = await cards.find_order(session, deposit_id)
    assert order is not None and order.paid_from is None


async def test_the_client_may_name_another_number_to_pay_from(setup: Setup) -> None:
    await set_setting(
        setup.database, "mobile_money_collector", {"provider": "pesapal"}, setup.clock
    )
    async with setup.http() as http:
        client = await setup.client(http)
        investment = await request_investment(setup.database, client, setup.clock)
        started = await http.post(
            f"/client/investments/{investment.id}/payments/mobile-money",
            json={"network": "airtel", "phone": "0701 999 999"},
            headers=key(),
        )
    assert started.status_code == 201, started.text
    assert (await as_deposit(setup, started.json()["id"])).payer_msisdn == "+256701999999"


async def test_with_no_iotec_running_mobile_money_falls_back_to_pesapal(setup: Setup) -> None:
    # Settings still say ioTec, the default.
    deposit_id, waiting = await start(setup)
    assert (await as_deposit(setup, deposit_id)).provider == "pesapal"
    assert waiting["page_url"] is not None


async def test_a_paused_network_refuses_a_new_payment_on_the_page(setup: Setup) -> None:
    from app.modules.platform_settings.domain.catalogue import CHANNELS

    await set_setting(
        setup.database, "mobile_money_collector", {"provider": "pesapal"}, setup.clock
    )
    await set_setting(
        setup.database,
        "payment_channels",
        {channel: channel != "mtn_pay_in" for channel in CHANNELS},
        setup.clock,
    )
    async with setup.http() as http:
        client = await setup.client(http)
        investment = await request_investment(setup.database, client, setup.clock)
        paused = await http.post(
            f"/client/investments/{investment.id}/payments/mobile-money",
            json={"network": "mtn"},
            headers=key(),
        )
    assert paused.status_code == 503
    assert paused.json()["error"]["fields"][0]["code"] == "channel_paused"


async def test_coming_back_from_the_page_asks_for_a_read(setup: Setup) -> None:
    await set_setting(
        setup.database, "mobile_money_collector", {"provider": "pesapal"}, setup.clock
    )
    async with setup.http() as http:
        client = await setup.client(http)
        investment = await request_investment(setup.database, client, setup.clock)
        started = await http.post(
            f"/client/investments/{investment.id}/payments/mobile-money",
            json={"network": "mtn"},
            headers=key(),
        )
        deposit_id = started.json()["id"]
        await drain(setup.worker)
        deposit = await as_deposit(setup, deposit_id)
        assert deposit.payer_msisdn is not None
        pay_from(setup, deposit_id, deposit.payer_msisdn)
        back = await http.post(f"/client/payments/{deposit_id}/card-return")
        assert back.status_code == 202
        assert back.json()["on_page"] is True
    settled = await until(setup, deposit, S.CONFIRMED, S.FAILED, S.UNCERTAIN)
    assert settled.status is S.CONFIRMED


async def collection(setup: Setup) -> object:
    async with setup.http() as http:
        await setup.client(http)
        answer = await http.get("/client/mobile-money")
    assert answer.status_code == 200, answer.text
    return answer.json()["on_page"]


async def test_the_pay_screen_is_told_mobile_money_is_paid_on_the_page(setup: Setup) -> None:
    # With no ioTec running, the page is used whatever Settings say.
    assert await collection(setup) is True
    await set_setting(
        setup.database, "mobile_money_collector", {"provider": "pesapal"}, setup.clock
    )
    assert await collection(setup) is True


async def test_the_pay_screen_follows_settings_when_both_collectors_run(
    setup: Setup,
    test_database: TestDatabase,
    app_database: Database,
) -> None:
    app = create_app(
        test_database.settings("finvest_app"),
        app_database,
        setup.clock,
        HASHER,
        collections=FakeCollections(),
        card_collections=pesapal(setup.fake, setup.clock),
    )
    both = replace(setup, app=app, identity=app.state.identity)
    # ioTec by default: the network prompts the number given in the app.
    assert await collection(both) is False
    await set_setting(
        setup.database, "mobile_money_collector", {"provider": "pesapal"}, setup.clock
    )
    assert await collection(both) is True


async def test_a_signed_out_visitor_is_not_told_how_mobile_money_is_collected(
    setup: Setup,
) -> None:
    async with setup.http() as http:
        assert (await http.get("/client/mobile-money")).status_code == 401
