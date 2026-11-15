"""Bond sales: a client's holding sold early through the dealing desk, by two people.

Revision ID: 0495_bond_sales
Revises: 0494_bond_maturity

* `bond_sales`: one row per ask. The client asks to sell a whole active holding, naming where
  the money goes. One member of staff records the price it fetched and the day it settled, and
  a different one approves it, or sends it back. The worker then pays the client and saves what
  it paid. A sale that waits for a destination is sent on once: by the client, or the worker.
  The client may cancel before the sale is priced, and the worker closes an ask whose bond
  matured first. What an ask was, and what a sale paid, never changes, and none is removed.
* `bond_holdings.status` may now be `sold`, set by the worker only, from `active`, once the
  holding's sale is saved.

Clients see only their own sales. Staff read every one.
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0495_bond_sales"
down_revision: str | None = "0494_bond_maturity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE bond_sales (
    id push_id NOT NULL,
    holding_id push_id NOT NULL,
    client_id push_id NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    -- The holding as bought: what is sold, and what comes out of the client's bond holding.
    face_minor bigint NOT NULL,
    cost_minor bigint NOT NULL,
    -- Where the client asked for the money to go.
    destination_id push_id NOT NULL,
    requested_at timestamptz NOT NULL,
    status varchar(10) NOT NULL,
    -- The dealing desk's sale, recorded by one member of staff.
    price integer,
    sold_on date,
    reason varchar(500),
    priced_by push_id,
    priced_at timestamptz,
    -- The second member of staff, who approved it.
    decided_by push_id,
    decided_at timestamptz,
    -- Why it was last sent back.
    note varchar(500),
    -- What the worker paid, once it is sold.
    clean_minor bigint,
    accrued_minor bigint,
    tax_minor bigint,
    amount_minor bigint,
    -- The ledger's `bond.sold` entry. No foreign key: the ledger's tables are its own.
    entry_id push_id,
    settled_at timestamptz,
    payout_status varchar(10),
    payout_id push_id,
    payout_refusal varchar(64),
    closed_at timestamptz,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_bond_sales PRIMARY KEY (id),
    CONSTRAINT uq_bond_sales_entry UNIQUE (entry_id),
    CONSTRAINT fk_bond_sales_holding FOREIGN KEY (holding_id) REFERENCES bond_holdings (id),
    CONSTRAINT ck_bond_sales_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_bond_sales_holding CHECK (face_minor > 0 AND cost_minor > 0),
    CONSTRAINT ck_bond_sales_status CHECK (
        status IN ('requested', 'priced', 'approved', 'sold', 'cancelled', 'closed')),
    CONSTRAINT ck_bond_sales_priced CHECK (
        -- An ask closed at maturity keeps any price it had.
        (status NOT IN ('requested', 'cancelled') OR price IS NULL)
        AND (status NOT IN ('priced', 'approved', 'sold') OR price IS NOT NULL)
        AND (price IS NULL) = (sold_on IS NULL) AND (price IS NULL) = (reason IS NULL)
        AND (price IS NULL) = (priced_by IS NULL) AND (price IS NULL) = (priced_at IS NULL)
        AND (price IS NULL OR price BETWEEN 500000 AND 1500000)),
    CONSTRAINT ck_bond_sales_decided CHECK (
        (status IN ('approved', 'sold')) = (decided_by IS NOT NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (decided_by IS NULL OR decided_by <> priced_by)),
    CONSTRAINT ck_bond_sales_paid CHECK (
        (status = 'sold') = (amount_minor IS NOT NULL)
        AND (amount_minor IS NULL) = (clean_minor IS NULL)
        AND (amount_minor IS NULL) = (accrued_minor IS NULL)
        AND (amount_minor IS NULL) = (tax_minor IS NULL)
        AND (amount_minor IS NULL) = (entry_id IS NULL)
        AND (amount_minor IS NULL) = (settled_at IS NULL)
        AND (amount_minor IS NULL) = (payout_status IS NULL)
        AND (amount_minor IS NULL OR (clean_minor > 0 AND accrued_minor >= 0
             AND tax_minor >= 0 AND tax_minor <= accrued_minor
             AND amount_minor = clean_minor + accrued_minor - tax_minor))),
    CONSTRAINT ck_bond_sales_payout CHECK (
        (payout_status IS NULL OR payout_status IN ('paid_out', 'held'))
        AND (payout_status IS NOT DISTINCT FROM 'paid_out') = (payout_id IS NOT NULL)
        AND (payout_status IS NOT DISTINCT FROM 'held') = (payout_refusal IS NOT NULL)),
    CONSTRAINT ck_bond_sales_closed CHECK (
        (status IN ('cancelled', 'closed')) = (closed_at IS NOT NULL))
)
"""

