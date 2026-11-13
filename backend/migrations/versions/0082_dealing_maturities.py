"""Record bills the issuer has paid at maturity.

Revision ID: 0082_dealing_maturities
Revises: 0009_claims

On a lot's maturity date the worker posts the face value still held into the trust bank account
and records the maturity. The database checks the record and its ledger entry agree with what
the lot still holds, takes nothing from a lot after it matures, and never changes or removes a
maturity. See docs/dealing-desk.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0082_dealing_maturities"
down_revision: str | None = "0009_claims"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE dealing_lot_maturities (
    lot_id push_id NOT NULL,
    matured_on date NOT NULL,
    -- What the lot still held when it matured.
    face_minor bigint NOT NULL,
    cost_minor bigint NOT NULL,
    -- The ledger entry that posted it. No foreign key: a module owns only its tables.
    ledger_entry_id push_id NOT NULL,
    recorded_at timestamptz NOT NULL,
    CONSTRAINT pk_dealing_lot_maturities PRIMARY KEY (lot_id),
    CONSTRAINT fk_dealing_lot_maturities_lot FOREIGN KEY (lot_id) REFERENCES dealing_lots (id),
    CONSTRAINT uq_dealing_lot_maturities_entry UNIQUE (ledger_entry_id),
    CONSTRAINT ck_dealing_lot_maturities_amounts CHECK (face_minor > 0 AND cost_minor > 0)
)
"""

# Uses the same two-key advisory lock on the lot as a sale, so a maturity and a sale of the same
# lot are checked one after the other.
MATURITY_GUARD = """
CREATE FUNCTION finvest_dealing_maturity_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        lot public.dealing_lots%ROWTYPE;
        sold_face bigint;
        sold_cost bigint;
        bank bigint;
        bills bigint;
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'a maturity is never changed or removed' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'system' THEN
            RAISE EXCEPTION 'only the worker records a maturity' USING ERRCODE = '42501';
        END IF;
        PERFORM pg_advisory_xact_lock(hashtext('dealing_lots'), hashtext(NEW.lot_id));
        SELECT * INTO lot FROM public.dealing_lots WHERE id = NEW.lot_id;
        SELECT coalesce(sum(face_minor), 0), coalesce(sum(cost_minor), 0)
            INTO sold_face, sold_cost FROM public.dealing_lot_sales WHERE lot_id = NEW.lot_id;
        IF lot.id IS NULL OR NEW.matured_on IS DISTINCT FROM lot.matures_on
            OR NEW.face_minor IS DISTINCT FROM lot.face_minor - sold_face
            OR NEW.cost_minor IS DISTINCT FROM lot.cost_minor - sold_cost THEN
            RAISE EXCEPTION 'a maturity is what the lot still holds, on its maturity date'
                USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (
            SELECT FROM public.ledger_journal_entries
            WHERE id = NEW.ledger_entry_id AND source_type = 'dealing.lot'
                AND source_id = NEW.lot_id AND currency = lot.currency
                AND effective_date = lot.matures_on AND reverses_entry_id IS NULL
        ) THEN
            RAISE EXCEPTION 'a maturity needs its ledger entry' USING ERRCODE = '23514';
        END IF;
        SELECT
            coalesce(sum(line.amount_minor) FILTER (WHERE account.code = 'bank.trust'), 0),
            coalesce(sum(line.amount_minor) FILTER (WHERE account.code = 'dealing.bills'), 0)
            INTO bank, bills
            FROM public.ledger_lines AS line JOIN public.ledger_accounts AS account ON account.id = line.account_id
            WHERE line.entry_id = NEW.ledger_entry_id
                AND account.owner_client_id IS NULL AND account.reference IS NULL;
        IF bank IS DISTINCT FROM NEW.face_minor OR bills IS DISTINCT FROM -NEW.cost_minor THEN
            RAISE EXCEPTION 'a maturity needs its ledger entry' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# A sale settles before the lot matures, and never takes from a lot that has matured. Runs after
# the 0081 guard, which holds the lot's lock by then.
SALE_GUARD = """
CREATE FUNCTION finvest_dealing_sale_before_maturity() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF EXISTS (SELECT FROM public.dealing_lot_maturities WHERE lot_id = NEW.lot_id)
            OR NOT EXISTS (
                SELECT FROM public.dealing_lots AS lot JOIN public.dealing_orders AS sale ON sale.id = NEW.order_id
                WHERE lot.id = NEW.lot_id AND sale.result_settled_on < lot.matures_on
            ) THEN
            RAISE EXCEPTION 'a sale cannot take bills that have matured' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_dealing_maturity_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_dealing_sale_before_maturity() FROM PUBLIC",
    "CREATE TRIGGER dealing_lot_maturities_guard "
    "BEFORE INSERT OR UPDATE OR DELETE ON dealing_lot_maturities "
    "FOR EACH ROW EXECUTE FUNCTION finvest_dealing_maturity_guard()",
    "CREATE TRIGGER dealing_lot_maturities_no_truncate BEFORE TRUNCATE ON dealing_lot_maturities "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_dealing_maturity_guard()",
    # Trigger names sort after dealing_lot_sales_guard, so this runs second.
    "CREATE TRIGGER dealing_lot_sales_not_matured BEFORE INSERT ON dealing_lot_sales "
    "FOR EACH ROW EXECUTE FUNCTION finvest_dealing_sale_before_maturity()",
    "REVOKE ALL ON dealing_lot_maturities FROM PUBLIC",
    f"GRANT SELECT ON dealing_lot_maturities TO {APP_ROLE}",
    f"GRANT SELECT, INSERT ON dealing_lot_maturities TO {WORKER_ROLE}",
    "ALTER TABLE dealing_lot_maturities ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY dealing_lot_maturities_staff_read ON dealing_lot_maturities FOR SELECT "
    f"TO {APP_ROLE} USING (finvest_actor_kind() = 'staff')",
    f"CREATE POLICY dealing_lot_maturities_worker ON dealing_lot_maturities TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (true)",
]


def upgrade() -> None:
    for statement in [TABLE, MATURITY_GUARD, SALE_GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM dealing_lot_maturities) THEN
        RAISE EXCEPTION 'refusing to drop bill maturities';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TRIGGER dealing_lot_sales_not_matured ON dealing_lot_sales")
    op.execute("DROP TABLE dealing_lot_maturities")
    op.execute("DROP FUNCTION finvest_dealing_sale_before_maturity()")
    op.execute("DROP FUNCTION finvest_dealing_maturity_guard()")
