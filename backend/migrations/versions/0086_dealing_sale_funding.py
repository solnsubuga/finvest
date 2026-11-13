"""Fund withdrawals waiting for a sale by the worker, as soon as the sale settles.

Revision ID: 0086_dealing_sale_funding
Revises: 0164_identity_session_devices

A review follow-up to 0084. There a member of staff funded the withdrawals waiting for a sale
from a settled sale's proceeds: one person releasing client payouts. The sale itself is
already prepared, approved, recorded and confirmed by two people, and the order of the queue
and the amounts are fixed, so the worker now does it when the sale settles and no person is
involved. Only the worker funds a withdrawal, from either source, and staff can no longer
change a funding at all. A funding from a sale has no `funded_by`: it is kept for any row an
earlier version recorded. The other rules are unchanged. See docs/dealing-desk.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0086_dealing_sale_funding"
down_revision: str | None = "0164_identity_session_devices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

GUARD = """
CREATE OR REPLACE FUNCTION public.finvest_dealing_withdrawal_fundings_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        sale public.dealing_orders%ROWTYPE;
        used bigint;
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'a withdrawal funding is never removed' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'system' THEN
            RAISE EXCEPTION 'only the worker records or funds a withdrawal'
                USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status = 'funded' AND NEW.source IS DISTINCT FROM 'buffer' THEN
                RAISE EXCEPTION 'a withdrawal waiting for a sale starts as waiting'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.funded_by IS NOT NULL THEN
                RAISE EXCEPTION 'a withdrawal is funded by the worker, not a person'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status IS DISTINCT FROM 'waiting' OR NEW.status IS DISTINCT FROM 'funded'
            OR (to_jsonb(NEW) - '{status,funded_at,sale_order_id}'::text[])
                IS DISTINCT FROM (to_jsonb(OLD) - '{status,funded_at,sale_order_id}'::text[]) THEN
            RAISE EXCEPTION 'a waiting withdrawal is only funded, once' USING ERRCODE = '42501';
        END IF;
        IF NEW.source = 'buffer' THEN
            RETURN NEW;
        END IF;
        -- Fundings from one sale are added up one at a time, against committed rows.
        PERFORM pg_advisory_xact_lock(hashtext('dealing_withdrawal_sales'),
            hashtext(NEW.sale_order_id));
        SELECT * INTO sale FROM public.dealing_orders WHERE id = NEW.sale_order_id;
        IF NOT FOUND OR sale.kind IS DISTINCT FROM 'secondary_sale'
            OR sale.status IS DISTINCT FROM 'settled'
            OR sale.currency IS DISTINCT FROM NEW.currency THEN
            RAISE EXCEPTION 'a withdrawal is funded from a settled sale in its currency'
                USING ERRCODE = '42501';
        END IF;
        SELECT coalesce(sum(amount_minor), 0)::bigint INTO used
            FROM public.dealing_withdrawal_fundings
            WHERE sale_order_id = NEW.sale_order_id AND id <> NEW.id;
        IF used + NEW.amount_minor > sale.result_amount_minor THEN
            RAISE EXCEPTION 'withdrawals funded from a sale cannot exceed what it raised'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

