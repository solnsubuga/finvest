"""Let an investment mature: rolled over, paid out, or held for the client.

Revision ID: 0063_investment_maturity
Revises: 0130_operations_signals

Adds the `matured` status, where the client asked to be paid, and what became of the money:
the investment it was rolled into, the payout that carries it, or why it is waiting. The guard
of migration 0061 is replaced to fence the new columns and the new step. See docs/investing.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0063_investment_maturity"
down_revision: str | None = "0130_operations_signals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

COLUMNS = [
    # A destination ID in the payouts module. No foreign key: a module owns only its own tables.
    "ALTER TABLE investments ADD COLUMN payout_destination_id push_id",
    "ALTER TABLE investments ADD COLUMN matured_at timestamptz",
    # The ledger entry that emptied the holding at maturity.
    "ALTER TABLE investments ADD COLUMN maturity_entry_id push_id",
    "ALTER TABLE investments ADD COLUMN rolled_into_id push_id",
    "ALTER TABLE investments ADD COLUMN rolled_from_id push_id",
    # What saved it when the client did not make it themselves: an automatic investing run or
    # a goal's saving rule, by that module's own ID. Set only by the worker, at creation, and
    # unique, so one source makes at most one investment.
    "ALTER TABLE investments ADD COLUMN source_type varchar(32)",
    "ALTER TABLE investments ADD COLUMN source_id push_id",
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_source CHECK ("
    "(source_type IS NULL AND source_id IS NULL) OR "
    "(source_type IN ('autoinvest_run', 'goal_saving') AND source_id IS NOT NULL))",
    "ALTER TABLE investments ADD CONSTRAINT uq_investments_source UNIQUE (source_type, source_id)",
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_one_origin CHECK ("
    "num_nonnulls(rolled_from_id, source_id) <= 1)",
    # A payout ID in the payouts module.
    "ALTER TABLE investments ADD COLUMN payout_id push_id",
    "ALTER TABLE investments ADD COLUMN payout_refusal varchar(64)",
    "ALTER TABLE investments DROP CONSTRAINT ck_investments_status",
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_status "
    "CHECK (status IN ('awaiting_payment', 'active', 'cancelled', 'matured'))",
    "ALTER TABLE investments DROP CONSTRAINT ck_investments_started",
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_started CHECK ("
    "(status IN ('active', 'matured')) = (start_date IS NOT NULL) "
    "AND (start_date IS NULL) = (started_at IS NULL) "
    "AND (start_date IS NULL) = (start_entry_id IS NULL))",
    # A matured investment has the entry that closed it, and its money went one way only.
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_matured CHECK ("
    "(status = 'matured') = (matured_at IS NOT NULL) "
    "AND (status = 'matured') = (maturity_entry_id IS NOT NULL) "
    "AND (status = 'matured')::int = num_nonnulls(rolled_into_id, payout_id, payout_refusal) "
    "AND (status <> 'matured' OR accrued_through = maturity_date))",
    "ALTER TABLE investments ADD CONSTRAINT fk_investments_rolled_into "
    "FOREIGN KEY (rolled_into_id) REFERENCES investments (id)",
    "ALTER TABLE investments ADD CONSTRAINT fk_investments_rolled_from "
    "FOREIGN KEY (rolled_from_id) REFERENCES investments (id)",
    "ALTER TABLE investments ADD CONSTRAINT uq_investments_rolled_from UNIQUE (rolled_from_id)",
    "ALTER TABLE investments ADD CONSTRAINT uq_investments_payout UNIQUE (payout_id)",
    # Paying out names where to. An open term has no maturity and so no destination.
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_destination CHECK ("
    "payout_destination_id IS NULL OR at_maturity = 'pay_out')",
    "CREATE INDEX ix_investments_maturing ON investments (maturity_date, id) "
    "WHERE status = 'active' AND maturity_date IS NOT NULL",
]

_FREE = (
    "status,at_maturity,start_date,maturity_date,started_at,start_entry_id,cancelled_at,"
    "accrued_through,accrued_gross_minor,accrued_tax_minor,updated_at,payout_destination_id,"
    "matured_at,maturity_entry_id,rolled_into_id,payout_id,payout_refusal"
)

# The whole guard again, as PostgreSQL replaces a function in one piece. What is new: the
# worker may create an investment for a rollover, an active investment may mature, a matured
# one changes only to send waiting money on, and the client's choice has a deadline.
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
                    OR OLD.status = 'active' AND NEW.status = 'matured') THEN
                RAISE EXCEPTION 'investment cannot go from % to % here', OLD.status, NEW.status
                    USING ERRCODE = '42501';
            END IF;
        ELSIF NEW.matured_at IS NOT NULL OR NEW.maturity_entry_id IS NOT NULL
            OR NEW.rolled_into_id IS NOT NULL OR NEW.payout_id IS NOT NULL
            OR NEW.payout_refusal IS NOT NULL THEN
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
            IF actor IS DISTINCT FROM 'client' THEN
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
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role name

GRANTS = [
    f"GRANT UPDATE (payout_destination_id, payout_id, payout_refusal) ON investments TO {APP_ROLE}",
    f"GRANT INSERT, UPDATE (matured_at, maturity_entry_id, rolled_into_id, payout_id, "
    f"payout_refusal) ON investments TO {WORKER_ROLE}",
]


def upgrade() -> None:
    for statement in [*COLUMNS, GUARD, *GRANTS]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM investments WHERE status = 'matured' OR rolled_from_id IS NOT NULL
               OR source_id IS NOT NULL
               OR payout_destination_id IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop the record of matured investments';
    END IF;
END
$$
"""


