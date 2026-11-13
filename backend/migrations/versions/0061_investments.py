"""Add investments.

Revision ID: 0061_investments
Revises: 0021_one_live_deposit

It also closes two gaps in rate approval that the review of migration 0060 found: an approval
must come before the rate's effective date, and an approved proposal must have its rate.

One row per investment. A client creates it unpaid, with the terms in force that day, and the
database checks those terms against the catalogue itself. Only the worker starts or cancels
it, and its terms never change afterwards. See docs/investing.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0061_investments"
down_revision: str | None = "0021_one_live_deposit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE investments (
    id push_id NOT NULL,
    -- The client's ID in the identity module. No foreign key: a module owns only its own tables.
    client_id push_id NOT NULL,
    product_id push_id NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    principal_minor bigint NOT NULL,
    -- The terms, fixed when the client confirmed the review. They are the investment's own:
    -- a later rate change does not reach them.
    annual_rate_bps integer NOT NULL,
    tax_bps integer NOT NULL,
    term_days integer,
    at_maturity varchar(10),
    status varchar(16) NOT NULL DEFAULT 'awaiting_payment',
    created_at timestamptz NOT NULL,
    pay_by timestamptz NOT NULL,
    -- The business day the payment was confirmed. Interest is counted from it.
    start_date date,
    maturity_date date,
    started_at timestamptz,
    -- The ledger entry that moved the payment into the holding. No foreign key onto the ledger.
    start_entry_id push_id,
    cancelled_at timestamptz,
    -- Interest posted to the ledger so far, and the last day it was posted for.
    accrued_through date,
    accrued_gross_minor bigint NOT NULL DEFAULT 0,
    accrued_tax_minor bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_investments PRIMARY KEY (id),
    CONSTRAINT fk_investments_product FOREIGN KEY (product_id) REFERENCES investment_products (id),
    CONSTRAINT uq_investments_start_entry UNIQUE (start_entry_id),
    CONSTRAINT ck_investments_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_investments_principal CHECK (principal_minor > 0),
    CONSTRAINT ck_investments_rate CHECK (annual_rate_bps BETWEEN 1 AND 5000),
    CONSTRAINT ck_investments_tax CHECK (tax_bps BETWEEN 0 AND 9999),
    CONSTRAINT ck_investments_term CHECK (term_days BETWEEN 1 AND 3650),
    CONSTRAINT ck_investments_status
        CHECK (status IN ('awaiting_payment', 'active', 'cancelled')),
    -- A fixed term has a choice for its maturity. An open term has no maturity to choose for.
    CONSTRAINT ck_investments_at_maturity CHECK (
        at_maturity IN ('roll_over', 'pay_out') AND (term_days IS NULL) = (at_maturity IS NULL)),
    CONSTRAINT ck_investments_pay_by CHECK (pay_by > created_at),
    -- Started investments, and only they, have a start: its date, time and ledger entry.
    CONSTRAINT ck_investments_started CHECK (
        (status = 'active') = (start_date IS NOT NULL)
        AND (start_date IS NULL) = (started_at IS NULL)
        AND (start_date IS NULL) = (start_entry_id IS NULL)),
    CONSTRAINT ck_investments_maturity CHECK (
        (maturity_date IS NULL) = (start_date IS NULL OR term_days IS NULL)
        AND (maturity_date IS NULL OR maturity_date = start_date + term_days)),
    CONSTRAINT ck_investments_cancelled CHECK ((status = 'cancelled') = (cancelled_at IS NOT NULL)),
    CONSTRAINT ck_investments_accrued CHECK (
        accrued_tax_minor >= 0 AND accrued_gross_minor >= accrued_tax_minor
        AND (accrued_through IS NULL OR accrued_through >= start_date)
        AND (accrued_through IS NOT NULL OR accrued_gross_minor = 0))
);
CREATE INDEX ix_investments_client ON investments (client_id, created_at DESC, id DESC);
-- The daily interest run walks active investments by the last day they were accrued for.
CREATE INDEX ix_investments_accrual ON investments (accrued_through NULLS FIRST, id)
    WHERE status = 'active';
CREATE INDEX ix_investments_unpaid ON investments (pay_by) WHERE status = 'awaiting_payment'
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001), so the
# system's steps also require the worker's database role.
GUARD = f"""
CREATE FUNCTION finvest_investments_guard() RETURNS trigger
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
                OR NEW.accrued_tax_minor <> 0 OR NEW.accrued_through IS NOT NULL THEN
                RAISE EXCEPTION 'an investment begins unpaid, with nothing earned'
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
        IF (to_jsonb(NEW) - '{{status,at_maturity,start_date,maturity_date,started_at,start_entry_id,cancelled_at,accrued_through,accrued_gross_minor,accrued_tax_minor,updated_at}}'::text[])
            <> (to_jsonb(OLD) - '{{status,at_maturity,start_date,maturity_date,started_at,start_entry_id,cancelled_at,accrued_through,accrued_gross_minor,accrued_tax_minor,updated_at}}'::text[]) THEN
            RAISE EXCEPTION 'the terms of an investment never change' USING ERRCODE = '42501';
        END IF;
        IF NEW.status <> OLD.status THEN
            IF NOT is_worker OR OLD.status <> 'awaiting_payment' THEN
                RAISE EXCEPTION 'investment cannot go from % to % here', OLD.status, NEW.status
                    USING ERRCODE = '42501';
            END IF;
        ELSIF OLD.status = 'active' AND (
            NEW.start_date <> OLD.start_date OR NEW.started_at <> OLD.started_at
            OR NEW.start_entry_id <> OLD.start_entry_id) THEN
            RAISE EXCEPTION 'the start of an investment never changes' USING ERRCODE = '42501';
        END IF;
        -- Interest only ever goes up, a day at a time, and only the worker posts it.
        IF NEW.accrued_through IS DISTINCT FROM OLD.accrued_through
            OR NEW.accrued_gross_minor <> OLD.accrued_gross_minor
            OR NEW.accrued_tax_minor <> OLD.accrued_tax_minor THEN
            IF NOT is_worker OR NEW.status <> 'active'
                OR NEW.accrued_through IS NULL
                OR (OLD.accrued_through IS NOT NULL AND NEW.accrued_through <= OLD.accrued_through)
                OR NEW.accrued_gross_minor < OLD.accrued_gross_minor
                OR NEW.accrued_tax_minor < OLD.accrued_tax_minor THEN
                RAISE EXCEPTION 'interest is added by the daily run, forwards only'
                    USING ERRCODE = '42501';
            END IF;
        END IF;
        -- The client changes only the choice for maturity. Staff change nothing here.
        IF NEW.at_maturity IS DISTINCT FROM OLD.at_maturity AND actor IS DISTINCT FROM 'client' THEN
            RAISE EXCEPTION 'only the client chooses what happens at maturity'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role name

