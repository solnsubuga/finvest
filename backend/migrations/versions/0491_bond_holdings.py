"""Clients' holdings of bond issues, and payments for them.

Revision ID: 0491_bond_holdings
Revises: 0490_bond_issues

* `bond_holdings`: a client confirms a quote for a face value of one live issue at its price
  in force. The price, the accrued interest and the cost are fixed then, and the holding waits
  for one payment of exactly the cost. Only the client saves it, for themselves, at the price
  in force. Only the worker moves it on: to active when the payment is confirmed, or to
  cancelled when nobody paid in time. What was bought never changes, and nothing is removed.
* `payment_deposits` takes a second purpose, `bond`, whose ID is a holding's. A holding takes
  one live payment, as an investment does.

Clients see only their own holdings. Staff read every one, and the worker moves them on.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0491_bond_holdings"
down_revision: str | None = "0490_bond_issues"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE bond_holdings (
    id push_id NOT NULL,
    client_id push_id NOT NULL,
    issue_id push_id NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    face_minor bigint NOT NULL,
    -- The price in force when the client confirmed, and the row it came from.
    price integer NOT NULL,
    price_id push_id NOT NULL,
    -- Interest before tax since the last coupon, as at the day of the quote.
    accrued_minor bigint NOT NULL,
    -- What the client pays: the clean amount and the accrued interest.
    cost_minor bigint NOT NULL,
    quoted_on date NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'unpaid',
    created_at timestamptz NOT NULL,
    pay_by timestamptz NOT NULL,
    -- The business day the payment was confirmed. Coupons dated after it are paid.
    start_date date,
    started_at timestamptz,
    -- The ledger's `bond.bought` entry. No foreign key: the ledger's tables are its own.
    start_entry_id push_id,
    cancelled_at timestamptz,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_bond_holdings PRIMARY KEY (id),
    CONSTRAINT fk_bond_holdings_issue FOREIGN KEY (issue_id) REFERENCES bond_issues (id),
    CONSTRAINT fk_bond_holdings_price FOREIGN KEY (price_id) REFERENCES bond_prices (id),
    CONSTRAINT uq_bond_holdings_start_entry UNIQUE (start_entry_id),
    CONSTRAINT ck_bond_holdings_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_bond_holdings_face CHECK (face_minor > 0),
    CONSTRAINT ck_bond_holdings_price CHECK (price BETWEEN 500000 AND 1500000),
    CONSTRAINT ck_bond_holdings_cost CHECK (accrued_minor >= 0 AND cost_minor > accrued_minor),
    CONSTRAINT ck_bond_holdings_status CHECK (status IN ('unpaid', 'active', 'cancelled')),
    CONSTRAINT ck_bond_holdings_pay_by CHECK (pay_by > created_at),
    CONSTRAINT ck_bond_holdings_started CHECK (
        (status = 'active') = (start_date IS NOT NULL)
        AND (start_date IS NULL) = (started_at IS NULL)
        AND (start_date IS NULL) = (start_entry_id IS NULL)
        AND (start_date IS NULL OR start_date >= quoted_on)),
    CONSTRAINT ck_bond_holdings_cancelled CHECK ((status = 'cancelled') = (cancelled_at IS NOT NULL))
)
"""

INDEXES = [
    "CREATE INDEX ix_bond_holdings_client ON bond_holdings (client_id, created_at DESC)",
    "CREATE INDEX ix_bond_holdings_unpaid ON bond_holdings (pay_by) WHERE status = 'unpaid'",
    "CREATE INDEX ix_bond_holdings_issue ON bond_holdings (issue_id) WHERE status = 'active'",
]

GUARD = f"""
CREATE FUNCTION finvest_bond_holdings_guard() RETURNS trigger
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
        IF current_user <> '{WORKER_ROLE}' OR OLD.status <> 'unpaid' OR NEW.status = 'unpaid'
           OR (NEW.id, NEW.client_id, NEW.issue_id, NEW.currency, NEW.face_minor, NEW.price,
               NEW.price_id, NEW.accrued_minor, NEW.cost_minor, NEW.quoted_on, NEW.created_at,
               NEW.pay_by)
              IS DISTINCT FROM
              (OLD.id, OLD.client_id, OLD.issue_id, OLD.currency, OLD.face_minor, OLD.price,
               OLD.price_id, OLD.accrued_minor, OLD.cost_minor, OLD.quoted_on, OLD.created_at,
               OLD.pay_by) THEN
            RAISE EXCEPTION 'a bond holding is only started or cancelled, once, by the worker'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role names

RULES = [
    "REVOKE ALL ON FUNCTION finvest_bond_holdings_guard() FROM PUBLIC",
    "CREATE TRIGGER bond_holdings_guard BEFORE INSERT OR UPDATE OR DELETE ON bond_holdings "
    "FOR EACH ROW EXECUTE FUNCTION finvest_bond_holdings_guard()",
    "CREATE TRIGGER bond_holdings_no_truncate BEFORE TRUNCATE ON bond_holdings "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_bond_holdings_guard()",
    "REVOKE ALL ON bond_holdings FROM PUBLIC",
    f"GRANT SELECT, INSERT ON bond_holdings TO {APP_ROLE}",
    f"GRANT SELECT, UPDATE (status, start_date, started_at, start_entry_id, cancelled_at, "
    f"updated_at) ON bond_holdings TO {WORKER_ROLE}",
    "ALTER TABLE bond_holdings ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY bond_holdings_app_read ON bond_holdings FOR SELECT TO {APP_ROLE} USING ("
    "(finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY bond_holdings_app_insert ON bond_holdings FOR INSERT TO {APP_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY bond_holdings_worker ON bond_holdings TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (true)",
]

DEPOSITS = [
    "ALTER TABLE payment_deposits DROP CONSTRAINT ck_payment_deposits_purpose_type",
    "ALTER TABLE payment_deposits ADD CONSTRAINT ck_payment_deposits_purpose_type "
    "CHECK (purpose_type IN ('investment', 'goal', 'bond'))",
    # A bond holding is paid for once, whichever way, as an investment is.
    "CREATE UNIQUE INDEX uq_payment_deposits_live_bond ON payment_deposits (purpose_id) "
    "WHERE purpose_type = 'bond' AND status <> 'failed' AND reversal_id IS NULL "
    "AND provider_refund_id IS NULL",
]


def upgrade() -> None:
    for statement in [TABLE, *INDEXES, GUARD, *RULES, *DEPOSITS]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM bond_holdings)
       OR EXISTS (SELECT FROM payment_deposits WHERE purpose_type = 'bond') THEN
        RAISE EXCEPTION 'refusing to drop clients'' bond holdings';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    for statement in [
        "DROP INDEX uq_payment_deposits_live_bond",
        "ALTER TABLE payment_deposits DROP CONSTRAINT ck_payment_deposits_purpose_type",
        "ALTER TABLE payment_deposits ADD CONSTRAINT ck_payment_deposits_purpose_type "
        "CHECK (purpose_type IN ('investment', 'goal'))",
        "DROP TABLE bond_holdings",
        "DROP FUNCTION finvest_bond_holdings_guard()",
    ]:
        op.execute(statement)
