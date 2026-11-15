"""Bond maturity: each holding repaid its face value and last coupon, once.

Revision ID: 0494_bond_maturity
Revises: 0493_product_types

* `bond_redemptions`: one row per holding, written by the worker on or after its issue's
  maturity date, for an active holding, with its face value and cost exactly as bought, the
  last coupon before tax, the tax and after tax, what the client is paid, and the payout made or
  why the money waits. A repayment that waits is sent on once: by the client naming a
  destination, or by the worker. What a repayment was never changes, and none is removed.
* `bond_holdings.status` may now be `matured`, set by the worker only, from `active`, once the
  holding's repayment is saved.
* A fix to 0493's product type check: filling in a missing type for a new bond issue read
  `NEW.action`, which only bond changes have, so it failed instead. Kept on a downgrade.

Clients see only their own repayments. Staff read every one.
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0494_bond_maturity"
down_revision: str | None = "0493_product_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE bond_redemptions (
    id push_id NOT NULL,
    holding_id push_id NOT NULL,
    client_id push_id NOT NULL,
    -- The business day it was repaid: the maturity date, or later if a run was missed.
    redeemed_on date NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    face_minor bigint NOT NULL,
    -- What the client paid for the holding, taken out of their bond holding.
    cost_minor bigint NOT NULL,
    coupon_gross_minor bigint NOT NULL,
    coupon_tax_minor bigint NOT NULL,
    coupon_net_minor bigint NOT NULL,
    -- What the client is paid: the face value and the last coupon after tax.
    amount_minor bigint NOT NULL,
    -- The ledger's `bond.matured` entry. No foreign key: the ledger's tables are its own.
    entry_id push_id NOT NULL,
    status varchar(10) NOT NULL,
    payout_id push_id,
    payout_refusal varchar(64),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_bond_redemptions PRIMARY KEY (id),
    CONSTRAINT uq_bond_redemptions_holding UNIQUE (holding_id),
    CONSTRAINT uq_bond_redemptions_entry UNIQUE (entry_id),
    CONSTRAINT fk_bond_redemptions_holding FOREIGN KEY (holding_id)
        REFERENCES bond_holdings (id),
    CONSTRAINT ck_bond_redemptions_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_bond_redemptions_amounts CHECK (
        face_minor > 0 AND cost_minor > 0 AND coupon_gross_minor > 0 AND coupon_tax_minor >= 0
        AND coupon_net_minor = coupon_gross_minor - coupon_tax_minor AND coupon_net_minor > 0
        AND amount_minor = face_minor + coupon_net_minor),
    CONSTRAINT ck_bond_redemptions_status CHECK (status IN ('paid_out', 'held')),
    CONSTRAINT ck_bond_redemptions_paid CHECK (
        (status = 'paid_out') = (payout_id IS NOT NULL)
        AND (status = 'held') = (payout_refusal IS NOT NULL))
)
"""

INDEXES = [
    "CREATE INDEX ix_bond_redemptions_client ON bond_redemptions (client_id, redeemed_on DESC)",
    "CREATE INDEX ix_bond_redemptions_held ON bond_redemptions (redeemed_on) WHERE status = 'held'",
]

