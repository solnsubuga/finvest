"""Carry running investments over from Finvest 1.0.

Revision ID: 0065_investment_carried_over
Revises: 0164_identity_session_devices

Adds `legacy_reference`, the investment's reference in Finvest 1.0, unique, so the switch
opens each one once. The guard of migration 0064 is replaced with one more way in: the worker
may insert an investment that is already running, with the old system's rate and tax and the
interest earned so far, when it carries a legacy reference. See docs/investing.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0065_investment_carried_over"
down_revision: str | None = "0164_identity_session_devices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKER_ROLE = "finvest_worker"

COLUMNS = [
    'ALTER TABLE investments ADD COLUMN legacy_reference varchar(64) COLLATE "C"',
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_legacy_reference "
    "CHECK (legacy_reference ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,63}$')",
    "ALTER TABLE investments ADD CONSTRAINT uq_investments_legacy_reference "
    "UNIQUE (legacy_reference)",
]

_FREE = (
    "status,at_maturity,start_date,maturity_date,started_at,start_entry_id,cancelled_at,"
    "accrued_through,accrued_gross_minor,accrued_tax_minor,updated_at,payout_destination_id,"
    "matured_at,maturity_entry_id,rolled_into_id,payout_id,payout_refusal,"
    "withdrawal_requested_at,withdrawal_destination_id,withdrawn_at,withdrawal_entry_id,"
    "withdrawal_payout_id"
)

# The whole guard again, as PostgreSQL replaces a function in one piece. What is new is the
# first branch: an investment carried over from Finvest 1.0. The legacy reference is not among
# the free columns, so it never changes afterwards.
GUARD = f"""
CREATE OR REPLACE FUNCTION finvest_investments_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        actor text := finvest_actor_kind();
        is_worker boolean := finvest_actor_kind() = 'system' AND current_user = '{WORKER_ROLE}';
        product investment_products%ROWTYPE;
        rate investment_rates%ROWTYPE;
        day date;
    BEGIN
        IF TG_OP = 'INSERT' AND NEW.legacy_reference IS NOT NULL THEN
            -- Carried over from Finvest 1.0 at the switch, by the worker only: already running,
            -- on the old system's own rate and tax, with the interest earned up to a day
            -- before today and before it matures. Nothing else of a new investment applies.
            SELECT * INTO product FROM investment_products WHERE id = NEW.product_id;
            day := (clock_timestamp() AT TIME ZONE 'Africa/Kampala')::date;
            IF NOT is_worker OR NEW.status <> 'active'
                OR product.id IS NULL OR product.status = 'draft'
                OR NEW.currency <> product.currency
                OR NEW.term_days IS DISTINCT FROM product.term_days
                OR NEW.rolled_from_id IS NOT NULL OR NEW.source_id IS NOT NULL
                OR NEW.payout_destination_id IS NOT NULL
                OR NEW.withdrawal_requested_at IS NOT NULL
                OR NEW.accrued_through IS NULL OR NEW.accrued_through >= day
                OR NEW.start_date > NEW.accrued_through
                OR (NEW.maturity_date IS NOT NULL AND NEW.accrued_through >= NEW.maturity_date)
            THEN
                RAISE EXCEPTION 'only the switch carries over a running investment'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'awaiting_payment' OR NEW.accrued_gross_minor <> 0
                OR NEW.accrued_tax_minor <> 0 OR NEW.accrued_through IS NOT NULL
                OR NEW.payout_destination_id IS NOT NULL THEN
                RAISE EXCEPTION 'an investment begins unpaid, with nothing earned'
                    USING ERRCODE = '42501';
            END IF;
            -- A client creates their own. Only the worker creates one, from a rollover or a
            -- saving source such as an automatic investing run.
            IF (NEW.rolled_from_id IS NOT NULL OR NEW.source_id IS NOT NULL) <> is_worker
                OR (NOT is_worker AND actor IS DISTINCT FROM 'client') THEN
                RAISE EXCEPTION 'an investment is created by its client, a rollover or a saving source'
                    USING ERRCODE = '42501';
            END IF;
            -- The terms are the catalogue's on the day of the review, not whatever was sent.
            SELECT * INTO product FROM investment_products WHERE id = NEW.product_id;
            day := (NEW.created_at AT TIME ZONE 'Africa/Kampala')::date;
            SELECT * INTO rate FROM investment_rates
                WHERE product_id = NEW.product_id AND effective_from <= day
                ORDER BY effective_from DESC LIMIT 1;
            IF product.id IS NULL OR rate.id IS NULL OR product.status <> 'live'
                OR NEW.currency <> product.currency
                OR NEW.principal_minor < product.minimum_minor
                OR NEW.term_days IS DISTINCT FROM product.term_days
                OR NEW.annual_rate_bps <> rate.annual_rate_bps
                OR NEW.tax_bps <> rate.tax_bps THEN
                RAISE EXCEPTION 'an investment takes the product''s own terms on the day'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;

        IF OLD.status = 'cancelled' THEN
            RAISE EXCEPTION 'a cancelled investment never changes' USING ERRCODE = '42501';
        END IF;
        IF (to_jsonb(NEW) - '{{{_FREE}}}'::text[]) <> (to_jsonb(OLD) - '{{{_FREE}}}'::text[]) THEN
            RAISE EXCEPTION 'the terms of an investment never change' USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'withdrawn' THEN
            -- Closed. The one thing left is the payout that carries the money, recorded once
            -- by the worker when the dealing desk has funded it.
            IF (to_jsonb(NEW) - '{{withdrawal_payout_id,updated_at}}'::text[])
                <> (to_jsonb(OLD) - '{{withdrawal_payout_id,updated_at}}'::text[])
                OR OLD.withdrawal_payout_id IS NOT NULL OR NEW.withdrawal_payout_id IS NULL
                OR NOT is_worker THEN
                RAISE EXCEPTION 'a withdrawn investment never changes' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status = 'matured' THEN
            -- Closed. The one thing left is sending on money that is waiting for a
            -- destination, by the client or the worker.
            IF (to_jsonb(NEW) - '{{payout_id,payout_refusal,updated_at}}'::text[])
                <> (to_jsonb(OLD) - '{{payout_id,payout_refusal,updated_at}}'::text[])
                OR OLD.payout_id IS NOT NULL OR OLD.rolled_into_id IS NOT NULL
                OR NEW.payout_id IS NULL OR NEW.payout_refusal IS NOT NULL
                OR NOT (is_worker OR actor = 'client') THEN
                RAISE EXCEPTION 'a matured investment never changes' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.status <> OLD.status THEN
            IF NOT is_worker
                OR NOT (OLD.status = 'awaiting_payment' AND NEW.status IN ('active', 'cancelled')
                    OR OLD.status = 'active' AND NEW.status = 'matured'
                    OR OLD.status = 'active' AND NEW.status = 'withdrawn'
                        AND OLD.withdrawal_requested_at IS NOT NULL) THEN
                RAISE EXCEPTION 'investment cannot go from % to % here', OLD.status, NEW.status
                    USING ERRCODE = '42501';
            END IF;
        ELSIF NEW.matured_at IS NOT NULL OR NEW.maturity_entry_id IS NOT NULL
            OR NEW.rolled_into_id IS NOT NULL OR NEW.payout_id IS NOT NULL
            OR NEW.payout_refusal IS NOT NULL OR NEW.withdrawn_at IS NOT NULL
            OR NEW.withdrawal_entry_id IS NOT NULL OR NEW.withdrawal_payout_id IS NOT NULL THEN
            RAISE EXCEPTION 'only maturity records what became of the money'
                USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'active' AND (
            NEW.start_date <> OLD.start_date OR NEW.started_at <> OLD.started_at
            OR NEW.start_entry_id <> OLD.start_entry_id
            OR NEW.maturity_date IS DISTINCT FROM OLD.maturity_date) THEN
            RAISE EXCEPTION 'the start of an investment never changes' USING ERRCODE = '42501';
        END IF;
        -- Interest only ever goes up, a day at a time, and only the worker posts it.
        IF NEW.accrued_through IS DISTINCT FROM OLD.accrued_through
            OR NEW.accrued_gross_minor <> OLD.accrued_gross_minor
            OR NEW.accrued_tax_minor <> OLD.accrued_tax_minor THEN
            IF NOT is_worker OR OLD.status <> 'active'
                OR OLD.withdrawal_requested_at IS NOT NULL
                OR NEW.accrued_through IS NULL
                OR (OLD.accrued_through IS NOT NULL AND NEW.accrued_through <= OLD.accrued_through)
                OR NEW.accrued_gross_minor < OLD.accrued_gross_minor
                OR NEW.accrued_tax_minor < OLD.accrued_tax_minor THEN
                RAISE EXCEPTION 'interest is added by the daily run, forwards only'
                    USING ERRCODE = '42501';
            END IF;
        END IF;
        -- The client changes only the choice for maturity, and only before the maturity
        -- date. Staff change nothing here.
        IF NEW.at_maturity IS DISTINCT FROM OLD.at_maturity
            OR NEW.payout_destination_id IS DISTINCT FROM OLD.payout_destination_id THEN
            IF actor IS DISTINCT FROM 'client' OR OLD.withdrawal_requested_at IS NOT NULL THEN
                RAISE EXCEPTION 'only the client chooses what happens at maturity'
                    USING ERRCODE = '42501';
            END IF;
            IF OLD.maturity_date IS NOT NULL
                AND (clock_timestamp() AT TIME ZONE 'Africa/Kampala')::date >= OLD.maturity_date
            THEN
                RAISE EXCEPTION 'the choice for maturity closes the day before'
                    USING ERRCODE = '42501';
            END IF;
        END IF;
        -- Only the client asks to withdraw early, once, while it runs and before the day it
        -- matures. Interest stops at the request, so the amount they saw is the amount paid.
        IF NEW.withdrawal_requested_at IS DISTINCT FROM OLD.withdrawal_requested_at
            OR NEW.withdrawal_destination_id IS DISTINCT FROM OLD.withdrawal_destination_id THEN
            IF actor IS DISTINCT FROM 'client' OR OLD.status <> 'active'
                OR OLD.withdrawal_requested_at IS NOT NULL
                OR NEW.withdrawal_requested_at IS NULL OR NEW.withdrawal_destination_id IS NULL
                OR abs(extract(epoch FROM NEW.withdrawal_requested_at - clock_timestamp())) > 300
            THEN
                RAISE EXCEPTION 'only the client asks, once, to withdraw a running investment'
                    USING ERRCODE = '42501';
            END IF;
            IF OLD.maturity_date IS NOT NULL
                AND (clock_timestamp() AT TIME ZONE 'Africa/Kampala')::date >= OLD.maturity_date
            THEN
                RAISE EXCEPTION 'early withdrawal closes the day before maturity'
                    USING ERRCODE = '42501';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role name


def upgrade() -> None:
    for statement in [*COLUMNS, GUARD]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM investments WHERE legacy_reference IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop the record of investments carried over';
    END IF;
END
$$
"""


def downgrade() -> None:
    # An investment carried over is kept; a downgrade never erases where it came from.
    op.execute(EMPTY_GUARD)
    script = op.get_context().script
    if script is None:
        raise RuntimeError("the migration scripts are needed to restore the guard")
    earlier = script.get_revision("0064_investment_early_withdrawal")
    op.execute(earlier.module.GUARD)
    op.execute("ALTER TABLE investments DROP CONSTRAINT uq_investments_legacy_reference")
    op.execute("ALTER TABLE investments DROP CONSTRAINT ck_investments_legacy_reference")
    op.execute("ALTER TABLE investments DROP COLUMN legacy_reference")