OLD_GUARD = """
CREATE OR REPLACE FUNCTION public.finvest_dealing_withdrawal_fundings_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        sale public.dealing_orders%ROWTYPE;
        used bigint;
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'a withdrawal funding is never removed' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF public.finvest_actor_kind() IS DISTINCT FROM 'system' THEN
                RAISE EXCEPTION 'only the worker records a withdrawal funding'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.status = 'funded' AND NEW.source IS DISTINCT FROM 'buffer' THEN
                RAISE EXCEPTION 'a withdrawal waiting for a sale starts as waiting'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status IS DISTINCT FROM 'waiting' OR NEW.status IS DISTINCT FROM 'funded'
            OR (to_jsonb(NEW) - '{status,funded_at,sale_order_id,funded_by}'::text[])
                IS DISTINCT FROM (to_jsonb(OLD) - '{status,funded_at,sale_order_id,funded_by}'::text[]) THEN
            RAISE EXCEPTION 'a waiting withdrawal is only funded, once' USING ERRCODE = '42501';
        END IF;
        IF NEW.source = 'buffer' THEN
            IF public.finvest_actor_kind() IS DISTINCT FROM 'system' THEN
                RAISE EXCEPTION 'only the worker funds a withdrawal from the buffer'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff'
            OR NEW.funded_by IS DISTINCT FROM public.finvest_actor_id() THEN
            RAISE EXCEPTION 'a withdrawal is funded from a sale by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        -- Fundings from one sale are added up one at a time, against committed rows.
        PERFORM pg_advisory_xact_lock(hashtext('dealing_withdrawal_sales'),
            hashtext(NEW.sale_order_id));
        SELECT * INTO sale FROM public.dealing_orders WHERE id = NEW.sale_order_id;
        IF NOT FOUND OR sale.kind IS DISTINCT FROM 'secondary_sale'
            OR sale.status IS DISTINCT FROM 'settled'
            OR sale.currency IS DISTINCT FROM NEW.currency THEN
            RAISE EXCEPTION 'a withdrawal is funded from a settled sale in its currency'
                USING ERRCODE = '42501';
        END IF;
        SELECT coalesce(sum(amount_minor), 0)::bigint INTO used
            FROM public.dealing_withdrawal_fundings
            WHERE sale_order_id = NEW.sale_order_id AND id <> NEW.id;
        IF used + NEW.amount_minor > sale.result_amount_minor THEN
            RAISE EXCEPTION 'withdrawals funded from a sale cannot exceed what it raised'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# A funding from a sale names the sale. Rows an earlier version recorded also name the person.
UPGRADE = [
    "ALTER TABLE dealing_withdrawal_fundings DROP CONSTRAINT ck_dealing_withdrawal_fundings_sale",
    "ALTER TABLE dealing_withdrawal_fundings ADD CONSTRAINT ck_dealing_withdrawal_fundings_sale "
    "CHECK ((sale_order_id IS NOT NULL) = (source = 'sale' AND status = 'funded') "
    "AND (funded_by IS NULL OR sale_order_id IS NOT NULL))",
    GUARD,
    f"REVOKE UPDATE ON dealing_withdrawal_fundings FROM {APP_ROLE}",
    f"GRANT UPDATE (sale_order_id) ON dealing_withdrawal_fundings TO {WORKER_ROLE}",
    "DROP POLICY dealing_withdrawal_fundings_staff ON dealing_withdrawal_fundings",
    f"CREATE POLICY dealing_withdrawal_fundings_staff ON dealing_withdrawal_fundings "
    f"FOR SELECT TO {APP_ROLE} USING (public.finvest_actor_kind() = 'staff')",
]

# The old rules need a person on every funding from a sale. One the worker funded has none, and
# none can be made up.
EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM dealing_withdrawal_fundings
               WHERE sale_order_id IS NOT NULL AND funded_by IS NULL) THEN
        RAISE EXCEPTION 'refusing to restore staff funding over withdrawals the worker funded';
    END IF;
END
$$
"""

DOWNGRADE = [
    EMPTY_GUARD,
    "DROP POLICY dealing_withdrawal_fundings_staff ON dealing_withdrawal_fundings",
    f"CREATE POLICY dealing_withdrawal_fundings_staff ON dealing_withdrawal_fundings "
    f"TO {APP_ROLE} USING (finvest_actor_kind() = 'staff') "
    "WITH CHECK (finvest_actor_kind() = 'staff')",
    f"REVOKE UPDATE ON dealing_withdrawal_fundings FROM {WORKER_ROLE}",
    f"GRANT UPDATE (status, funded_at) ON dealing_withdrawal_fundings TO {WORKER_ROLE}",
    f"GRANT UPDATE (status, funded_at, sale_order_id, funded_by) "
    f"ON dealing_withdrawal_fundings TO {APP_ROLE}",
    OLD_GUARD,
    "ALTER TABLE dealing_withdrawal_fundings DROP CONSTRAINT ck_dealing_withdrawal_fundings_sale",
    "ALTER TABLE dealing_withdrawal_fundings ADD CONSTRAINT ck_dealing_withdrawal_fundings_sale "
    "CHECK ((sale_order_id IS NOT NULL) = (source = 'sale' AND status = 'funded') "
    "AND (funded_by IS NOT NULL) = (sale_order_id IS NOT NULL))",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
