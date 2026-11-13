"""Add the bills the dealing desk holds, and let a second person settle an order.

Revision ID: 0081_dealing_holdings
Revises: 0080_dealing_orders

A lot is the bills one purchase bought: face value, cost and maturity. A sale takes face value
from lots of one instrument (product and maturity date). Settling an order needs, in the same
transaction, its ledger entry and its lots or sale lines, and the database checks they agree
with the result a different person recorded. Nothing here is ever changed or removed.
See docs/dealing-desk.md.
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0081_dealing_holdings"
down_revision: str | None = "0080_dealing_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
ALTER TABLE dealing_orders
    ADD COLUMN settled_by push_id,
    ADD COLUMN settled_at timestamptz,
    -- The ledger entry that settled it. No foreign key: a module owns only its tables.
    ADD COLUMN settlement_entry_id push_id,
    ADD CONSTRAINT ck_dealing_orders_settled CHECK (
        (status = 'settled') = (settled_by IS NOT NULL)
        AND (status = 'settled') = (settled_at IS NOT NULL)
        AND (status = 'settled') = (settlement_entry_id IS NOT NULL)),
    ADD CONSTRAINT ck_dealing_orders_settled_by CHECK (settled_by <> result_recorded_by),
    ADD CONSTRAINT uq_dealing_orders_settlement_entry UNIQUE (settlement_entry_id);
CREATE TABLE dealing_lots (
    id push_id NOT NULL,
    -- The purchase that bought it. One lot per purchase.
    order_id push_id NOT NULL,
    product_code varchar(24) COLLATE "C" NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    face_minor bigint NOT NULL,
    cost_minor bigint NOT NULL,
    settled_on date NOT NULL,
    matures_on date NOT NULL,
    custodian_reference varchar(64) NOT NULL,
    CONSTRAINT pk_dealing_lots PRIMARY KEY (id),
    CONSTRAINT uq_dealing_lots_order UNIQUE (order_id),
    CONSTRAINT fk_dealing_lots_order FOREIGN KEY (order_id) REFERENCES dealing_orders (id),
    CONSTRAINT ck_dealing_lots_amounts CHECK (face_minor > 0 AND cost_minor > 0),
    CONSTRAINT ck_dealing_lots_dates CHECK (matures_on > settled_on)
);
CREATE INDEX ix_dealing_lots_instrument ON dealing_lots (product_code, matures_on, settled_on, id);
CREATE TABLE dealing_lot_sales (
    lot_id push_id NOT NULL,
    -- The sale order that took face value from the lot.
    order_id push_id NOT NULL,
    face_minor bigint NOT NULL,
    cost_minor bigint NOT NULL,
    CONSTRAINT pk_dealing_lot_sales PRIMARY KEY (order_id, lot_id),
    CONSTRAINT fk_dealing_lot_sales_lot FOREIGN KEY (lot_id) REFERENCES dealing_lots (id),
    CONSTRAINT fk_dealing_lot_sales_order FOREIGN KEY (order_id) REFERENCES dealing_orders (id),
    -- Cost released is rounded down, so a small part of a lot can release none.
    CONSTRAINT ck_dealing_lot_sales_amounts CHECK (face_minor > 0 AND cost_minor >= 0)
);
CREATE INDEX ix_dealing_lot_sales_lot ON dealing_lot_sales (lot_id)
"""

