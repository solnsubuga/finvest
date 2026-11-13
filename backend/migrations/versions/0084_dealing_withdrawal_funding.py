"""Add how each early withdrawal is funded: from the liquidity buffer or by a sale.

Revision ID: 0084_dealing_withdrawal_funding
Revises: 0083_dealing_buffer_rules

The worker records a funding when investing hands over an early withdrawal, and funds one
queued for the buffer when the buffer recovers. A member of staff funds withdrawals waiting for
a sale from a settled sale, never for more than it raised. A funding is never removed, and once
funded never changes. See docs/dealing-desk.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0084_dealing_withdrawal_funding"
down_revision: str | None = "0083_dealing_buffer_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE dealing_withdrawal_fundings (
    id push_id NOT NULL,
    -- The investment withdrawn early, in the investments module. Withdrawn once.
    investment_id push_id NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    amount_minor bigint NOT NULL,
    source varchar(6) NOT NULL,
    status varchar(7) NOT NULL,
    asked_at timestamptz NOT NULL,
    funded_at timestamptz,
    sale_order_id push_id,
    -- The member of staff who funded it from a sale. A staff ID in the identity module.
    funded_by push_id,
    CONSTRAINT pk_dealing_withdrawal_fundings PRIMARY KEY (id),
    CONSTRAINT uq_dealing_withdrawal_fundings_investment UNIQUE (investment_id),
    CONSTRAINT fk_dealing_withdrawal_fundings_sale
        FOREIGN KEY (sale_order_id) REFERENCES dealing_orders (id),
    CONSTRAINT ck_dealing_withdrawal_fundings_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_dealing_withdrawal_fundings_amount CHECK (amount_minor > 0),
    CONSTRAINT ck_dealing_withdrawal_fundings_source CHECK (source IN ('buffer', 'sale')),
    CONSTRAINT ck_dealing_withdrawal_fundings_status CHECK (status IN ('waiting', 'funded')),
    CONSTRAINT ck_dealing_withdrawal_fundings_funded CHECK (
        (status = 'funded') = (funded_at IS NOT NULL)),
    CONSTRAINT ck_dealing_withdrawal_fundings_sale CHECK (
        (sale_order_id IS NOT NULL) = (source = 'sale' AND status = 'funded')
        AND (funded_by IS NOT NULL) = (sale_order_id IS NOT NULL))
);
CREATE INDEX ix_dealing_withdrawal_fundings_waiting
    ON dealing_withdrawal_fundings (currency, source, asked_at, id) WHERE status = 'waiting';
CREATE INDEX ix_dealing_withdrawal_fundings_sale
    ON dealing_withdrawal_fundings (sale_order_id) WHERE sale_order_id IS NOT NULL
"""

GUARD = """
CREATE FUNCTION finvest_dealing_withdrawal_fundings_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        sale dealing_orders%ROWTYPE;
        used bigint;
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'a withdrawal funding is never removed' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF finvest_actor_kind() IS DISTINCT FROM 'system' THEN
                RAISE EXCEPTION 'only the worker records a withdrawal funding'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.status = 'funded' AND NEW.source <> 'buffer' THEN
                RAISE EXCEPTION 'a withdrawal waiting for a sale starts as waiting'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'waiting' OR NEW.status <> 'funded'
            OR (to_jsonb(NEW) - '{status,funded_at,sale_order_id,funded_by}'::text[])
                <> (to_jsonb(OLD) - '{status,funded_at,sale_order_id,funded_by}'::text[]) THEN
            RAISE EXCEPTION 'a waiting withdrawal is only funded, once' USING ERRCODE = '42501';
        END IF;
        IF NEW.source = 'buffer' THEN
            IF finvest_actor_kind() IS DISTINCT FROM 'system' THEN
                RAISE EXCEPTION 'only the worker funds a withdrawal from the buffer'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF finvest_actor_kind() IS DISTINCT FROM 'staff'
            OR NEW.funded_by IS DISTINCT FROM finvest_actor_id() THEN
            RAISE EXCEPTION 'a withdrawal is funded from a sale by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        -- Fundings from one sale are added up one at a time, against committed rows.
        PERFORM pg_advisory_xact_lock(hashtext('dealing_withdrawal_sales'),
            hashtext(NEW.sale_order_id));
        SELECT * INTO sale FROM dealing_orders WHERE id = NEW.sale_order_id;
        IF NOT FOUND OR sale.kind <> 'secondary_sale' OR sale.status <> 'settled'
            OR sale.currency <> NEW.currency THEN
            RAISE EXCEPTION 'a withdrawal is funded from a settled sale in its currency'
                USING ERRCODE = '42501';
        END IF;
        SELECT coalesce(sum(amount_minor), 0)::bigint INTO used
            FROM dealing_withdrawal_fundings
            WHERE sale_order_id = NEW.sale_order_id AND id <> NEW.id;
        IF used + NEW.amount_minor > sale.result_amount_minor THEN
            RAISE EXCEPTION 'withdrawals funded from a sale cannot exceed what it raised'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_dealing_withdrawal_fundings_guard() FROM PUBLIC",
    "CREATE TRIGGER dealing_withdrawal_fundings_guard "
    "BEFORE INSERT OR UPDATE OR DELETE ON dealing_withdrawal_fundings "
    "FOR EACH ROW EXECUTE FUNCTION finvest_dealing_withdrawal_fundings_guard()",
    "CREATE TRIGGER dealing_withdrawal_fundings_no_truncate "
    "BEFORE TRUNCATE ON dealing_withdrawal_fundings "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_dealing_withdrawal_fundings_guard()",
    "REVOKE ALL ON dealing_withdrawal_fundings FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (status, funded_at) ON dealing_withdrawal_fundings "
    f"TO {WORKER_ROLE}",
    f"GRANT SELECT, UPDATE (status, funded_at, sale_order_id, funded_by) "
    f"ON dealing_withdrawal_fundings TO {APP_ROLE}",
    "ALTER TABLE dealing_withdrawal_fundings ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY dealing_withdrawal_fundings_worker ON dealing_withdrawal_fundings "
    f"TO {WORKER_ROLE} USING (true) WITH CHECK (true)",
    f"CREATE POLICY dealing_withdrawal_fundings_staff ON dealing_withdrawal_fundings "
    f"TO {APP_ROLE} USING (finvest_actor_kind() = 'staff') "
    "WITH CHECK (finvest_actor_kind() = 'staff')",
]


def split(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLE), GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM dealing_withdrawal_fundings) THEN
        RAISE EXCEPTION 'refusing to drop withdrawal fundings';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE dealing_withdrawal_fundings")
    op.execute("DROP FUNCTION finvest_dealing_withdrawal_fundings_guard()")