REDEMPTIONS_GUARD = f"""
CREATE FUNCTION finvest_bond_redemptions_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        held record;
    BEGIN
        IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'a bond repayment is never removed' USING ERRCODE = '42501';
        END IF;
        IF current_user NOT IN ('{APP_ROLE}', '{WORKER_ROLE}') THEN
            RETURN NEW;
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF current_user <> '{WORKER_ROLE}' THEN
                RAISE EXCEPTION 'a bond is repaid by the worker' USING ERRCODE = '42501';
            END IF;
            SELECT h.status, h.client_id, h.currency, h.face_minor, h.cost_minor,
                   b.maturity_date INTO held
                FROM bond_holdings h JOIN bond_issues b ON b.id = h.issue_id
                WHERE h.id = NEW.holding_id;
            IF held.status IS DISTINCT FROM 'active' OR held.maturity_date > NEW.redeemed_on
               OR (held.client_id, held.currency, held.face_minor, held.cost_minor)
                  IS DISTINCT FROM
                  (NEW.client_id, NEW.currency, NEW.face_minor, NEW.cost_minor) THEN
                RAISE EXCEPTION 'a bond holding is repaid what it bought, once it has matured'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'held' OR NEW.status <> 'paid_out'
           OR (current_user = '{APP_ROLE}' AND (finvest_actor_kind() IS DISTINCT FROM 'client'
               OR OLD.client_id IS DISTINCT FROM finvest_actor_id()))
           OR (NEW.id, NEW.holding_id, NEW.client_id, NEW.redeemed_on, NEW.currency,
               NEW.face_minor, NEW.cost_minor, NEW.coupon_gross_minor, NEW.coupon_tax_minor,
               NEW.coupon_net_minor, NEW.amount_minor, NEW.entry_id, NEW.created_at)
              IS DISTINCT FROM
              (OLD.id, OLD.holding_id, OLD.client_id, OLD.redeemed_on, OLD.currency,
               OLD.face_minor, OLD.cost_minor, OLD.coupon_gross_minor, OLD.coupon_tax_minor,
               OLD.coupon_net_minor, OLD.amount_minor, OLD.entry_id, OLD.created_at) THEN
            RAISE EXCEPTION 'a held bond repayment is only sent on, once'
                USING ERRCODE = '42501';
        END IF;
        IF NOT EXISTS (SELECT FROM payouts p WHERE p.id = NEW.payout_id
                       AND p.client_id = OLD.client_id AND p.source_reference = OLD.id) THEN
            RAISE EXCEPTION 'a bond repayment is sent on by a payout of its own'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role names

# 0492's guard, now also letting the worker mark a repaid holding matured.
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
               OR OLD.status IN ('cancelled', 'matured')
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
        IF OLD.status = 'active' AND NEW.status = 'matured' THEN
            IF (NEW.start_date, NEW.started_at, NEW.start_entry_id, NEW.cancelled_at,
                NEW.payout_destination_id)
               IS DISTINCT FROM
               (OLD.start_date, OLD.started_at, OLD.start_entry_id, OLD.cancelled_at,
                OLD.payout_destination_id)
               OR NOT EXISTS (SELECT FROM bond_redemptions r WHERE r.holding_id = NEW.id) THEN
                RAISE EXCEPTION 'a bond holding matures only once it has been repaid'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'unpaid' OR NEW.status IN ('unpaid', 'matured')
           OR NEW.payout_destination_id IS DISTINCT FROM OLD.payout_destination_id THEN
            RAISE EXCEPTION 'a bond holding is only started or cancelled, once, by the worker'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role names

CONSTRAINTS = [
    "ALTER TABLE bond_holdings DROP CONSTRAINT ck_bond_holdings_status",
    "ALTER TABLE bond_holdings ADD CONSTRAINT ck_bond_holdings_status "
    "CHECK (status IN ('unpaid', 'active', 'matured', 'cancelled'))",
    "ALTER TABLE bond_holdings DROP CONSTRAINT ck_bond_holdings_started",
    "ALTER TABLE bond_holdings ADD CONSTRAINT ck_bond_holdings_started CHECK ("
    "(status IN ('active', 'matured')) = (start_date IS NOT NULL) "
    "AND (start_date IS NULL) = (started_at IS NULL) "
    "AND (start_date IS NULL) = (start_entry_id IS NULL) "
    "AND (start_date IS NULL OR start_date >= quoted_on))",
]

RULES = [
    "REVOKE ALL ON FUNCTION finvest_bond_redemptions_guard() FROM PUBLIC",
    "CREATE TRIGGER bond_redemptions_guard BEFORE INSERT OR UPDATE OR DELETE "
    "ON bond_redemptions FOR EACH ROW EXECUTE FUNCTION finvest_bond_redemptions_guard()",
    "CREATE TRIGGER bond_redemptions_no_truncate BEFORE TRUNCATE ON bond_redemptions "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_bond_redemptions_guard()",
    "REVOKE ALL ON bond_redemptions FROM PUBLIC",
    f"GRANT SELECT, UPDATE (status, payout_id, payout_refusal, updated_at) "
    f"ON bond_redemptions TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (status, payout_id, payout_refusal, updated_at) "
    f"ON bond_redemptions TO {WORKER_ROLE}",
    "ALTER TABLE bond_redemptions ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY bond_redemptions_app_read ON bond_redemptions FOR SELECT TO {APP_ROLE} "
    "USING ((finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY bond_redemptions_app_update ON bond_redemptions FOR UPDATE TO {APP_ROLE} "
    "USING (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY bond_redemptions_worker ON bond_redemptions TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (true)",
]


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"finvest_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BROKEN_FILL = """            ELSIF TG_TABLE_NAME = 'bond_changes' AND NEW.action = 'create' THEN
                NEW.type_id := '-PTYPE0000000000000T';"""
_FIXED_FILL = """            ELSIF TG_TABLE_NAME = 'bond_changes' THEN
                IF NEW.action = 'create' THEN
                    NEW.type_id := '-PTYPE0000000000000T';
                END IF;"""


def _type_check() -> str:
    """0493's type check, filling in a bond issue's type without reading a column it lacks."""
    check = str(_load("0493_product_types").PRODUCT_TYPE_CHECK)
    assert _BROKEN_FILL in check
    return check.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace(
        _BROKEN_FILL, _FIXED_FILL
    )


def upgrade() -> None:
    for statement in [
        TABLE,
        *INDEXES,
        REDEMPTIONS_GUARD,
        *CONSTRAINTS,
        HOLDINGS_GUARD,
        *RULES,
        _type_check(),
    ]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM bond_redemptions)
       OR EXISTS (SELECT FROM bond_holdings WHERE status = 'matured') THEN
        RAISE EXCEPTION 'refusing to drop the bonds repaid to clients';
    END IF;
END
$$
"""


def _previous_guard() -> str:
    return str(_load("0492_bond_coupons").HOLDINGS_GUARD)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    for statement in [
        "DROP TABLE bond_redemptions",
        "DROP FUNCTION finvest_bond_redemptions_guard()",
        _previous_guard(),
        "ALTER TABLE bond_holdings DROP CONSTRAINT ck_bond_holdings_started",
        "ALTER TABLE bond_holdings ADD CONSTRAINT ck_bond_holdings_started CHECK ("
        "(status = 'active') = (start_date IS NOT NULL) "
        "AND (start_date IS NULL) = (started_at IS NULL) "
        "AND (start_date IS NULL) = (start_entry_id IS NULL) "
        "AND (start_date IS NULL OR start_date >= quoted_on))",
        "ALTER TABLE bond_holdings DROP CONSTRAINT ck_bond_holdings_status",
        "ALTER TABLE bond_holdings ADD CONSTRAINT ck_bond_holdings_status "
        "CHECK (status IN ('unpaid', 'active', 'cancelled'))",
    ]:
        op.execute(statement)