# Lots and sale lines are written once, by staff, while the order they belong to is being
# settled. The two-key advisory lock serialises everything that takes face value from one lot,
# and the sums are read after it is held, so two sales at once can never oversell a lot.
HOLDINGS_GUARD = """
CREATE FUNCTION finvest_dealing_holdings_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        source dealing_orders%ROWTYPE;
        lot dealing_lots%ROWTYPE;
        sold_face bigint;
        sold_cost bigint;
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'bills held are never changed or removed' USING ERRCODE = '42501';
        END IF;
        IF finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff settle dealing orders' USING ERRCODE = '42501';
        END IF;
        SELECT * INTO source FROM dealing_orders WHERE id = NEW.order_id;
        IF NOT FOUND OR source.status <> 'result_recorded'
            OR source.result_recorded_by = finvest_actor_id() THEN
            RAISE EXCEPTION 'bills move only while a second person settles the order'
                USING ERRCODE = '42501';
        END IF;

        IF TG_TABLE_NAME = 'dealing_lots' THEN
            IF source.kind = 'secondary_sale'
                OR NEW.product_code <> source.product_code
                OR NEW.currency <> source.currency
                OR NEW.face_minor <> source.result_face_minor
                OR NEW.cost_minor <> source.result_amount_minor
                OR NEW.settled_on <> source.result_settled_on
                OR NEW.matures_on <> source.result_matures_on
                OR NEW.custodian_reference <> source.custodian_reference THEN
                RAISE EXCEPTION 'a lot is exactly what its purchase recorded'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;

        PERFORM pg_advisory_xact_lock(hashtext('dealing_lots'), hashtext(NEW.lot_id));
        SELECT * INTO lot FROM dealing_lots WHERE id = NEW.lot_id;
        IF source.kind <> 'secondary_sale'
            OR lot.product_code <> source.product_code
            OR lot.matures_on <> source.result_matures_on
            OR lot.settled_on > source.result_settled_on THEN
            RAISE EXCEPTION 'a sale takes bills of the instrument it recorded'
                USING ERRCODE = '23514';
        END IF;
        SELECT coalesce(sum(face_minor), 0), coalesce(sum(cost_minor), 0)
            INTO sold_face, sold_cost FROM dealing_lot_sales WHERE lot_id = NEW.lot_id;
        sold_face := sold_face + NEW.face_minor;
        sold_cost := sold_cost + NEW.cost_minor;
        IF sold_face > lot.face_minor OR sold_cost > lot.cost_minor
            OR (sold_face = lot.face_minor AND sold_cost <> lot.cost_minor) THEN
            RAISE EXCEPTION 'a sale cannot take more than a lot still holds'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# Whether a settled order has exactly the ledger entry and the bills its result describes.
SETTLEMENT_MATCHES = """
CREATE FUNCTION finvest_dealing_settlement_matches(o dealing_orders) RETURNS boolean
    LANGUAGE plpgsql STABLE SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        sells boolean := o.kind = 'secondary_sale';
        cost bigint;
        face bigint;
        bank bigint;
        bills bigint;
    BEGIN
        IF sells THEN
            SELECT sum(face_minor), sum(cost_minor) INTO face, cost
                FROM dealing_lot_sales WHERE order_id = o.id;
        ELSE
            SELECT face_minor, cost_minor INTO face, cost FROM dealing_lots WHERE order_id = o.id;
        END IF;
        IF face IS DISTINCT FROM o.result_face_minor THEN
            RETURN false;
        END IF;
        IF NOT EXISTS (
            SELECT FROM ledger_journal_entries
            WHERE id = o.settlement_entry_id AND source_type = 'dealing.order'
                AND source_id = o.id AND currency = o.currency
                AND effective_date = o.result_settled_on AND reverses_entry_id IS NULL
        ) THEN
            RETURN false;
        END IF;
        SELECT
            coalesce(sum(line.amount_minor) FILTER (WHERE account.code = 'bank.trust'), 0),
            coalesce(sum(line.amount_minor) FILTER (WHERE account.code = 'dealing.bills'), 0)
            INTO bank, bills
            FROM ledger_lines AS line JOIN ledger_accounts AS account ON account.id = line.account_id
            WHERE line.entry_id = o.settlement_entry_id
                AND account.owner_client_id IS NULL AND account.reference IS NULL;
        IF sells THEN
            RETURN bank = o.result_amount_minor AND bills = -cost;
        END IF;
        RETURN bank = -o.result_amount_minor AND bills = cost;
    END;
    $$