def downgrade() -> None:
    # What became of a matured investment's money is kept; a downgrade never erases it.
    op.execute(EMPTY_GUARD)
    op.execute(
        f"REVOKE UPDATE (payout_destination_id, payout_id, payout_refusal) "
        f"ON investments FROM {APP_ROLE}"
    )
    op.execute(f"REVOKE INSERT ON investments FROM {WORKER_ROLE}")
    # Back to the guard as migration 0061 wrote it, before the columns it does not know go.
    script = op.get_context().script
    if script is None:
        raise RuntimeError("the migration scripts are needed to restore the guard")
    earlier = script.get_revision("0061_investments")
    if earlier is None:
        raise RuntimeError("migration 0061_investments is missing")
    op.execute(earlier.module.GUARD.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    for name in (
        "ck_investments_one_origin",
        "uq_investments_source",
        "ck_investments_source",
        "ck_investments_destination",
        "uq_investments_payout",
        "uq_investments_rolled_from",
        "fk_investments_rolled_from",
        "fk_investments_rolled_into",
        "ck_investments_matured",
        "ck_investments_started",
        "ck_investments_status",
    ):
        op.execute(f"ALTER TABLE investments DROP CONSTRAINT {name}")
    op.execute("DROP INDEX ix_investments_maturing")
    for column in (
        "payout_destination_id",
        "matured_at",
        "maturity_entry_id",
        "rolled_into_id",
        "rolled_from_id",
        "source_type",
        "source_id",
        "payout_id",
        "payout_refusal",
    ):
        op.execute(f"ALTER TABLE investments DROP COLUMN {column}")
    op.execute(
        "ALTER TABLE investments ADD CONSTRAINT ck_investments_status "
        "CHECK (status IN ('awaiting_payment', 'active', 'cancelled'))"
    )
    op.execute(
        "ALTER TABLE investments ADD CONSTRAINT ck_investments_started CHECK ("
        "(status = 'active') = (start_date IS NOT NULL) "
        "AND (start_date IS NULL) = (started_at IS NULL) "
        "AND (start_date IS NULL) = (start_entry_id IS NULL))"
    )