# From the review of 0060. The service already refuses both. Now the database does too.
RATE_APPROVAL = [
    # A rate never changes under a day that has already begun, in Kampala.
    "ALTER TABLE investment_rate_proposals ADD CONSTRAINT ck_investment_rate_proposals_in_time "
    "CHECK (status <> 'approved' "
    "OR effective_from > (decided_at AT TIME ZONE 'Africa/Kampala')::date)",
    # Checked at commit, so the approval and its rate can be written in either order.
    """
    CREATE FUNCTION finvest_investment_rate_proposals_have_rate() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
        AS $$
        BEGIN
            IF NEW.status = 'approved' AND NOT EXISTS (
                SELECT FROM investment_rates WHERE proposal_id = NEW.id) THEN
                RAISE EXCEPTION 'an approved rate proposal must have its rate'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$
    """,
    "REVOKE ALL ON FUNCTION finvest_investment_rate_proposals_have_rate() FROM PUBLIC",
    "CREATE CONSTRAINT TRIGGER investment_rate_proposals_have_rate "
    "AFTER UPDATE ON investment_rate_proposals DEFERRABLE INITIALLY DEFERRED "
    "FOR EACH ROW EXECUTE FUNCTION finvest_investment_rate_proposals_have_rate()",
]

RULES = [
    "REVOKE ALL ON FUNCTION finvest_investments_guard() FROM PUBLIC",
    "CREATE TRIGGER investments_guard BEFORE INSERT OR UPDATE ON investments "
    "FOR EACH ROW EXECUTE FUNCTION finvest_investments_guard()",
    "REVOKE ALL ON investments FROM PUBLIC",
    # No DELETE for anyone: an investment is a record the regulator can ask for.
    f"GRANT SELECT, INSERT, UPDATE (at_maturity, updated_at) ON investments TO {APP_ROLE}",
    f"GRANT SELECT, UPDATE (status, start_date, maturity_date, started_at, start_entry_id, "
    f"cancelled_at, accrued_through, accrued_gross_minor, accrued_tax_minor, updated_at) "
    f"ON investments TO {WORKER_ROLE}",
    "ALTER TABLE investments ENABLE ROW LEVEL SECURITY",
    # A client reaches their own investments. Staff read every one; which staff may is a
    # permission checked in the application.
    f"CREATE POLICY investments_app_read ON investments FOR SELECT TO {APP_ROLE} USING ("
    "(finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY investments_app_insert ON investments FOR INSERT TO {APP_ROLE} WITH CHECK ("
    "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY investments_app_update ON investments FOR UPDATE TO {APP_ROLE} USING ("
    "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY investments_worker ON investments TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (true)",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), GUARD, *RULES, *RATE_APPROVAL]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM investments) THEN
        RAISE EXCEPTION 'refusing to drop investments that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Investments are client records; a downgrade never erases them.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE investments")
    op.execute("DROP FUNCTION finvest_investments_guard()")
    op.execute("DROP TRIGGER investment_rate_proposals_have_rate ON investment_rate_proposals")
    op.execute("DROP FUNCTION finvest_investment_rate_proposals_have_rate()")
    op.execute(
        "ALTER TABLE investment_rate_proposals DROP CONSTRAINT ck_investment_rate_proposals_in_time"
    )