"""


def orders_guard() -> str:
    """The orders guard of 0080 with one more step: a second person settles a recorded result."""
    settle = """
        ELSIF OLD.status = 'result_recorded' AND NEW.status = 'settled' THEN
            IF actor = OLD.result_recorded_by OR NEW.settled_by IS DISTINCT FROM actor
                OR (to_jsonb(NEW) - '{status,settled_by,settled_at,settlement_entry_id}'::text[])
                    <> (to_jsonb(OLD) - '{status,settled_by,settled_at,settlement_entry_id}'::text[])
                THEN
                RAISE EXCEPTION 'an order is settled by a second person, and only settled'
                    USING ERRCODE = '42501';
            END IF;
            IF NOT finvest_dealing_settlement_matches(NEW) THEN
                RAISE EXCEPTION 'a settled order needs the ledger entry and bills its result shows'
                    USING ERRCODE = '23514';
            END IF;
        ELSE"""
    old = _previous_guard()
    marker = "\n        ELSE\n            -- This covers settling, which needs the holdings and the ledger entry."
    if marker not in old:
        raise RuntimeError("The 0080 orders guard is not the one this revision extends")
    return old.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace(
        marker, settle + "\n            -- Any other move is refused.", 1
    )


def _previous_guard() -> str:
    path = Path(__file__).with_name("0080_dealing_orders.py")
    spec = importlib.util.spec_from_file_location("finvest_0080_dealing_orders", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.GUARD)


RULES = [
    "REVOKE ALL ON FUNCTION finvest_dealing_holdings_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_dealing_settlement_matches(dealing_orders) FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION finvest_dealing_settlement_matches(dealing_orders) TO {APP_ROLE}",
    f"GRANT UPDATE (settled_by, settled_at, settlement_entry_id) ON dealing_orders TO {APP_ROLE}",
    # The day close reads settled orders to expect their bank movements.
    f"GRANT SELECT ON dealing_orders TO {WORKER_ROLE}",
    f"CREATE POLICY dealing_orders_worker_read ON dealing_orders FOR SELECT TO {WORKER_ROLE} "
    "USING (true)",
]
for _table in ("dealing_lots", "dealing_lot_sales"):
    RULES += [
        f"CREATE TRIGGER {_table}_guard BEFORE INSERT OR UPDATE OR DELETE ON {_table} "
        "FOR EACH ROW EXECUTE FUNCTION finvest_dealing_holdings_guard()",
        f"CREATE TRIGGER {_table}_no_truncate BEFORE TRUNCATE ON {_table} "
        "FOR EACH STATEMENT EXECUTE FUNCTION finvest_dealing_holdings_guard()",
        f"REVOKE ALL ON {_table} FROM PUBLIC",
        f"GRANT SELECT, INSERT ON {_table} TO {APP_ROLE}",
        # The worker reads holdings for the day close and for maturities.
        f"GRANT SELECT ON {_table} TO {WORKER_ROLE}",
        f"ALTER TABLE {_table} ENABLE ROW LEVEL SECURITY",
        f"CREATE POLICY {_table}_staff ON {_table} TO {APP_ROLE} "
        "USING (finvest_actor_kind() = 'staff') WITH CHECK (finvest_actor_kind() = 'staff')",
        f"CREATE POLICY {_table}_worker_read ON {_table} FOR SELECT TO {WORKER_ROLE} USING (true)",
    ]


def upgrade() -> None:
    statements = [
        *[part.strip() for part in TABLES.split(";\n")],
        HOLDINGS_GUARD,
        SETTLEMENT_MATCHES,
        orders_guard(),
        *RULES,
    ]
    for statement in statements:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM dealing_lots) OR EXISTS (SELECT FROM dealing_lot_sales)
        OR EXISTS (SELECT FROM dealing_orders WHERE status = 'settled') THEN
        RAISE EXCEPTION 'refusing to drop bills held or settled orders';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(_previous_guard().replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.execute("DROP POLICY dealing_orders_worker_read ON dealing_orders")
    op.execute("REVOKE SELECT ON dealing_orders FROM finvest_worker")
    op.execute("DROP TABLE dealing_lot_sales")
    op.execute("DROP TABLE dealing_lots")
    op.execute("DROP FUNCTION finvest_dealing_holdings_guard()")
    op.execute("DROP FUNCTION finvest_dealing_settlement_matches(dealing_orders)")
    for statement in (
        "ALTER TABLE dealing_orders DROP CONSTRAINT uq_dealing_orders_settlement_entry",
        "ALTER TABLE dealing_orders DROP CONSTRAINT ck_dealing_orders_settled_by",
        "ALTER TABLE dealing_orders DROP CONSTRAINT ck_dealing_orders_settled",
        "ALTER TABLE dealing_orders DROP COLUMN settlement_entry_id",
        "ALTER TABLE dealing_orders DROP COLUMN settled_at",
        "ALTER TABLE dealing_orders DROP COLUMN settled_by",
    ):
        op.execute(statement)
