"""Add the allocations of bills to client investments.

Revision ID: 0085_dealing_allocations
Revises: 0084_dealing_withdrawal_funding

An allocation is an amount of one lot's cost standing behind one investment. The worker makes
and releases them. One is never changed except to be released once, and never removed. A lot
never has more allocated from it than the cost it still holds. See docs/dealing-desk.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0085_dealing_allocations"
down_revision: str | None = "0084_dealing_withdrawal_funding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE dealing_allocations (
    id push_id NOT NULL,
    -- A running investment in the investments module.
    investment_id push_id NOT NULL,
    lot_id push_id NOT NULL,
    cost_minor bigint NOT NULL,
    allocated_at timestamptz NOT NULL,
    released_at timestamptz,
    release_reason varchar(20),
    CONSTRAINT pk_dealing_allocations PRIMARY KEY (id),
    CONSTRAINT fk_dealing_allocations_lot FOREIGN KEY (lot_id) REFERENCES dealing_lots (id),
    CONSTRAINT ck_dealing_allocations_cost CHECK (cost_minor > 0),
    CONSTRAINT ck_dealing_allocations_released CHECK (
        (released_at IS NULL) = (release_reason IS NULL)
        AND (release_reason IS NULL
            OR release_reason IN ('investment_ended', 'investment_reduced', 'lot_reduced')))
);
CREATE INDEX ix_dealing_allocations_investment
    ON dealing_allocations (investment_id) WHERE released_at IS NULL;
CREATE INDEX ix_dealing_allocations_lot ON dealing_allocations (lot_id) WHERE released_at IS NULL
"""

GUARD = """
CREATE FUNCTION finvest_dealing_allocations_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        cost_left bigint;
        allocated bigint;
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'an allocation is never removed' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'system' THEN
            RAISE EXCEPTION 'only the worker allocates bills' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'UPDATE' THEN
            IF OLD.released_at IS NOT NULL OR NEW.released_at IS NULL
                OR (to_jsonb(NEW) - '{released_at,release_reason}'::text[])
                    <> (to_jsonb(OLD) - '{released_at,release_reason}'::text[]) THEN
                RAISE EXCEPTION 'an allocation is only released, once' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.released_at IS NOT NULL THEN
            RAISE EXCEPTION 'an allocation starts in force' USING ERRCODE = '42501';
        END IF;
        -- The same lock a sale of the lot takes, so the cost left is read against committed
        -- sales and allocations.
        PERFORM pg_advisory_xact_lock(hashtext('dealing_lots'), hashtext(NEW.lot_id));
        SELECT lot.cost_minor - coalesce((SELECT sum(sale.cost_minor) FROM
                public.dealing_lot_sales AS sale WHERE sale.lot_id = lot.id), 0)
            INTO cost_left FROM public.dealing_lots AS lot
            JOIN public.dealing_orders AS bought ON bought.id = lot.order_id
            WHERE lot.id = NEW.lot_id AND bought.status = 'settled'
                AND NOT EXISTS (SELECT FROM public.dealing_lot_maturities AS matured
                    WHERE matured.lot_id = lot.id);
        SELECT coalesce(sum(cost_minor), 0) INTO allocated FROM public.dealing_allocations
            WHERE lot_id = NEW.lot_id AND released_at IS NULL;
        IF cost_left IS NULL OR allocated + NEW.cost_minor > cost_left THEN
            RAISE EXCEPTION 'a lot cannot have more allocated than the cost it still holds'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_dealing_allocations_guard() FROM PUBLIC",
    "CREATE TRIGGER dealing_allocations_guard "
    "BEFORE INSERT OR UPDATE OR DELETE ON dealing_allocations "
    "FOR EACH ROW EXECUTE FUNCTION finvest_dealing_allocations_guard()",
    "CREATE TRIGGER dealing_allocations_no_truncate BEFORE TRUNCATE ON dealing_allocations "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_dealing_allocations_guard()",
    "REVOKE ALL ON dealing_allocations FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (released_at, release_reason) ON dealing_allocations "
    f"TO {WORKER_ROLE}",
    f"GRANT SELECT ON dealing_allocations TO {APP_ROLE}",
    "ALTER TABLE dealing_allocations ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY dealing_allocations_worker ON dealing_allocations TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (true)",
    f"CREATE POLICY dealing_allocations_staff_read ON dealing_allocations FOR SELECT "
    f"TO {APP_ROLE} USING (finvest_actor_kind() = 'staff')",
]


def split(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLE), GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM dealing_allocations) THEN
        RAISE EXCEPTION 'refusing to drop allocations';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE dealing_allocations")
    op.execute("DROP FUNCTION finvest_dealing_allocations_guard()")
