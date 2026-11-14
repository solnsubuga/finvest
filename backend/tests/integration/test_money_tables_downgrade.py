"""A downgrade must never delete the books, the issued numbers, the audit trail, the job history,
the receipts that stop a command running twice, the identity records, the payout destinations,
their two-person checks and network name lookups, the withdrawal limits, the payouts, their send
attempts, their resolutions and their bank batches, the migration checks, the imported
statements, the day closes and their recorded matches, the pot transfers, the help chat, the
complaints register, the goals, the email delivery records, the unclaimed accounts, the
complaint outcomes, the suppression lifts, the client locks, the announcements, the team
changes, Finvest's own money in the buffer, the payments of tax to URA, the payments moved to
the right client, next of kin, the answers to the investing questions, the requests to close an
account, the journals sent to the accounting system or who set the bank account clients pay
into."""

import os
from collections.abc import Iterator

import pytest

from tests.support import database as harness
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

ROWS = {
    "0182_transaction_monitoring": (
        "BEGIN; SELECT set_config('finvest.actor_kind', 'system', true); "
        "INSERT INTO monitoring_transactions (id, client_id, kind, source_id, currency, "
        "amount_minor, occurred_at, recorded_at) VALUES ('-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', 'deposit', '-Nabc0000000000000C1', 'UGX', 1000, now(), now()); "
        "COMMIT",
        "refusing to drop the monitoring record",
    ),
    "0181_payout_screening": (
        "INSERT INTO payout_destinations (id, client_id, kind, network, account_number, "
        "holder_name, status, created_at, updated_at) VALUES ('-Nabc0000000000000D1', "
        "'-Nabc0000000000000B1', 'mobile_money', 'mtn', '256772000000', 'Test', "
        "'awaiting_code', now(), now()); "
        "INSERT INTO payouts (id, client_id, destination_id, kind, amount_minor, currency, "
        "status, business_date, source_account_id, source_reference, in_flight_account_id, "
        "request_key, created_at, updated_at) VALUES ('-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', '-Nabc0000000000000D1', 'mobile_money', 1000, 'UGX', "
        "'screening', DATE '2026-11-10', '-Nabc0000000000000E1', 'investment-1', "
        "'-Nabc0000000000000F1', 'request-key-1', now(), now())",
        "refusing to downgrade while payouts wait for screening",
    ),
    "0180_screening": (
        "INSERT INTO screening_runs (id, client_id, reason, requested_at, idempotency_key) "
        "VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', 'rescreen', now(), 'k-0001')",
        "refusing to drop the screening and compliance record",
    ),
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
    "0110_goals": (
        "INSERT INTO goals (id, client_id, name, category, currency, target_minor, "
        "product_code, created_at, updated_at) VALUES ('-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', 'School fees', 'school_fees', 'UGX', 2000000, 'TREA-91', "
        "now(), now())",
        "refusing to drop goals",
    ),
    "0120_autoinvest_plans": (
        "BEGIN; SELECT set_config('finvest.actor_kind', 'client', true); "
        "SELECT set_config('finvest.actor_id', '-Nabc0000000000000B1', true); "
        "INSERT INTO autoinvest_plans (id, client_id, product_code, currency, amount_minor, "
        "frequency, run_day, network, payer_msisdn, status, next_run_on, created_at, "
        "updated_at) VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', 'TREA-91', 'UGX', "
        "100000, 'monthly', 28, 'mtn', '+256772000000', 'active', DATE '2030-01-28', now(), "
        "now()); COMMIT",
        "refusing to drop automatic investing plans",
    ),
    "0111_goal_rollovers": (
        "BEGIN; SELECT set_config('finvest.actor_kind', 'system', true); "
        "INSERT INTO goals (id, client_id, name, category, currency, target_minor, "
        "product_code, created_at, updated_at) VALUES ('-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', 'School fees', 'school_fees', 'UGX', 2000000, 'TREA-91', "
        "now(), now()); "
        "INSERT INTO goal_savings (investment_id, goal_id, client_id, created_at) VALUES "
        "('-Nabc0000000000000C1', '-Nabc0000000000000A1', '-Nabc0000000000000B1', now()); "
        "INSERT INTO goal_savings (investment_id, goal_id, client_id, created_at, "
        "rolled_from_investment_id) VALUES ('-Nabc0000000000000C2', '-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', now(), '-Nabc0000000000000C1'); COMMIT",
        "refusing to drop the rollovers of goal savings",
    ),
    "0112_goal_groups": (
        "INSERT INTO goal_groups (id, owner_client_id, name, category, currency, target_minor, "
        "created_at, updated_at) VALUES ('-Nabc0000000000000G1', '-Nabc0000000000000B1', "
        "'Land', 'land_or_home', 'UGX', 10000000, now(), now())",
        "refusing to drop group goals",
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
    "0081_dealing_holdings": (
        "BEGIN; SELECT set_config('finvest.actor_kind', 'staff', true); "
        "SELECT set_config('finvest.actor_id', '-Nabc0000000000000B1', true); "
        "INSERT INTO dealing_orders (id, kind, product_code, currency, face_minor, prepared_by, "
        "prepared_at) VALUES ('-Nabc0000000000000A1', 'auction_bid', 'TREA-91', 'UGX', 100000, "
        "'-Nabc0000000000000B1', now()); "
        "SELECT set_config('finvest.actor_id', '-Nabc0000000000000C1', true); "
        "UPDATE dealing_orders SET status = 'approved', decided_by = '-Nabc0000000000000C1', "
        "decided_at = now(); "
        "UPDATE dealing_orders SET status = 'result_recorded', result_face_minor = 100000, "
        "result_amount_minor = 97000, result_settled_on = DATE '2026-11-19', "
        "result_matures_on = DATE '2027-02-18', custodian_reference = 'CSD-1', "
        "result_recorded_by = '-Nabc0000000000000C1', result_recorded_at = now(); "
        "SELECT set_config('finvest.actor_id', '-Nabc0000000000000D1', true); "
        "INSERT INTO dealing_lots (id, order_id, product_code, currency, face_minor, cost_minor, "
        "settled_on, matures_on, custodian_reference) VALUES ('-Nabc0000000000000L1', "
        "'-Nabc0000000000000A1', 'TREA-91', 'UGX', 100000, 97000, DATE '2026-11-19', "
        "DATE '2027-02-18', 'CSD-1'); COMMIT",
        "refusing to drop bills held or settled orders",
    ),
    # The owner turns the guards off for this one transaction: a real maturity needs a settled
    # purchase and its ledger entries, which is more than this test is about.
    "0082_dealing_maturities": (
        "BEGIN; "
        "ALTER TABLE dealing_orders DISABLE TRIGGER dealing_orders_guard; "
        "ALTER TABLE dealing_lots DISABLE TRIGGER dealing_lots_guard; "
        "ALTER TABLE dealing_lot_maturities DISABLE TRIGGER dealing_lot_maturities_guard; "
        "INSERT INTO dealing_orders (id, kind, product_code, currency, face_minor, prepared_by, "
        "prepared_at) VALUES ('-Nabc0000000000000A1', 'auction_bid', 'TREA-91', 'UGX', 100000, "
        "'-Nabc0000000000000B1', now()); "
        "INSERT INTO dealing_lots (id, order_id, product_code, currency, face_minor, cost_minor, "
        "settled_on, matures_on, custodian_reference) VALUES ('-Nabc0000000000000L1', "
        "'-Nabc0000000000000A1', 'TREA-91', 'UGX', 100000, 97000, DATE '2026-11-19', "
        "DATE '2027-02-18', 'CSD-1'); "
        "INSERT INTO dealing_lot_maturities (lot_id, matured_on, face_minor, cost_minor, "
        "ledger_entry_id, recorded_at) VALUES ('-Nabc0000000000000L1', DATE '2027-02-18', "
        "100000, 97000, '-Nabc0000000000000E1', now()); "
        "ALTER TABLE dealing_orders ENABLE TRIGGER dealing_orders_guard; "
        "ALTER TABLE dealing_lots ENABLE TRIGGER dealing_lots_guard; "
        "ALTER TABLE dealing_lot_maturities ENABLE TRIGGER dealing_lot_maturities_guard; "
        "COMMIT",
        "refusing to drop bill maturities",
    ),
    "0083_dealing_buffer_rules": (
        "BEGIN; SELECT set_config('finvest.actor_kind', 'staff', true); "
        "SELECT set_config('finvest.actor_id', '-Nabc0000000000000B1', true); "
        "INSERT INTO dealing_buffer_rule_proposals (id, currency, effective_from, target_minor, "
        "floor_minor, from_buffer_up_to_minor, pay_within_working_days, reason, proposed_by, "
        "proposed_at) VALUES ('-Nabc0000000000000A1', 'UGX', DATE '2027-01-01', 3, 2, 1, 2, "
        "'Test', '-Nabc0000000000000B1', now()); COMMIT",
        "refusing to drop buffer rules that staff proposed or approved",
    ),
    "0084_dealing_withdrawal_funding": (
        "BEGIN; SELECT set_config('finvest.actor_kind', 'system', true); "
        "INSERT INTO dealing_withdrawal_fundings (id, investment_id, currency, amount_minor, "
        "source, status, asked_at) VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', "
        "'UGX', 100000, 'sale', 'waiting', now()); COMMIT",
        "refusing to drop withdrawal fundings",
    ),
    # As for 0082, the owner turns the guards off: a real lot needs a settled purchase.
    "0085_dealing_allocations": (
        "BEGIN; "
        "ALTER TABLE dealing_orders DISABLE TRIGGER dealing_orders_guard; "
        "ALTER TABLE dealing_lots DISABLE TRIGGER dealing_lots_guard; "
        "ALTER TABLE dealing_allocations DISABLE TRIGGER dealing_allocations_guard; "
        "INSERT INTO dealing_orders (id, kind, product_code, currency, face_minor, prepared_by, "
        "prepared_at) VALUES ('-Nabc0000000000000A1', 'auction_bid', 'TREA-91', 'UGX', 100000, "
        "'-Nabc0000000000000B1', now()); "
        "INSERT INTO dealing_lots (id, order_id, product_code, currency, face_minor, cost_minor, "
        "settled_on, matures_on, custodian_reference) VALUES ('-Nabc0000000000000L1', "
        "'-Nabc0000000000000A1', 'TREA-91', 'UGX', 100000, 97000, DATE '2026-11-19', "
        "DATE '2027-02-18', 'CSD-1'); "
        "INSERT INTO dealing_allocations (id, investment_id, lot_id, cost_minor, allocated_at) "
        "VALUES ('-Nabc0000000000000M1', '-Nabc0000000000000I1', '-Nabc0000000000000L1', 97000, "
        "now()); "
        "ALTER TABLE dealing_orders ENABLE TRIGGER dealing_orders_guard; "
        "ALTER TABLE dealing_lots ENABLE TRIGGER dealing_lots_guard; "
        "ALTER TABLE dealing_allocations ENABLE TRIGGER dealing_allocations_guard; "
        "COMMIT",
        "refusing to drop allocations",
    ),
    "0170_tax_remittances": (
        "BEGIN; SELECT set_config('finvest.actor_kind', 'staff', true); "
        "SELECT set_config('finvest.actor_id', '-Nabc0000000000000B1', true); "
        "INSERT INTO tax_remittances (id, month, currency, amount_minor, ura_reference, "
        "bank_reference, paid_on, note, status, proposed_by, proposed_at) VALUES "
        "('-Nabc0000000000000A1', DATE '2026-10-01', 'UGX', 100000, 'PRN1', 'FT1', "
        "DATE '2026-11-12', 'Test', 'proposed', '-Nabc0000000000000B1', now()); COMMIT",
        "refusing to drop tax remittances",
    ),
    # A reassignment cannot be saved without a confirmed, reviewed transfer and its ledger entry,
    # so the row is the history event an applied one leaves on the right client's new deposit.
    "0171_payment_reassignments": (
        "INSERT INTO payment_deposits (id, client_id, purpose_type, purpose_id, method, provider, "
        "currency, amount_minor, status, created_at, updated_at) VALUES ('-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', 'investment', '-Nabc0000000000000C1', 'bank_transfer', 'bank', "
        "'UGX', 1000, 'in_review', now(), now()); "
        "INSERT INTO payment_deposit_events (id, deposit_id, occurred_at, from_status, to_status, "
        "cause) VALUES ('-Nabc0000000000000E1', '-Nabc0000000000000A1', now(), NULL, "
        "'confirmed', 'reassignment')",
        "refusing to drop the record of reassigned deposits",
    ),
    "0250_finance_journal_exports": (
        "BEGIN; SELECT set_config('finvest.actor_kind', 'staff', true); "
        "SELECT set_config('finvest.actor_id', '-Nabc0000000000000B1', true); "
        "INSERT INTO finance_journal_exports (id, currency, date_from, date_to, lines, "
        "debit_total_minor, exported_by, exported_at) VALUES ('-Nabc0000000000000A1', 'UGX', "
        "DATE '2026-11-12', DATE '2026-11-12', 4, 100000, '-Nabc0000000000000B1', now()); COMMIT",
        "refusing to drop journal export records",
    ),
    "0087_dealing_buffer_capital": (
        "BEGIN; SELECT set_config('finvest.actor_kind', 'staff', true); "
        "SELECT set_config('finvest.actor_id', '-Nabc0000000000000B1', true); "
        "INSERT INTO dealing_buffer_capital (id, direction, currency, amount_minor, "
        "bank_reference, value_date, reason, status, proposed_by, proposed_at) VALUES "
        "('-Nabc0000000000000A1', 'in', 'UGX', 100000, 'FT1', DATE '2026-11-13', 'Test', "
        "'proposed', '-Nabc0000000000000B1', now()); COMMIT",
        "refusing to drop buffer capital movements",
    ),
    # The owner turns the guards off: a real funding from a sale needs a settled sale.
    "0086_dealing_sale_funding": (
        "BEGIN; "
        "ALTER TABLE dealing_orders DISABLE TRIGGER dealing_orders_guard; "
        "ALTER TABLE dealing_withdrawal_fundings "
        "DISABLE TRIGGER dealing_withdrawal_fundings_guard; "
        "INSERT INTO dealing_orders (id, kind, product_code, currency, face_minor, prepared_by, "
        "prepared_at) VALUES ('-Nabc0000000000000A1', 'secondary_sale', 'TREA-91', 'UGX', "
        "100000, '-Nabc0000000000000B1', now()); "
        "INSERT INTO dealing_withdrawal_fundings (id, investment_id, currency, amount_minor, "
        "source, status, asked_at, funded_at, sale_order_id) VALUES ('-Nabc0000000000000F1', "
        "'-Nabc0000000000000I1', 'UGX', 100000, 'sale', 'funded', now(), now(), "
        "'-Nabc0000000000000A1'); "
        "ALTER TABLE dealing_orders ENABLE TRIGGER dealing_orders_guard; "
        "ALTER TABLE dealing_withdrawal_fundings "
        "ENABLE TRIGGER dealing_withdrawal_fundings_guard; "
        "COMMIT",
        "refusing to restore staff funding over withdrawals the worker funded",
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
    "0190_payout_name_lookups": (
        "INSERT INTO payout_destinations (id, client_id, kind, network, account_number, "
        "holder_name, status, created_at, updated_at) VALUES ('-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', 'mobile_money', 'mtn', '256772000000', 'Test', "
        "'awaiting_proof', now(), now()); "
        "INSERT INTO payout_name_lookups (id, destination_id, number, status, created_at, "
        "updated_at) VALUES ('-Nabc0000000000000C1', '-Nabc0000000000000A1', 1, 'queued', "
        "now(), now())",
        "refusing to drop the network name lookups of payout destinations",
    ),
    "0031_payouts": (
        "INSERT INTO withdrawal_limits (id, effective_from, currency, mobile_money_per_payout, "
        "proposed_by, approved_by, reason, created_at) VALUES ('-Nabc0000000000000A1', "
        "DATE '2030-01-01', 'UGX', 1000000, '-Nabc0000000000000B1', '-Nabc0000000000000C1', "
        "'Test', now())",
        "refusing to drop payouts or limits",
    ),
    "0033_payout_send_attempts": (
        "INSERT INTO payout_destinations (id, client_id, kind, network, account_number, "
        "holder_name, status, created_at, updated_at) VALUES ('-Nabc0000000000000D1', "
        "'-Nabc0000000000000B1', 'mobile_money', 'mtn', '256772000000', 'Test', "
        "'awaiting_code', now(), now()); "
        "INSERT INTO payouts (id, client_id, destination_id, kind, amount_minor, currency, "
        "status, business_date, source_account_id, source_reference, in_flight_account_id, "
        "request_key, created_at, updated_at) VALUES ('-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', '-Nabc0000000000000D1', 'mobile_money', 1000, 'UGX', 'queued', "
        "DATE '2026-11-10', '-Nabc0000000000000E1', 'investment-1', '-Nabc0000000000000F1', "
        "'request-key-1', now(), now()); "
        "INSERT INTO payout_send_attempts (payout_id, attempt, provider, started_at) "
        "VALUES ('-Nabc0000000000000A1', 1, 'sandbox', now())",
        "refusing to drop payout send attempts",
    ),
    # The owner turns the guard off for this one transaction: a real request needs a running
    # investment, which needs a confirmed payment and its ledger entries.
    "0064_investment_early_withdrawal": (
        "BEGIN; ALTER TABLE investments DISABLE TRIGGER USER; "
        "INSERT INTO investments (id, client_id, product_id, currency, principal_minor, "
        "annual_rate_bps, tax_bps, term_days, at_maturity, created_at, pay_by, updated_at, "
        "withdrawal_requested_at, withdrawal_destination_id) "
        "VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', 'FvProduct1TREA91_001', "
        "'UGX', 1000000, 1250, 1500, 91, 'roll_over', now(), now() + interval '72 hours', "
        "now(), now(), '-Nabc0000000000000D1'); "
        "ALTER TABLE investments ENABLE TRIGGER USER; COMMIT",
        "refusing to drop the record of early withdrawals",
    ),
    "0065_investment_carried_over": (
        "BEGIN; ALTER TABLE investments DISABLE TRIGGER USER; "
        "INSERT INTO investments (id, client_id, product_id, currency, principal_minor, "
        "annual_rate_bps, tax_bps, term_days, at_maturity, created_at, pay_by, updated_at, "
        "legacy_reference) "
        "VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', 'FvProduct1TREA91_001', "
        "'UGX', 1000000, 1250, 1500, 91, 'roll_over', now(), now() + interval '72 hours', "
        "now(), 'FV1-INV-0001'); "
        "ALTER TABLE investments ENABLE TRIGGER USER; COMMIT",
        "refusing to drop the record of investments carried over",
    ),
    "0066_investment_corrections": (
        "BEGIN; ALTER TABLE investments DISABLE TRIGGER USER; "
        "ALTER TABLE investment_corrections DISABLE TRIGGER USER; "
        "INSERT INTO investments (id, client_id, product_id, currency, principal_minor, "
        "annual_rate_bps, tax_bps, term_days, at_maturity, created_at, pay_by, updated_at) "
        "VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', 'FvProduct1TREA91_001', "
        "'UGX', 1000000, 1250, 1500, 91, 'roll_over', now(), now() + interval '72 hours', "
        "now()); "
        "INSERT INTO investment_corrections (id, kind, investment_id, client_id, currency, "
        "amount_minor, reason, proposed_by, proposed_at) "
        "VALUES ('-Nabc0000000000000C1', 'unwind', '-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', 'UGX', 1000000, 'Credited to the wrong client', "
        "'-Nabc0000000000000E1', now()); "
        "ALTER TABLE investment_corrections ENABLE TRIGGER USER; "
        "ALTER TABLE investments ENABLE TRIGGER USER; COMMIT",
        "refusing to drop the record of investment corrections",
    ),
    "0034_payout_account_codes": (
        "INSERT INTO identity_codes (id, purpose, channel, phone, actor_id, bound_to, "
        "code_hash, created_at, expires_at) VALUES ('-Nabc0000000000000A1', 'payout_account', "
        "'sms', '+256772123456', '-Nabc0000000000000B1', '-Nabc0000000000000C1', "
        "decode(repeat('00', 32), 'hex'), now(), now())",
        "refusing to drop payout account codes",
    ),
    "0035_payout_account_two_people": (
        "INSERT INTO payout_destinations (id, client_id, kind, network, account_number, "
        "holder_name, status, proof_kind, proof_name, name_match, checked_by, checked_at, "
        "created_at, updated_at) VALUES ('-Nabc0000000000000D1', '-Nabc0000000000000B1', "
        "'mobile_money', 'mtn', '256772000000', 'Test', 'in_review', 'network_registered_name', "
        "'Test', 'exact', '-Nabc0000000000000S1', now(), now(), now())",
        "refusing to drop the checks and approvals of payout destinations",
    ),
    "0037_payout_resolutions": (
        "INSERT INTO payout_destinations (id, client_id, kind, network, account_number, "
        "holder_name, status, created_at, updated_at) VALUES ('-Nabc0000000000000D1', "
        "'-Nabc0000000000000B1', 'mobile_money', 'mtn', '256772000000', 'Test', "
        "'awaiting_code', now(), now()); "
        "INSERT INTO payouts (id, client_id, destination_id, kind, amount_minor, currency, "
        "status, business_date, source_account_id, source_reference, in_flight_account_id, "
        "request_key, created_at, updated_at) VALUES ('-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', '-Nabc0000000000000D1', 'mobile_money', 1000, 'UGX', 'queued', "
        "DATE '2026-11-10', '-Nabc0000000000000E1', 'investment-1', '-Nabc0000000000000F1', "
        "'request-key-1', now(), now()); "
        "INSERT INTO reconciliation_statements (id, source, account_reference, business_date, "
        "currency, opening_minor, closing_minor, line_count, file_sha256, imported_by, "
        "imported_at) VALUES ('-Nabc0000000000000C1', 'iotec', 'mtn', DATE '2026-11-10', "
        "'UGX', 100, 100, 0, repeat('0', 64), '-Nabc0000000000000B1', now()); "
        # The guard admits only staff proposing with the statement as evidence.
        "ALTER TABLE payout_resolutions DISABLE TRIGGER payout_resolutions_guard; "
        "INSERT INTO payout_resolutions (id, payout_id, outcome, statement_id, reason, status, "
        "proposed_by, proposed_at) VALUES ('-Nabc0000000000000R1', '-Nabc0000000000000A1', "
        "'failed', '-Nabc0000000000000C1', 'Checked', 'proposed', '-Nabc0000000000000S1', now())",
        "refusing to drop the resolutions of payouts",
    ),
    "0038_payout_releases": (
        "INSERT INTO payout_destinations (id, client_id, kind, network, account_number, "
        "holder_name, status, created_at, updated_at) VALUES ('-Nabc0000000000000D1', "
        "'-Nabc0000000000000B1', 'mobile_money', 'mtn', '256772000000', 'Test', "
        "'awaiting_code', now(), now()); "
        "INSERT INTO payouts (id, client_id, destination_id, kind, amount_minor, currency, "
        "status, business_date, source_account_id, source_reference, in_flight_account_id, "
        "request_key, created_at, updated_at) VALUES ('-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', '-Nabc0000000000000D1', 'mobile_money', 1000, 'UGX', 'held', "
        "DATE '2026-11-10', '-Nabc0000000000000E1', 'investment-1', '-Nabc0000000000000F1', "
        "'request-key-1', now(), now()); "
        # The guard admits only staff proposing, by the person signed in.
        "ALTER TABLE payout_releases DISABLE TRIGGER payout_releases_guard; "
        "INSERT INTO payout_releases (id, payout_id, reason, status, proposed_by, proposed_at) "
        "VALUES ('-Nabc0000000000000R1', '-Nabc0000000000000A1', 'Checked', 'proposed', "
        "'-Nabc0000000000000S1', now())",
        "refusing to drop the releases of payouts",
    ),
    "0039_payout_bank_batches": (
        # The guard admits only a member of staff preparing in their own name, now.
        "ALTER TABLE payout_bank_batches DISABLE TRIGGER payout_bank_batches_guard; "
        "INSERT INTO payout_bank_batches (id, currency, reason, status, prepared_by, "
        "prepared_at) VALUES ('-Nabc0000000000000A1', 'UGX', 'Today', 'prepared', "
        "'-Nabc0000000000000S1', now())",
        "refusing to drop the batches of bank payouts",
    ),
    "0044_payout_returns": (
        "INSERT INTO payout_destinations (id, client_id, kind, network, account_number, "
        "holder_name, status, created_at, updated_at) VALUES ('-Nabc0000000000000D1', "
        "'-Nabc0000000000000B1', 'mobile_money', 'mtn', '256772000000', 'Test', "
        "'awaiting_code', now(), now()); "
        "INSERT INTO payouts (id, client_id, destination_id, kind, amount_minor, currency, "
        "status, business_date, source_account_id, source_reference, in_flight_account_id, "
        "request_key, created_at, updated_at) VALUES ('-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', '-Nabc0000000000000D1', 'mobile_money', 1000, 'UGX', 'queued', "
        "DATE '2026-11-10', '-Nabc0000000000000E1', 'investment-1', '-Nabc0000000000000F1', "
        "'request-key-1', now(), now()); "
        "INSERT INTO reconciliation_statements (id, source, account_reference, business_date, "
        "currency, opening_minor, closing_minor, line_count, file_sha256, imported_by, "
        "imported_at) VALUES ('-Nabc0000000000000C1', 'iotec', 'mtn', DATE '2026-11-10', "
        "'UGX', 100, 1100, 1, repeat('0', 64), '-Nabc0000000000000B1', now()); "
        "INSERT INTO reconciliation_statement_lines (id, statement_id, line_no, value_date, "
        "direction, amount_minor, reference, narrative, fingerprint) VALUES "
        "('-Nabc0000000000000L1', '-Nabc0000000000000C1', 1, DATE '2026-11-10', 'in', 1000, "
        "'r', 'n', repeat('0', 64)); "
        # The guard admits only staff proposing with the statement line as evidence.
        "ALTER TABLE payout_returns DISABLE TRIGGER payout_returns_guard; "
        "INSERT INTO payout_returns (id, payout_id, client_id, statement_line_id, reason, "
        "status, proposed_by, proposed_at) VALUES ('-Nabc0000000000000R1', "
        "'-Nabc0000000000000A1', '-Nabc0000000000000B1', '-Nabc0000000000000L1', 'Came back', "
        "'proposed', '-Nabc0000000000000S1', now())",
        "refusing to drop the returns of payouts",
    ),
    "0051_kyc_sealed_details": (
        "INSERT INTO kyc_checks (id, client_id, document_type, document_country, "
        "document_number_sealed, created_at, updated_at) VALUES ('-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', 'passport', 'UG', decode(repeat('00', 40), 'hex'), now(), now())",
        "refusing to drop sealed identity details",
    ),
    "0052_kyc_carried_over": (
        "INSERT INTO account_numbers (number, issued_at) VALUES ('1001004827', now()); "
        "INSERT INTO kyc_checks (id, client_id, status, document_type, document_country, "
        "surname, given_names, document_number_sealed, date_of_birth_sealed, "
        "document_fingerprint, details_confirmed_at, decided_at, account_number, source, "
        "legacy_reference, created_at, updated_at) VALUES ('-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', 'approved', 'national_id', 'UG', 'NAKATO', 'Amara', "
        "decode(repeat('00', 40), 'hex'), decode(repeat('00', 40), 'hex'), repeat('0', 64), "
        "now(), now(), '1001004827', 'finvest1', 'FV1-0001', now(), now())",
        "refusing to drop identity checks carried over from Finvest 1.0",
    ),
    "0053_kyc_claim_checks": (
        "INSERT INTO account_numbers (number, issued_at) VALUES ('1001004827', now()); "
        "INSERT INTO kyc_checks (id, client_id, status, document_type, document_country, "
        "surname, given_names, document_number_sealed, date_of_birth_sealed, "
        "document_fingerprint, details_confirmed_at, decided_at, account_number, source, "
        "legacy_reference, created_at, updated_at) VALUES ('-Nabc0000000000000A1', "
        "'-Nabc0000000000000B1', 'approved', 'national_id', 'UG', 'NAKATO', 'Amara', "
        "decode(repeat('00', 40), 'hex'), decode(repeat('00', 40), 'hex'), repeat('0', 64), "
        "now(), now(), '1001004827', 'finvest1', 'FV1-0001', now(), now()); "
        "INSERT INTO kyc_claim_checks (id, client_id, check_id, created_at, updated_at) "
        "VALUES ('-Nabc0000000000000C1', '-Nabc0000000000000B1', '-Nabc0000000000000A1', "
        "now(), now())",
        "refusing to drop claim checks that hold records",
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
    "0102_announcements": (
        # The guard admits only a member of staff proposing in their own name.
        "WITH acting AS (SELECT set_config('finvest.actor_kind', 'staff', true), "
        "set_config('finvest.actor_id', '-Nabc0000000000000A1', true)) "
        "INSERT INTO notification_announcements (id, title, message, reason, proposed_by, "
        "proposed_at) SELECT '-Nabc0000000000000A1', 'T', 'M', 'R', '-Nabc0000000000000A1', now() "
        "FROM acting",
        "refusing to drop announcements",
    ),
    "0092_email_delivery": (
        "INSERT INTO notification_email_events (svix_id, delivery, email_id, recipients, "
        "permanent, received_at) VALUES ('msg_test1', 'delivered', 'email-1', '{}', false, now())",
        "refusing to drop email delivery records",
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
    "0101_complaint_outcomes": (
        "INSERT INTO complaints (id, client_id, category, received_via, summary, details, "
        "received_at, due_on, logged_by_kind, logged_by, client_key, updated_at) VALUES "
        "('-Nabc0000000000000A1', '-Nabc0000000000000B1', 'other', 'app', 'S', 'D', now(), "
        "current_date, 'client', '-Nabc0000000000000B1', 'complaint-key-1', now())",
        "refusing to drop complaint outcomes or keys",
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
    "0041_reconciliation_day_close": (
        "INSERT INTO reconciliation_movements (entry_id, line_no, source, account_reference, "
        "effective_date, currency, amount_minor, reference, entry_type, recorded_at) VALUES "
        "('-Nabc0000000000000A1', 1, 'bank', 'trust', DATE '2026-11-10', 'UGX', 5, '', "
        "'test.movement', now())",
        "refusing to drop reconciliation records",
    ),
    "0008_codes": (
        "INSERT INTO identity_codes (id, purpose, channel, phone, code_hash, created_at, "
        "expires_at) VALUES ('-Nabc0000000000000A1', 'signup', 'sms', '+256772123456', "
        "decode(repeat('00', 32), 'hex'), now(), now())",
        "refusing to drop sign-in codes",
    ),
    "0140_documents": (
        "INSERT INTO documents (id, client_id, kind, idempotency_key, period_start, period_end, "
        "requested_at) VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', "
        "'monthly_statement', '2026-10', DATE '2026-10-01', DATE '2026-10-31', now())",
        "refusing to drop documents",
    ),
    "0142_tax_certificates": (
        "INSERT INTO documents (id, client_id, kind, idempotency_key, period_start, period_end, "
        "requested_at) VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', "
        "'tax_certificate', '2026-27', DATE '2026-07-01', DATE '2027-06-30', now())",
        "refusing to drop tax certificates",
    ),
    "0141_document_emails": (
        # Only the worker queues an email, and only for a ready document. The migrator owns
        # both tables, so it sets their guards aside to write the row this test needs.
        "BEGIN; "
        "INSERT INTO documents (id, client_id, kind, idempotency_key, period_start, period_end, "
        "requested_at) VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', "
        "'monthly_statement', '2026-10', DATE '2026-10-01', DATE '2026-10-31', now()); "
        "ALTER TABLE documents DISABLE TRIGGER documents_guard; "
        "UPDATE documents SET status = 'ready', finished_at = now(), file_name = 'a.pdf', "
        "content = 'abc', sha256 = sha256('abc'), byte_size = 3; "
        "ALTER TABLE documents ENABLE TRIGGER documents_guard; "
        "ALTER TABLE document_emails DISABLE TRIGGER document_emails_guard; "
        "INSERT INTO document_emails (document_id, client_id, queued_at) VALUES "
        "('-Nabc0000000000000A1', '-Nabc0000000000000B1', now()); "
        "ALTER TABLE document_emails ENABLE TRIGGER document_emails_guard; COMMIT",
        "refusing to drop document emails",
    ),
    "0042_pot_transfers": (
        "INSERT INTO reconciliation_pot_transfers (id, from_source, from_reference, to_source, "
        "to_reference, currency, sent_minor, received_minor, value_date, provider_reference, "
        "status, proposed_by, proposed_at, reason) VALUES ('-Nabc0000000000000A1', 'iotec', "
        "'mtn', 'bank', 'trust', 'UGX', 5, 5, DATE '2026-11-10', 'IOT-1', 'proposed', "
        "'-Nabc0000000000000B1', now(), 'Sweep')",
        "refusing to drop pot transfers",
    ),
    "0043_reconciliation_recorded": (
        "INSERT INTO reconciliation_statements (id, source, account_reference, business_date, "
        "currency, opening_minor, closing_minor, line_count, file_sha256, imported_by, "
        "imported_at) VALUES ('-Nabc0000000000000A1', 'bank', 'trust', DATE '2026-11-10', "
        "'UGX', 0, 5, 1, repeat('0', 64), '-Nabc0000000000000B1', now()); "
        "INSERT INTO reconciliation_statement_lines (id, statement_id, line_no, value_date, "
        "direction, amount_minor, reference, narrative, fingerprint) VALUES "
        "('-Nabc0000000000000L1', '-Nabc0000000000000A1', 1, DATE '2026-11-10', 'in', 5, "
        "'FT1', 'Transfer', repeat('1', 64)); "
        "INSERT INTO reconciliation_movements (entry_id, line_no, source, account_reference, "
        "effective_date, currency, amount_minor, reference, entry_type, recorded_at) VALUES "
        "('-Nabc0000000000000E1', 1, 'bank', 'trust', DATE '2026-11-11', 'UGX', 5, 'deposit-1', "
        "'payment.deposit_confirmed', now()); "
        "INSERT INTO reconciliation_matches (statement_line_id, entry_id, line_no, how, "
        "matched_by, reason, matched_at) VALUES ('-Nabc0000000000000L1', "
        "'-Nabc0000000000000E1', 1, 'recorded', '-Nabc0000000000000S1', 'Review', now())",
        "refusing to drop recorded matches",
    ),
    "0153_email_suppression_lifts": (
        "BEGIN; SELECT set_config('finvest.actor_kind', 'staff', true); "
        "SELECT set_config('finvest.actor_id', '-Nabc0000000000000S1', true); "
        "INSERT INTO notification_email_events (svix_id, delivery, email_id, recipients, "
        "permanent, received_at) VALUES ('msg_lift1', 'bounced', 'email-1', "
        "ARRAY[repeat('a', 64)], true, now()); "
        "INSERT INTO notification_email_suppressions (address_hash, reason, svix_id, created_at) "
        "VALUES (repeat('a', 64), 'bounced', 'msg_lift1', now()); "
        "INSERT INTO notification_email_suppression_lifts (id, address_hash, reason, "
        "proposed_by, proposed_at) VALUES ('-Nabc0000000000000A1', repeat('a', 64), "
        "'Fixed their inbox', '-Nabc0000000000000S1', now()); COMMIT",
        "refusing to drop suppression lifts",
    ),
    "0009_claims": (
        "INSERT INTO identity_clients (id, phone, status, created_at) VALUES "
        "('-Nabc0000000000000A1', '+256772123456', 'unclaimed', now())",
        "refusing to drop unclaimed accounts or claim codes",
    ),
    "0072_migration_claims": (
        "INSERT INTO migration_claims (client_id, legacy_client_ref, needs_id_check, status, "
        "created_at, updated_at) VALUES ('-Nabc0000000000000A1', 'CL-1', false, 'not_invited', "
        "now(), now())",
        "refusing to drop migration claims",
    ),
    "0073_migration_difference_places": (
        "INSERT INTO migration_trials (id, as_of, clients_sha256, investments_sha256, "
        "error_count, warning_count, problems, summary, new_totals, differing, "
        "difference_places, ran_by, ran_at) VALUES ('-Nabc0000000000000A1', DATE '2026-11-13', "
        "repeat('0', 64), repeat('0', 64), 0, 0, '[]', '{}', '{}', '{}', "
        '\'{"investment:A": {"row": 2, "currency": "UGX"}}\', '
        "'-Nabc0000000000000S1', now())",
        "refusing to drop the sheet rows of migration differences",
    ),
    "0074_migration_switch": (
        "INSERT INTO migration_trials (id, as_of, clients_sha256, investments_sha256, "
        "error_count, warning_count, problems, summary, new_totals, differing, ran_by, ran_at) "
        "VALUES ('-Nabc0000000000000A1', DATE '2026-11-13', repeat('0', 64), repeat('0', 64), "
        "0, 0, '[]', '{}', '{}', '{}', '-Nabc0000000000000S1', now()); "
        "INSERT INTO migration_switches (id, trial_id, as_of, clients_sha256, "
        "investments_sha256, proposed_by, proposed_at) VALUES ('-Nabc0000000000000W1', "
        "'-Nabc0000000000000A1', DATE '2026-11-13', repeat('0', 64), repeat('0', 64), "
        "'-Nabc0000000000000S1', now())",
        "refusing to drop the record of the switch",
    ),
    "0075_migration_carry_problems": (
        "INSERT INTO migration_trials (id, as_of, clients_sha256, investments_sha256, "
        "error_count, warning_count, problems, summary, new_totals, differing, ran_by, ran_at) "
        "VALUES ('-Nabc0000000000000A1', DATE '2026-11-13', repeat('0', 64), repeat('0', 64), "
        "0, 0, '[]', '{}', '{}', '{}', '-Nabc0000000000000S1', now()); "
        "INSERT INTO migration_switches (id, trial_id, as_of, clients_sha256, "
        "investments_sha256, proposed_by, proposed_at) VALUES ('-Nabc0000000000000W1', "
        "'-Nabc0000000000000A1', DATE '2026-11-13', repeat('0', 64), repeat('0', 64), "
        "'-Nabc0000000000000S1', now()); "
        "INSERT INTO migration_switch_decisions (switch_id, outcome, decided_by, decided_at) "
        "VALUES ('-Nabc0000000000000W1', 'approved', '-Nabc0000000000000S2', now()); "
        "INSERT INTO migration_carried_clients (legacy_client_ref, switch_id, client_id, "
        "carried_by, carried_at) VALUES ('1', '-Nabc0000000000000W1', '-Nabc0000000000000C1', "
        "'-Nabc0000000000000S2', now()); "
        "INSERT INTO migration_carry_problems (switch_id, legacy_client_ref, code, noted_at) "
        "VALUES ('-Nabc0000000000000W1', '1', 'document_taken', now())",
        "refusing to drop the clients the switch could not carry over",
    ),
    "0076_migration_carry_resolutions": (
        "INSERT INTO migration_trials (id, as_of, clients_sha256, investments_sha256, "
        "error_count, warning_count, problems, summary, new_totals, differing, ran_by, ran_at) "
        "VALUES ('-Nabc0000000000000A1', DATE '2026-11-13', repeat('0', 64), repeat('0', 64), "
        "0, 0, '[]', '{}', '{}', '{}', '-Nabc0000000000000S1', now()); "
        "INSERT INTO migration_switches (id, trial_id, as_of, clients_sha256, "
        "investments_sha256, proposed_by, proposed_at) VALUES ('-Nabc0000000000000W1', "
        "'-Nabc0000000000000A1', DATE '2026-11-13', repeat('0', 64), repeat('0', 64), "
        "'-Nabc0000000000000S1', now()); "
        "INSERT INTO migration_switch_decisions (switch_id, outcome, decided_by, decided_at) "
        "VALUES ('-Nabc0000000000000W1', 'approved', '-Nabc0000000000000S2', now()); "
        "INSERT INTO migration_carried_clients (legacy_client_ref, switch_id, client_id, "
        "carried_by, carried_at) VALUES ('1', '-Nabc0000000000000W1', '-Nabc0000000000000C1', "
        "'-Nabc0000000000000S2', now()); "
        "INSERT INTO migration_carry_problems (switch_id, legacy_client_ref, attempt, code, "
        "noted_at) VALUES ('-Nabc0000000000000W1', '1', 1, 'document_taken', now()); "
        "INSERT INTO migration_carry_resolutions (id, switch_id, legacy_client_ref, attempt, "
        "action, reason, proposed_by, proposed_at) VALUES ('-Nabc0000000000000R1', "
        "'-Nabc0000000000000W1', '1', 1, 'close', 'Same person', '-Nabc0000000000000S1', now())",
        "refusing to drop how the switch resolved the clients it could not carry",
    ),
    "0200_kyc_liveness": (
        "INSERT INTO kyc_checks (id, client_id, document_type, document_country, created_at, "
        "updated_at) VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', 'national_id', "
        "'UG', now(), now()); "
        "INSERT INTO kyc_liveness (id, check_id, provider, created_at, updated_at) VALUES "
        "('-Nabc0000000000000L1', '-Nabc0000000000000A1', 'iotec-faces', now(), now())",
        "refusing to drop liveness records",
    ),
    "0152_client_locks": (
        "BEGIN; SELECT set_config('finvest.actor_kind', 'staff', true); "
        "SELECT set_config('finvest.actor_id', '-Nabc0000000000000S1', true); "
        "INSERT INTO identity_staff (id, email, name, role, password_hash, status, created_at) "
        "VALUES ('-Nabc0000000000000S1', 'ops@finvestug.com', 'Ops', 'operations', 'x', "
        "'active', now()); "
        "INSERT INTO identity_clients (id, phone, password_hash, password_changed_at, status, "
        "created_at) VALUES ('-Nabc0000000000000B1', '+256772123456', 'x', now(), 'active', "
        "now()); "
        "INSERT INTO identity_client_locks (id, kind, client_id, reason, proposed_by, "
        "proposed_at) VALUES ('-Nabc0000000000000A1', 'lock', '-Nabc0000000000000B1', "
        "'Phone stolen', '-Nabc0000000000000S1', now()); COMMIT",
        "refusing to drop identity_client_locks",
    ),
    "0160_identity_phone_changes": (
        "BEGIN; INSERT INTO identity_staff (id, email, name, role, created_at) VALUES "
        "('-Nabc0000000000000S1', 'ada@finvestug.com', 'Ada', 'operations', now()); "
        "INSERT INTO identity_clients (id, phone, status, created_at) VALUES "
        "('-Nabc0000000000000B1', '+256772123456', 'unclaimed', now()); "
        "SELECT set_config('finvest.actor_kind', 'staff', true); "
        "SELECT set_config('finvest.actor_id', '-Nabc0000000000000S1', true); "
        "INSERT INTO identity_phone_changes (id, client_id, new_phone, reason, proposed_by, "
        "proposed_at, expires_at) VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', "
        "'+256772123457', 'Lost SIM', '-Nabc0000000000000S1', now(), now() + interval '1 day'); "
        "COMMIT",
        "refusing to drop the record of number changes",
    ),
    "0150_staff_changes": (
        "BEGIN; INSERT INTO identity_staff (id, email, name, role, password_hash, status, "
        "created_at) VALUES ('-Nabc0000000000000S1', 'admin@finvestug.com', 'Admin', 'admin', "
        "'x', 'active', now()); "
        "SELECT set_config('finvest.actor_kind', 'staff', true); "
        "SELECT set_config('finvest.actor_id', '-Nabc0000000000000S1', true); "
        "INSERT INTO identity_staff_changes (id, kind, staff_id, email, name, role, reason, "
        "proposed_by, proposed_at) VALUES ('-Nabc0000000000000A1', 'invite', "
        "'-Nabc0000000000000S2', 'new@finvestug.com', 'New Person', 'support', 'Hire', "
        "'-Nabc0000000000000S1', now()); COMMIT",
        "refusing to drop identity_staff_changes",
    ),
    "0162_identity_email_checks": (
        "INSERT INTO identity_clients (id, phone, status, created_at) VALUES "
        "('-Nabc0000000000000B1', '+256772123456', 'unclaimed', now()); "
        "INSERT INTO identity_email_checks (id, client_id, email, token_hash, created_at, "
        "expires_at) VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', 'a@example.com', "
        "decode(repeat('00', 32), 'hex'), now(), now() + interval '1 day')",
        "refusing to drop proved email addresses",
    ),
    "0163_identity_email_fence": (
        "INSERT INTO identity_clients (id, phone, status, created_at) VALUES "
        "('-Nabc0000000000000B1', '+256772123456', 'unclaimed', now()); "
        "INSERT INTO identity_email_checks (id, client_id, email, token_hash, created_at, "
        "expires_at) VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', 'a@example.com', "
        "decode(repeat('00', 32), 'hex'), now(), now() + interval '1 day')",
        "refusing to unfence email links that exist",
    ),
    "0164_identity_session_devices": (
        "INSERT INTO identity_clients (id, phone, status, created_at) VALUES "
        "('-Nabc0000000000000B1', '+256772123456', 'unclaimed', now()); "
        "INSERT INTO identity_sessions (id, actor_kind, actor_id, token_hash, second_step, "
        "transport, created_at, last_seen_at, idle_expires_at, absolute_expires_at, device) "
        "VALUES ('-Nabc0000000000000A1', 'client', '-Nabc0000000000000B1', "
        "decode(repeat('00', 32), 'hex'), 'passkey', 'token', now(), now(), now(), now(), "
        "'Chrome on Android')",
        "refusing to drop the devices sessions were opened on",
    ),
    "0165_identity_session_reverify": (
        "INSERT INTO identity_staff (id, email, name, role, password_hash, status, created_at) "
        "VALUES ('-Nabc0000000000000S1', 'admin@finvestug.com', 'Admin', 'admin', 'x', "
        "'active', now()); "
        "INSERT INTO identity_sessions (id, actor_kind, actor_id, token_hash, second_step, "
        "transport, created_at, last_seen_at, idle_expires_at, absolute_expires_at, "
        "reverified_at) VALUES ('-Nabc0000000000000A1', 'staff', '-Nabc0000000000000S1', "
        "decode(repeat('00', 32), 'hex'), 'passkey', 'cookie', now(), now(), now(), now(), "
        "now())",
        "refusing to drop staff re-confirmations",
    ),
    "0230_kyc_next_of_kin": (
        "INSERT INTO kyc_next_of_kin_lists (client_id, people, updated_at) "
        "VALUES ('-Nabc0000000000000B1', 0, now())",
        "refusing to drop next of kin",
    ),
    "0231_kyc_risk_answers": (
        "INSERT INTO kyc_risk_answers (id, client_id, answers, score, profile, answered_at) "
        "VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', '{}', 0, 'careful', now())",
        "refusing to drop the answers clients gave",
    ),
    "0232_account_closures": (
        "BEGIN; SELECT set_config('finvest.actor_kind', 'client', true); "
        "SELECT set_config('finvest.actor_id', '-Nabc0000000000000B1', true); "
        "INSERT INTO closure_requests (id, client_id, requested_at) "
        "VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', now()); COMMIT",
        "refusing to drop closure_requests",
    ),
    "0221_identity_app_pins": (
        "INSERT INTO identity_clients (id, phone, status, created_at) VALUES "
        "('-Nabc0000000000000B1', '+256772123456', 'unclaimed', now()); "
        "INSERT INTO identity_sessions (id, actor_kind, actor_id, token_hash, second_step, "
        "transport, created_at, last_seen_at, idle_expires_at, absolute_expires_at) "
        "VALUES ('-Nabc0000000000000A1', 'client', '-Nabc0000000000000B1', "
        "decode(repeat('00', 32), 'hex'), 'passkey', 'token', now(), now(), now(), now()); "
        "INSERT INTO identity_app_pins (session_id, pin_hash, created_at) "
        "VALUES ('-Nabc0000000000000A1', 'x', now())",
        "refusing to drop app PINs that are in use",
    ),
    "0222_identity_recovery_requests": (
        "INSERT INTO identity_clients (id, phone, status, created_at) VALUES "
        "('-Nabc0000000000000B1', '+256772123456', 'unclaimed', now()); "
        "INSERT INTO identity_recovery_requests (id, client_id, contact_phone, reason, status, "
        "created_at) VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', '+256701555013', "
        "'lost_phone', 'open', now())",
        "refusing to drop recovery requests staff have not closed",
    ),
    "0260_payment_bank_accounts": (
        # The guard admits only staff proposing in their own name.
        "ALTER TABLE payment_bank_accounts DISABLE TRIGGER payment_bank_accounts_guard; "
        "INSERT INTO payment_bank_accounts (id, currency, bank_name, account_name, "
        "account_number, reason, proposed_by, proposed_at) VALUES ('-Nabc0000000000000A1', "
        "'UGX', 'Example Bank', 'Finvest Clients', '9030001234567', 'Trust account', "
        "'-Nabc0000000000000S1', now())",
        "refusing to drop bank accounts that hold records",
    ),
    "0224_identity_recovery_asks": (
        "INSERT INTO identity_clients (id, phone, status, created_at) VALUES "
        "('-Nabc0000000000000B1', '+256772123456', 'unclaimed', now()); "
        "INSERT INTO identity_recovery_requests (id, client_id, contact_phone, reason, status, "
        "created_at, asks, last_asked_at) VALUES ('-Nabc0000000000000A1', '-Nabc0000000000000B1', "
        "'+256701555013', 'lost_phone', 'open', now(), 2, now())",
        "refusing to drop the later asks on open recovery requests",
    ),
    "0225_identity_pin_confirmations": (
        "INSERT INTO identity_clients (id, phone, status, created_at) VALUES "
        "('-Nabc0000000000000B1', '+256772123456', 'unclaimed', now()); "
        "INSERT INTO identity_sessions (id, actor_kind, actor_id, token_hash, second_step, "
        "transport, created_at, last_seen_at, idle_expires_at, absolute_expires_at) "
        "VALUES ('-Nabc0000000000000A1', 'client', '-Nabc0000000000000B1', "
        "decode(repeat('00', 32), 'hex'), 'passkey', 'token', now(), now(), now(), now()); "
        "INSERT INTO identity_pin_confirmations (id, session_id, action, token_hash, created_at, "
        "expires_at) VALUES ('-Nabc0000000000000C1', '-Nabc0000000000000A1', 'withdrawal', "
        "decode(repeat('11', 32), 'hex'), now(), now() + interval '3 minutes')",
        "refusing to drop PIN confirmations that are not used yet",
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
