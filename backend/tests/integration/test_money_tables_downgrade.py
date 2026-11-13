"""A downgrade must never delete the books, the issued numbers, the audit trail, the job
history, the receipts that stop a command running twice, the identity records, the payout
destinations, the withdrawal limits, the payouts, the migration checks, the imported
statements, the help chat or the complaints register."""

import os
from collections.abc import Iterator

import pytest

from tests.support import database as harness
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

ROWS = {
    "0010_ledger": (
        "INSERT INTO ledger_accounts (id, code, kind, currency, name, created_at) "
        "VALUES ('-Nabc0000000000000A1', 'test.cash', 'asset', 'UGX', 'Cash', now())",
        "refusing to drop a ledger",
    ),
    "0011_account_numbers": (
        "INSERT INTO account_numbers (number, issued_at) VALUES ('1001004827', now())",
        "refusing to drop the registry",
    ),
    "0012_audit_events": (
        "INSERT INTO audit_events (id, occurred_at, category, action, outcome, actor_kind, "
        "resource_type) VALUES ('-Nabc0000000000000A1', now(), 'change', 'test.changed', "
        "'succeeded', 'system', 'test')",
        "refusing to drop an audit trail",
    ),
    "0002_jobs": (
        "INSERT INTO outbox_events (id, topic, idempotency_key, payload, created_at) "
        "VALUES ('-Nabc0000000000000A1', 'test.sent', 'k', '{}', now())",
        "refusing to drop job tables",
    ),
    "0003_idempotency": (
        "INSERT INTO operation_receipts (actor_kind, actor_id, operation, idempotency_key, "
        "request_digest, status_code, response, created_at) VALUES ('client', "
        "'-Nabc0000000000000A1', 'test.run', 'key-0001', repeat('0', 64), 200, '{}', now())",
        "refusing to drop operation receipts",
    ),
    "0062_investment_interest_runs": (
        "INSERT INTO investment_interest_runs (run_date, currency, started_at) "
        "VALUES (DATE '2026-11-14', 'UGX', now())",
        "refusing to drop interest runs",
    ),
    "0063_investment_maturity": (
        "BEGIN; SELECT set_config('finvest.actor_kind', 'client', true); "
        "SELECT set_config('finvest.actor_id', '-Nabc0000000000000B1', true); "
        "INSERT INTO investments (id, client_id, product_id, currency, principal_minor, "
        "annual_rate_bps, tax_bps, term_days, at_maturity, created_at, pay_by, updated_at) "
        "VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', 'FvProduct1TREA91_001', "
        "'UGX', 1000000, 1250, 1500, 91, 'roll_over', now(), now() + interval '72 hours', "
        "now()); UPDATE investments SET at_maturity = 'pay_out', "
        "payout_destination_id = '-Nabc0000000000000D1'; COMMIT",
        "refusing to drop the record of matured investments",
    ),
    "0061_investments": (
        "BEGIN; SELECT set_config('finvest.actor_kind', 'client', true); "
        "SELECT set_config('finvest.actor_id', '-Nabc0000000000000B1', true); "
        "INSERT INTO investments (id, client_id, product_id, currency, principal_minor, "
        "annual_rate_bps, tax_bps, term_days, at_maturity, created_at, pay_by, updated_at) "
        "VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', 'FvProduct1TREA91_001', "
        "'UGX', 1000000, 1250, 1500, 91, 'roll_over', now(), now() + interval '72 hours', "
        "now()); COMMIT",
        "refusing to drop investments",
    ),
    "0060_investment_products": (
        "BEGIN; SELECT set_config('finvest.actor_kind', 'staff', true); "
        "SELECT set_config('finvest.actor_id', '-Nabc0000000000000B1', true); "
        "INSERT INTO investment_rate_proposals (id, product_id, effective_from, annual_rate_bps, "
        "tax_bps, reason, proposed_by, proposed_at) VALUES ('-Nabc0000000000000A1', "
        "'FvProduct1TREA91_001', DATE '2030-01-01', 1300, 1500, 'Auction', "
        "'-Nabc0000000000000B1', now()); COMMIT",
        "refusing to drop rates",
    ),
    "0080_dealing_orders": (
        "BEGIN; SELECT set_config('finvest.actor_kind', 'staff', true); "
        "SELECT set_config('finvest.actor_id', '-Nabc0000000000000B1', true); "
        "INSERT INTO dealing_orders (id, kind, product_code, currency, face_minor, prepared_by, "
        "prepared_at) VALUES ('-Nabc0000000000000A1', 'auction_bid', 'TREA-91', 'UGX', 100000, "
        "'-Nabc0000000000000B1', now()); COMMIT",
        "refusing to drop dealing orders",
    ),
    "0050_kyc": (
        "INSERT INTO kyc_checks (id, client_id, document_type, document_country, created_at, "
        "updated_at) VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', 'passport', 'UG', "
        "now(), now())",
        "refusing to drop identity checks",
    ),
    "0014_ledger_closed_days": (
        "INSERT INTO ledger_closed_days (closed_through, closed_by, closed_at) "
        "VALUES (DATE '2001-01-01', '-Nabc0000000000000S1', now())",
        "refusing to drop closed days",
    ),
    "0013_ledger_corrections": (
        "BEGIN; "
        "INSERT INTO ledger_accounts (id, code, kind, currency, name, created_at) VALUES "
        "('-Nabc0000000000000A1', 'test.cash', 'asset', 'UGX', 'Cash', now()), "
        "('-Nabc0000000000000B2', 'test.owed', 'liability', 'UGX', 'Owed', now()); "
        "INSERT INTO ledger_journal_entries (id, entry_type, currency, effective_date, "
        "posted_at, idempotency_key, request_digest) VALUES ('-Nabc0000000000000E1', "
        "'test.deposit', 'UGX', DATE '2026-11-13', now(), 'downgrade-test', repeat('0', 64)); "
        "INSERT INTO ledger_lines (entry_id, line_no, account_id, currency, amount_minor) VALUES "
        "('-Nabc0000000000000E1', 1, '-Nabc0000000000000A1', 'UGX', 5), "
        "('-Nabc0000000000000E1', 2, '-Nabc0000000000000B2', 'UGX', -5); "
        "INSERT INTO ledger_correction_proposals (id, corrects_entry_id, reverses, entry_type, "
        "currency, effective_date, lines, reason, proposed_by, proposed_at) VALUES "
        "('-Nabc0000000000000P1', '-Nabc0000000000000E1', true, 'ledger.correction', 'UGX', "
        "DATE '2026-11-13', '[{}, {}]'::jsonb, 'test', '-Nabc0000000000000S1', now()); "
        "COMMIT",
        "refusing to drop ledger corrections",
    ),
    "0005_identity": (
        "INSERT INTO identity_staff (id, email, name, role, created_at) "
        "VALUES ('-Nabc0000000000000A1', 'ada@example.com', 'Ada Nakato', 'admin', now())",
        "refusing to drop sign-in tables",
    ),
    "0020_payment_deposits": (
        "INSERT INTO payment_provider_notices (id, provider, notice_key, received_at) "
        "VALUES ('-Nabc0000000000000A1', 'sandbox', 'notice-1', now())",
        "refusing to drop payments",
    ),
    "0022_bank_transfer_deposits": (
        "INSERT INTO payment_deposits (id, client_id, purpose_type, purpose_id, method, provider, "
        "currency, amount_minor, status, created_at, updated_at) VALUES ('-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', 'investment', '-Nabc0000000000000C1', 'bank_transfer', 'bank', "
        "'UGX', 1000, 'in_review', now(), now())",
        "refusing to drop bank transfers",
    ),
    "0030_payout_destinations": (
        "INSERT INTO payout_destinations (id, client_id, kind, network, account_number, "
        "holder_name, status, created_at, updated_at) VALUES ('-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', 'mobile_money', 'mtn', '256772000000', 'Test', "
        "'awaiting_code', now(), now())",
        "refusing to drop payout destinations",
    ),
    "0031_payouts": (
        "INSERT INTO withdrawal_limits (id, effective_from, currency, mobile_money_per_payout, "
        "proposed_by, approved_by, reason, created_at) VALUES ('-Nabc0000000000000A1', "
        "DATE '2030-01-01', 'UGX', 1000000, '-Nabc0000000000000B1', '-Nabc0000000000000C1', "
        "'Test', now())",
        "refusing to drop payouts or limits",
    ),
    "0051_kyc_sealed_details": (
        "INSERT INTO kyc_checks (id, client_id, document_type, document_country, "
        "document_number_sealed, created_at, updated_at) VALUES ('-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', 'passport', 'UG', decode(repeat('00', 40), 'hex'), now(), now())",
        "refusing to drop sealed identity details",
    ),
    "0006_passkeys": (
        "INSERT INTO identity_passkeys (id, actor_kind, actor_id, credential_id, public_key, "
        "sign_count, transports, name, created_at) VALUES ('-Nabc0000000000000A1', 'staff', "
        "'-Nabc0000000000000S1', '\\x01', '\\x02', 0, '{}', 'Laptop', now())",
        "refusing to drop second steps",
    ),
    "0090_notification_inbox": (
        "INSERT INTO notification_preferences (client_id, category, channels, updated_at) "
        "VALUES ('-Nabc0000000000000B1', 'money', ARRAY['push'], now())",
        "refusing to drop notifications",
    ),
    "0007_notifications": (
        "INSERT INTO notification_messages (id, idempotency_key, channel, recipient, template, "
        "variables, created_at) VALUES ('-Nabc0000000000000A1', 'test:1', 'email', "
        "'ada@example.com', 'sign_in_code', '{}', now())",
        "refusing to drop messages",
    ),
    "0070_migration_checks": (
        "INSERT INTO migration_control_totals (id, as_of, totals, entered_by, entered_at) "
        "VALUES ('-Nabc0000000000000A1', DATE '2026-11-13', '{}', '-Nabc0000000000000S1', now())",
        "refusing to drop migration checks",
    ),
    "0040_reconciliation_statements": (
        "INSERT INTO reconciliation_statements (id, source, account_reference, business_date, "
        "currency, opening_minor, closing_minor, line_count, file_sha256, imported_by, "
        "imported_at) VALUES ('-Nabc0000000000000A1', 'bank', 'trust', DATE '2026-11-10', "
        "'UGX', 100, 100, 0, repeat('0', 64), '-Nabc0000000000000B1', now())",
        "refusing to drop imported statements",
    ),
    "0100_support": (
        "INSERT INTO support_conversations (id, client_id, created_at, last_message_at) "
        "VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', now(), now())",
        "refusing to drop help chat or complaints",
    ),
    "0071_migration_totals_confirmed": (
        "INSERT INTO migration_control_totals (id, as_of, totals, entered_by, entered_at) "
        "VALUES ('-Nabc0000000000000A1', DATE '2026-11-13', '{}', '-Nabc0000000000000S1', "
        "now()); INSERT INTO migration_control_totals_confirmations (totals_id, confirmed_by, "
        "confirmed_at) VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000S2', now())",
        "refusing to drop confirmations of migration totals",
    ),
}


