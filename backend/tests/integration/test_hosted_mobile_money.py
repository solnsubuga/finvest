"""Mobile money that Pesapal collects on its hosted page, chosen on Settings, against real
PostgreSQL, the worker, the API and the ledger, with the fake Pesapal of the card tests.

This app has Pesapal and no ioTec, so it also shows the fallback: with no ioTec running, a
mobile money payment goes to Pesapal whatever Settings say.
"""

import pytest

from app.modules.payments.domain.deposits import DepositStatus as S
from tests.integration.test_card_deposits import (
    Setup,
    as_deposit,
    entry_lines,
    key,
    until,
)
from tests.integration.test_card_deposits import setup as card_setup  # noqa: F401 - a fixture
from tests.support.investments import request as request_investment
from tests.support.payments import drain, reload
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


async def test_pesapal_collects_mobile_money_on_its_page_from_the_clients_own_number(
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
    assert setup.fake.sends == 1


async def test_a_payment_from_another_number_is_held_for_staff_and_not_credited(
    setup: Setup,
) -> None:
    await set_setting(
        setup.database, "mobile_money_collector", {"provider": "pesapal"}, setup.clock
    )
    deposit_id, _ = await start(setup)
    deposit = await as_deposit(setup, deposit_id)
    pay_from(setup, deposit_id, "+256701999999")
    held = await until(setup, deposit, S.UNCERTAIN, S.CONFIRMED)
    assert (held.status, held.code) == (S.UNCERTAIN, "payer_differs")
    assert await entry_lines(setup.system, deposit_id) == []
    # Reading it again changes nothing: it is for staff now.
    setup.clock.advance(seconds=3600)
    await drain(setup.worker)
    assert (await reload(setup.system, deposit)).status is S.UNCERTAIN


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