INDEXES = [
    # One sale going per holding, and none after it is sold.
    "CREATE UNIQUE INDEX uq_bond_sales_holding_open ON bond_sales (holding_id) "
    "WHERE status IN ('requested', 'priced', 'approved', 'sold')",
    "CREATE INDEX ix_bond_sales_client ON bond_sales (client_id, requested_at DESC)",
    "CREATE INDEX ix_bond_sales_waiting ON bond_sales (requested_at) "
    "WHERE status IN ('requested', 'priced', 'approved')",
]

SALES_GUARD = f"""
CREATE FUNCTION finvest_bond_sales_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        held record;
        actor_kind text;
        actor_id text;
    BEGIN
        IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'a bond sale is never removed' USING ERRCODE = '42501';
        END IF;
        IF current_user NOT IN ('{APP_ROLE}', '{WORKER_ROLE}') THEN
            RETURN NEW;
        END IF;
        actor_kind := finvest_actor_kind();
        actor_id := finvest_actor_id();
        SELECT h.status, h.client_id, h.currency, h.face_minor, h.cost_minor INTO held
            FROM bond_holdings h WHERE h.id = NEW.holding_id;
        IF TG_OP = 'INSERT' THEN
            IF current_user <> '{APP_ROLE}' OR actor_kind IS DISTINCT FROM 'client'
               OR NEW.client_id IS DISTINCT FROM actor_id OR NEW.status <> 'requested'
               OR held.status IS DISTINCT FROM 'active'
               OR (held.client_id, held.currency, held.face_minor, held.cost_minor)
                  IS DISTINCT FROM
                  (NEW.client_id, NEW.currency, NEW.face_minor, NEW.cost_minor)
               OR NEW.note IS NOT NULL THEN
                RAISE EXCEPTION 'a bond sale is asked for by the client who holds it'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF (NEW.id, NEW.holding_id, NEW.client_id, NEW.currency, NEW.face_minor,
            NEW.cost_minor, NEW.destination_id, NEW.requested_at)
           IS DISTINCT FROM
           (OLD.id, OLD.holding_id, OLD.client_id, OLD.currency, OLD.face_minor,
            OLD.cost_minor, OLD.destination_id, OLD.requested_at) THEN
            RAISE EXCEPTION 'what a client asked to sell never changes' USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'requested' AND NEW.status = 'cancelled' THEN
            IF current_user <> '{APP_ROLE}' OR actor_kind IS DISTINCT FROM 'client'
               OR OLD.client_id IS DISTINCT FROM actor_id THEN
                RAISE EXCEPTION 'only the client cancels their ask to sell'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF OLD.status = 'requested' AND NEW.status = 'priced' THEN
            IF current_user <> '{APP_ROLE}' OR actor_kind IS DISTINCT FROM 'staff'
               OR NEW.priced_by IS DISTINCT FROM actor_id
               OR held.status IS DISTINCT FROM 'active' THEN
                RAISE EXCEPTION 'a bond sale is priced by the member of staff signed in'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF OLD.status = 'priced' AND NEW.status = 'requested' THEN
            -- Withdrawn by whoever priced it, or sent back with a note by someone else.
            IF current_user <> '{APP_ROLE}' OR actor_kind IS DISTINCT FROM 'staff'
               OR NOT (OLD.priced_by = actor_id AND NEW.note IS NOT DISTINCT FROM OLD.note
                       OR OLD.priced_by <> actor_id AND NEW.note IS NOT NULL) THEN
                RAISE EXCEPTION 'a priced sale is withdrawn by its maker or sent back by another'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF OLD.status = 'priced' AND NEW.status = 'approved' THEN
            IF current_user <> '{APP_ROLE}' OR actor_kind IS DISTINCT FROM 'staff'
               OR NEW.decided_by IS DISTINCT FROM actor_id
               OR held.status IS DISTINCT FROM 'active'
               OR (NEW.price, NEW.sold_on, NEW.reason, NEW.priced_by, NEW.priced_at, NEW.note)
                  IS DISTINCT FROM
                  (OLD.price, OLD.sold_on, OLD.reason, OLD.priced_by, OLD.priced_at, OLD.note) THEN
                RAISE EXCEPTION 'a bond sale is approved as priced, by a second person'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF OLD.status = 'approved' AND NEW.status = 'sold' THEN
            IF current_user <> '{WORKER_ROLE}' OR held.status IS DISTINCT FROM 'active'
               OR (NEW.price, NEW.sold_on, NEW.reason, NEW.priced_by, NEW.priced_at, NEW.note,
                   NEW.decided_by, NEW.decided_at)
                  IS DISTINCT FROM
                  (OLD.price, OLD.sold_on, OLD.reason, OLD.priced_by, OLD.priced_at, OLD.note,
                   OLD.decided_by, OLD.decided_at) THEN
                RAISE EXCEPTION 'an approved bond sale is paid by the worker, as approved'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF OLD.status = 'sold' AND NEW.status = 'sold' THEN
            IF OLD.payout_status <> 'held' OR NEW.payout_status <> 'paid_out'
               OR (current_user = '{APP_ROLE}' AND (actor_kind IS DISTINCT FROM 'client'
                   OR OLD.client_id IS DISTINCT FROM actor_id))
               OR (NEW.price, NEW.sold_on, NEW.reason, NEW.priced_by, NEW.priced_at, NEW.note,
                   NEW.decided_by, NEW.decided_at, NEW.clean_minor, NEW.accrued_minor,
                   NEW.tax_minor, NEW.amount_minor, NEW.entry_id, NEW.settled_at)
                  IS DISTINCT FROM
                  (OLD.price, OLD.sold_on, OLD.reason, OLD.priced_by, OLD.priced_at, OLD.note,
                   OLD.decided_by, OLD.decided_at, OLD.clean_minor, OLD.accrued_minor,
                   OLD.tax_minor, OLD.amount_minor, OLD.entry_id, OLD.settled_at) THEN
                RAISE EXCEPTION 'a held bond sale is only sent on, once' USING ERRCODE = '42501';
            END IF;
            IF NOT EXISTS (SELECT FROM payouts p WHERE p.id = NEW.payout_id
                           AND p.client_id = OLD.client_id AND p.source_reference = OLD.id) THEN
                RAISE EXCEPTION 'a bond sale is sent on by a payout of its own'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF OLD.status IN ('requested', 'priced') AND NEW.status = 'closed' THEN
            IF current_user <> '{WORKER_ROLE}' OR held.status IS DISTINCT FROM 'matured' THEN
                RAISE EXCEPTION 'an ask to sell is closed only when its bond matured first'
                    USING ERRCODE = '42501';
            END IF;
        ELSE
            RAISE EXCEPTION 'a bond sale cannot move from % to %', OLD.status, NEW.status
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status <> 'sold' AND (NEW.clean_minor, NEW.accrued_minor, NEW.tax_minor,
                NEW.amount_minor, NEW.entry_id, NEW.settled_at, NEW.payout_status,
                NEW.payout_id, NEW.payout_refusal) IS DISTINCT FROM
               (OLD.clean_minor, OLD.accrued_minor, OLD.tax_minor, OLD.amount_minor,
                OLD.entry_id, OLD.settled_at, OLD.payout_status, OLD.payout_id,
                OLD.payout_refusal) THEN
            RAISE EXCEPTION 'only the worker records what a bond sale paid'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role names

# 0494's guard, now also letting the worker mark a sold holding sold.
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
               OR OLD.status IN ('cancelled', 'matured', 'sold')
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
        IF OLD.status = 'active' AND NEW.status IN ('matured', 'sold') THEN
            IF (NEW.start_date, NEW.started_at, NEW.start_entry_id, NEW.cancelled_at,
                NEW.payout_destination_id)
               IS DISTINCT FROM
               (OLD.start_date, OLD.started_at, OLD.start_entry_id, OLD.cancelled_at,
                OLD.payout_destination_id) THEN
                RAISE EXCEPTION 'a bond holding closes as it was' USING ERRCODE = '42501';
            END IF;
            IF NEW.status = 'matured'
               AND NOT EXISTS (SELECT FROM bond_redemptions r WHERE r.holding_id = NEW.id) THEN
                RAISE EXCEPTION 'a bond holding matures only once it has been repaid'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.status = 'sold' AND NOT EXISTS (
                    SELECT FROM bond_sales s WHERE s.holding_id = NEW.id AND s.status = 'sold') THEN
                RAISE EXCEPTION 'a bond holding is sold only once its sale has been paid'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'unpaid' OR NEW.status IN ('unpaid', 'matured', 'sold')
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
    "CHECK (status IN ('unpaid', 'active', 'matured', 'sold', 'cancelled'))",
    "ALTER TABLE bond_holdings DROP CONSTRAINT ck_bond_holdings_started",
    "ALTER TABLE bond_holdings ADD CONSTRAINT ck_bond_holdings_started CHECK ("
    "(status IN ('active', 'matured', 'sold')) = (start_date IS NOT NULL) "
    "AND (start_date IS NULL) = (started_at IS NULL) "
    "AND (start_date IS NULL) = (start_entry_id IS NULL) "
    "AND (start_date IS NULL OR start_date >= quoted_on))",
]

_APP_COLUMNS = (
    "status, price, sold_on, reason, priced_by, priced_at, decided_by, decided_at, note, "
    "payout_status, payout_id, payout_refusal, closed_at, updated_at"
)
_WORKER_COLUMNS = (
    "status, clean_minor, accrued_minor, tax_minor, amount_minor, entry_id, settled_at, "
    "payout_status, payout_id, payout_refusal, closed_at, updated_at"
)

RULES = [
    "REVOKE ALL ON FUNCTION finvest_bond_sales_guard() FROM PUBLIC",
    "CREATE TRIGGER bond_sales_guard BEFORE INSERT OR UPDATE OR DELETE "
    "ON bond_sales FOR EACH ROW EXECUTE FUNCTION finvest_bond_sales_guard()",
    "CREATE TRIGGER bond_sales_no_truncate BEFORE TRUNCATE ON bond_sales "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_bond_sales_guard()",
    "REVOKE ALL ON bond_sales FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE ({_APP_COLUMNS}) ON bond_sales TO {APP_ROLE}",
    f"GRANT SELECT, UPDATE ({_WORKER_COLUMNS}) ON bond_sales TO {WORKER_ROLE}",
    "ALTER TABLE bond_sales ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY bond_sales_app_read ON bond_sales FOR SELECT TO {APP_ROLE} "
    "USING ((finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY bond_sales_app_insert ON bond_sales FOR INSERT TO {APP_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY bond_sales_app_update ON bond_sales FOR UPDATE TO {APP_ROLE} "
    "USING ((finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY bond_sales_worker ON bond_sales TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (true)",
]


def upgrade() -> None:
    for statement in [TABLE, *INDEXES, SALES_GUARD, *CONSTRAINTS, HOLDINGS_GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM bond_sales)
       OR EXISTS (SELECT FROM bond_holdings WHERE status = 'sold') THEN
        RAISE EXCEPTION 'refusing to drop the bonds clients asked to sell';
    END IF;
END
$$
"""


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"finvest_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    for statement in [
        "DROP TABLE bond_sales",
        "DROP FUNCTION finvest_bond_sales_guard()",
        str(_load("0494_bond_maturity").HOLDINGS_GUARD),
        *_load("0494_bond_maturity").CONSTRAINTS,
    ]:
        op.execute(statement)