@pytest.fixture
def scratch_database() -> Iterator[TestDatabase]:
    database = harness.create(f"finvest_test_downgrade_{os.getpid()}")
    try:
        yield database
    finally:
        harness.drop(database)


@pytest.mark.parametrize("revision", ROWS)
def test_a_downgrade_refuses_to_drop_a_table_that_holds_rows(
    scratch_database: TestDatabase, revision: str
) -> None:
    statement, refusal = ROWS[revision]
    assert harness.alembic(scratch_database, "upgrade", revision).returncode == 0
    migrator = scratch_database.admin.set(
        username="finvest_migrator",
        password=harness.PASSWORDS["finvest_migrator"],
        database=scratch_database.name,
    )
    harness.psql(migrator, "-c", statement)

    result = harness.alembic(scratch_database, "downgrade", "-1")
    assert result.returncode != 0
    assert refusal in result.stderr
    current = harness.alembic(scratch_database, "current")
    assert revision in current.stdout
    # The row is still there.
    table = statement.rsplit("INSERT INTO ", 1)[1].split()[0]
    harness.psql(
        migrator,
        "-c",
        f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM {table}) THEN "
        "RAISE EXCEPTION 'row was lost'; END IF; END $$",
    )


def test_details_saved_in_plain_columns_are_never_dropped_by_the_sealing_upgrade(
    scratch_database: TestDatabase,
) -> None:
    sealed = "0051_kyc_sealed_details"
    assert harness.alembic(scratch_database, "upgrade", sealed).returncode == 0
    assert harness.alembic(scratch_database, "downgrade", "-1").returncode == 0
    migrator = scratch_database.admin.set(
        username="finvest_migrator",
        password=harness.PASSWORDS["finvest_migrator"],
        database=scratch_database.name,
    )
    harness.psql(
        migrator,
        "-c",
        "INSERT INTO kyc_checks (id, client_id, document_type, document_country, "
        "document_number, created_at, updated_at) VALUES ('-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', 'passport', 'UG', 'B1234567', now(), now())",
    )

    result = harness.alembic(scratch_database, "upgrade", sealed)
    assert result.returncode != 0
    assert "refusing to drop identity details that were saved unsealed" in result.stderr
    harness.psql(
        migrator,
        "-c",
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM kyc_checks WHERE document_number = 'B1234567') "
        "THEN RAISE EXCEPTION 'details were lost'; END IF; END $$",
    )
