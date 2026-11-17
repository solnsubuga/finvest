# ruff: noqa: S608 - the SQL is built from this file's own constants
"""Migration 0531 marks the bond payouts already made with their purpose, a resend with the
purpose of the payout it resends, and leaves every other payout a withdrawal. It switches the
payouts guard off only for its backfill, and its downgrade leaves the table as it found it.

The rows are written as the administrator with triggers and foreign keys off, as the shapes the
bond and payout code leaves behind, because a whole client, ledger and bond holding is not what
this test is about. Each table's own checks still hold them.
"""

import os
import subprocess
from collections.abc import Iterator

import pytest
from sqlalchemy.engine import URL

from tests.support import database as harness
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

BEFORE, PURPOSE = "0530_treasury_bill_type_tax", "0531_payout_purpose"


def pid(name: str) -> str:
    """A push ID shaped from a readable name."""
    return ("-" + name)[:20].ljust(20, "x")


CLIENT, OTHER = pid("ClientA"), pid("ClientB")
COUPON_PAYOUT, REPAID_PAYOUT = pid("PayoutCoupon"), pid("PayoutRepaid")
SALE_PAYOUT, RESENT_PAYOUT = pid("PayoutSale"), pid("PayoutSaleResent")
WITHDRAWAL, OTHERS_WITHDRAWAL = pid("PayoutWithdrawal"), pid("PayoutOthers")
COUPON, REDEMPTION, SALE = pid("Coupon"), pid("Redemption"), pid("Sale")


def payout(
    payout_id: str, client: str, source: str, status: str, key: str, amount: int = 72_000
) -> str:
    return (
        "INSERT INTO payouts (id, client_id, destination_id, kind, amount_minor, currency, "
        "status, business_date, source_account_id, source_reference, in_flight_account_id, "
        f"request_key, created_at, updated_at) VALUES ('{payout_id}', '{client}', "
        f"'{pid('Destination')}', 'mobile_money', {amount}, 'UGX', '{status}', '2026-11-10', "
        f"'{pid('Src' + payout_id[1:8])}', '{source}', '{pid('Fly' + payout_id[1:8])}', "
        f"'{key}', now(), now());"
    )


SEED = f"""
SET session_replication_role = replica;
{payout(COUPON_PAYOUT, CLIENT, COUPON, "paid", f"bond-coupon:{COUPON}")}
{payout(REPAID_PAYOUT, CLIENT, REDEMPTION, "paid", f"bond-matured:{REDEMPTION}", 1_072_000)}
{payout(SALE_PAYOUT, CLIENT, SALE, "paid", f"bond-sold:{SALE}", 1_009_096)}
{payout(RESENT_PAYOUT, CLIENT, SALE, "queued", f"resend:{SALE}", 1_009_096)}
{payout(WITHDRAWAL, CLIENT, pid("Investment"), "paid", "withdraw:first")}
{payout(OTHERS_WITHDRAWAL, OTHER, SALE, "paid", "withdraw:other")}
INSERT INTO bond_coupons (id, holding_id, client_id, coupon_date, currency, gross_minor,
    tax_minor, net_minor, entry_id, status, payout_id, created_at, updated_at)
VALUES ('{COUPON}', '{pid("Holding")}', '{CLIENT}', '2026-11-10', 'UGX', 80000, 8000, 72000,
    '{pid("EntryCoupon")}', 'paid_out', '{COUPON_PAYOUT}', now(), now());
INSERT INTO bond_redemptions (id, holding_id, client_id, redeemed_on, currency, face_minor,
    cost_minor, coupon_gross_minor, coupon_tax_minor, coupon_net_minor, amount_minor, entry_id,
    status, payout_id, created_at, updated_at)
VALUES ('{REDEMPTION}', '{pid("HoldingTwo")}', '{CLIENT}', '2026-11-10', 'UGX', 1000000,
    985883, 80000, 8000, 72000, 1072000, '{pid("EntryRepaid")}', 'paid_out',
    '{REPAID_PAYOUT}', now(), now());
INSERT INTO bond_sales (id, holding_id, client_id, currency, face_minor, cost_minor,
    destination_id, requested_at, status, price, sold_on, reason, priced_by, priced_at,
    decided_by, decided_at, clean_minor, accrued_minor, tax_minor, amount_minor, entry_id,
    settled_at, payout_status, payout_id, updated_at)
VALUES ('{SALE}', '{pid("HoldingThree")}', '{CLIENT}', 'UGX', 1000000, 985883,
    '{pid("Destination")}', now(), 'sold', 972500, '2026-11-10', 'Desk trade 4460',
    '{pid("StaffOne")}', now(), '{pid("StaffTwo")}', now(), 972500, 40662, 4066, 1009096,
    '{pid("EntrySale")}', now(), 'paid_out', '{SALE_PAYOUT}', now());
SET session_replication_role = origin;
"""


def query(url: URL, sql: str) -> str:
    target = url.set(drivername="postgresql").render_as_string(hide_password=False)
    found = subprocess.run(
        ["psql", "--no-psqlrc", "-At", "-v", "ON_ERROR_STOP=1", target, "-c", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    return found.stdout.strip()


@pytest.fixture
def scratch_database() -> Iterator[TestDatabase]:
    database = harness.create(f"finvest_test_payout_purpose_{os.getpid()}")
    try:
        yield database
    finally:
        harness.drop(database)


def test_the_backfill_marks_bond_payouts_and_puts_the_guard_back(
    scratch_database: TestDatabase,
) -> None:
    url = scratch_database.admin.set(database=scratch_database.name)
    assert harness.alembic(scratch_database, "upgrade", BEFORE).returncode == 0
    harness.psql(url, "-c", SEED)

    result = harness.alembic(scratch_database, "upgrade", PURPOSE)
    assert result.returncode == 0, result.stderr
    purposes = dict(
        line.split("|")
        for line in query(url, "SELECT id, purpose FROM payouts ORDER BY id").splitlines()
    )
    assert purposes == {
        COUPON_PAYOUT: "bond_coupon",
        REPAID_PAYOUT: "bond_repayment",
        SALE_PAYOUT: "bond_sale",
        # Sent again from the same handover, so it is still the sale's money.
        RESENT_PAYOUT: "bond_sale",
        WITHDRAWAL: "withdrawal",
        # The same source reference, but another client's: never matched.
        OTHERS_WITHDRAWAL: "withdrawal",
    }
    # Both triggers are on: the guard the backfill switched off, and the new one.
    assert query(
        url,
        "SELECT tgname::text || ':' || tgenabled::text FROM pg_trigger "
        "WHERE tgrelid = 'payouts'::regclass "
        "AND tgname IN ('payouts_guard', 'payouts_fixed_purpose') ORDER BY tgname",
    ).splitlines() == ["payouts_fixed_purpose:O", "payouts_guard:O"]

    result = harness.alembic(scratch_database, "downgrade", BEFORE)
    assert result.returncode == 0, result.stderr
    assert (
        query(
            url,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'payouts' AND column_name = 'purpose'",
        )
        == "0"
    )
    assert query(url, "SELECT count(*) FROM pg_proc WHERE proname LIKE '%purpose_fixed'") == "0"
    assert query(
        url, "SELECT tgenabled FROM pg_trigger WHERE tgname = 'payouts_guard'"
    ).splitlines() == ["O"]
    assert query(url, "SELECT count(*) FROM payouts") == "6"
