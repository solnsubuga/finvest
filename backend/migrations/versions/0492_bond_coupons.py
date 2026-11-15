"""Bond coupons: paid every six months to the client's payout destination.

Revision ID: 0492_bond_coupons
Revises: 0491_bond_holdings

* `bond_holdings.payout_destination_id`: where the client wants coupons paid. The client
  changes it on their own unpaid or active holding, and nothing else of it.
* `bond_coupons`: one row per holding per coupon date, written by the worker with the coupon
  before tax, the tax withheld and what the client is paid, and the payout made or why the
  money waits. A coupon that waits is sent on once: by the client naming a destination, or by
  the worker. What a coupon was never changes, and none is removed.

Clients see only their own coupons. Staff read every one.
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0492_bond_coupons"
down_revision: str | None = "0491_bond_holdings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE bond_coupons (
    id push_id NOT NULL,
    holding_id push_id NOT NULL,
    client_id push_id NOT NULL,
    coupon_date date NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    gross_minor bigint NOT NULL,
    tax_minor bigint NOT NULL,
    net_minor bigint NOT NULL,
    -- The ledger's `bond.coupon` entry. No foreign key: the ledger's tables are its own.
    entry_id push_id NOT NULL,
    -- `paid_out` once a payout carries it, `held` while it waits for a destination.
    status varchar(10) NOT NULL,
    payout_id push_id,
    -- Why it waits: no destination, or the payout module's refusal.
    payout_refusal varchar(64),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_bond_coupons PRIMARY KEY (id),
    CONSTRAINT uq_bond_coupons_holding_date UNIQUE (holding_id, coupon_date),
    CONSTRAINT uq_bond_coupons_entry UNIQUE (entry_id),
    CONSTRAINT fk_bond_coupons_holding FOREIGN KEY (holding_id) REFERENCES bond_holdings (id),
    CONSTRAINT ck_bond_coupons_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_bond_coupons_amounts CHECK (
        gross_minor > 0 AND tax_minor >= 0 AND net_minor = gross_minor - tax_minor
        AND net_minor > 0),
    CONSTRAINT ck_bond_coupons_status CHECK (status IN ('paid_out', 'held')),
    CONSTRAINT ck_bond_coupons_paid CHECK (
        (status = 'paid_out') = (payout_id IS NOT NULL)
        AND (status = 'held') = (payout_refusal IS NOT NULL))
)
"""

INDEXES = [
    "CREATE INDEX ix_bond_coupons_client ON bond_coupons (client_id, coupon_date DESC)",
    "CREATE INDEX ix_bond_coupons_held ON bond_coupons (coupon_date) WHERE status = 'held'",
]

COUPONS_GUARD = f"""
CREATE FUNCTION finvest_bond_coupons_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'a bond coupon is never removed' USING ERRCODE = '42501';
        END IF;
        IF current_user NOT IN ('{APP_ROLE}', '{WORKER_ROLE}') THEN
            RETURN NEW;
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF current_user <> '{WORKER_ROLE}' THEN
                RAISE EXCEPTION 'a bond coupon is paid by the worker' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'held' OR NEW.status <> 'paid_out'
           OR (current_user = '{APP_ROLE}' AND (finvest_actor_kind() IS DISTINCT FROM 'client'
               OR OLD.client_id IS DISTINCT FROM finvest_actor_id()))
           OR (NEW.id, NEW.holding_id, NEW.client_id, NEW.coupon_date, NEW.currency,
               NEW.gross_minor, NEW.tax_minor, NEW.net_minor, NEW.entry_id, NEW.created_at)
              IS DISTINCT FROM
              (OLD.id, OLD.holding_id, OLD.client_id, OLD.coupon_date, OLD.currency,
               OLD.gross_minor, OLD.tax_minor, OLD.net_minor, OLD.entry_id, OLD.created_at) THEN
            RAISE EXCEPTION 'a held bond coupon is only sent on, once'
                USING ERRCODE = '42501';
        END IF;
        -- Sent on means a payout of this coupon, to its own client.
        IF NOT EXISTS (SELECT FROM payouts p WHERE p.id = NEW.payout_id
                       AND p.client_id = OLD.client_id AND p.source_reference = OLD.id) THEN
            RAISE EXCEPTION 'a bond coupon is sent on by a payout of its own'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# 0491's guard, now also letting the client choose where their coupons go.
HOLDINGS_GUARD = f"""
CREATE OR REPLACE FUNCTION finvest_bond_holdings_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        issue record;
        latest record;
    BEGIN
        IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'a bond holding is never removed' USING ERRCODE = '42501';
        END IF;
        IF current_user NOT IN ('{APP_ROLE}', '{WORKER_ROLE}') THEN
            RETURN NEW;
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF current_user <> '{APP_ROLE}' OR finvest_actor_kind() IS DISTINCT FROM 'client'
               OR NEW.client_id IS DISTINCT FROM finvest_actor_id()
               OR NEW.status <> 'unpaid' THEN
                RAISE EXCEPTION 'a bond holding is bought by the client signed in'
                    USING ERRCODE = '42501';
            END IF;
            SELECT status, currency INTO issue FROM bond_issues WHERE id = NEW.issue_id;
            SELECT id, price INTO latest FROM bond_prices WHERE issue_id = NEW.issue_id
                ORDER BY effective_at DESC, id DESC LIMIT 1;
            IF issue.status IS DISTINCT FROM 'live' OR issue.currency <> NEW.currency
               OR latest.id IS DISTINCT FROM NEW.price_id
               OR latest.price IS DISTINCT FROM NEW.price THEN
                RAISE EXCEPTION 'a bond is bought only while it is open, at the price in force'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF (NEW.id, NEW.client_id, NEW.issue_id, NEW.currency, NEW.face_minor, NEW.price,
            NEW.price_id, NEW.accrued_minor, NEW.cost_minor, NEW.quoted_on, NEW.created_at,
            NEW.pay_by)
           IS DISTINCT FROM
           (OLD.id, OLD.client_id, OLD.issue_id, OLD.currency, OLD.face_minor, OLD.price,
            OLD.price_id, OLD.accrued_minor, OLD.cost_minor, OLD.quoted_on, OLD.created_at,
            OLD.pay_by) THEN
            RAISE EXCEPTION 'what a bond holding bought never changes' USING ERRCODE = '42501';
        END IF;
        IF current_user = '{APP_ROLE}' THEN
            IF finvest_actor_kind() IS DISTINCT FROM 'client'
               OR OLD.client_id IS DISTINCT FROM finvest_actor_id()
               OR OLD.status = 'cancelled'
               OR (NEW.status, NEW.start_date, NEW.started_at, NEW.start_entry_id,
                   NEW.cancelled_at)
                  IS DISTINCT FROM
                  (OLD.status, OLD.start_date, OLD.started_at, OLD.start_entry_id,
                   OLD.cancelled_at) THEN
                RAISE EXCEPTION 'a client only chooses where their coupons go'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'unpaid' OR NEW.status = 'unpaid'
           OR NEW.payout_destination_id IS DISTINCT FROM OLD.payout_destination_id THEN
            RAISE EXCEPTION 'a bond holding is only started or cancelled, once, by the worker'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role names

RULES = [
    "ALTER TABLE bond_holdings ADD COLUMN payout_destination_id push_id",
    f"GRANT UPDATE (payout_destination_id, updated_at) ON bond_holdings TO {APP_ROLE}",
    f"CREATE POLICY bond_holdings_app_update ON bond_holdings FOR UPDATE TO {APP_ROLE} USING ("
    "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    "REVOKE ALL ON FUNCTION finvest_bond_coupons_guard() FROM PUBLIC",
    "CREATE TRIGGER bond_coupons_guard BEFORE INSERT OR UPDATE OR DELETE ON bond_coupons "
    "FOR EACH ROW EXECUTE FUNCTION finvest_bond_coupons_guard()",
    "CREATE TRIGGER bond_coupons_no_truncate BEFORE TRUNCATE ON bond_coupons "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_bond_coupons_guard()",
    "REVOKE ALL ON bond_coupons FROM PUBLIC",
    f"GRANT SELECT, UPDATE (status, payout_id, payout_refusal, updated_at) "
    f"ON bond_coupons TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (status, payout_id, payout_refusal, updated_at) "
    f"ON bond_coupons TO {WORKER_ROLE}",
    "ALTER TABLE bond_coupons ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY bond_coupons_app_read ON bond_coupons FOR SELECT TO {APP_ROLE} USING ("
    "(finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY bond_coupons_app_update ON bond_coupons FOR UPDATE TO {APP_ROLE} USING ("
    "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY bond_coupons_worker ON bond_coupons TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (true)",
]


def upgrade() -> None:
    for statement in [TABLE, *INDEXES, COUPONS_GUARD, HOLDINGS_GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM bond_coupons) THEN
        RAISE EXCEPTION 'refusing to drop the coupons paid to clients';
    END IF;
END
$$
"""


def _previous_guard() -> str:
    path = Path(__file__).with_name("0491_bond_holdings.py")
    spec = importlib.util.spec_from_file_location("finvest_0491_bond_holdings", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.GUARD).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    for statement in [
        "DROP TABLE bond_coupons",
        "DROP FUNCTION finvest_bond_coupons_guard()",
        "DROP POLICY bond_holdings_app_update ON bond_holdings",
        f"REVOKE UPDATE (payout_destination_id, updated_at) ON bond_holdings FROM {APP_ROLE}",
        "ALTER TABLE bond_holdings DROP COLUMN payout_destination_id",
        _previous_guard(),
    ]:
        op.execute(statement)
