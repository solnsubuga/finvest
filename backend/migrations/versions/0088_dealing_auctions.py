"""Add the Treasury bill auction calendar, so the desk shows each product's next auction.

Revision ID: 0088_dealing_auctions
Revises: 0401_goal_interest_savings

The Bank of Uganda publishes the dates of its Treasury bill auctions. A member of staff with
`dealing_orders.change` enters each date for a product and can cancel one, with a reason, if the
Bank moves it. An auction date moves no money and no order depends on it, so one person enters
it (decided for now, see docs/decisions.md), and every entry and cancellation is audited.

* A date is for one product and is never on a day already past when it is entered.
* One date per product and day, while it stands. A cancelled date stays, with who cancelled it,
  when and why, and the day can be entered again.
* Nothing is ever deleted, and only the cancellation columns ever change, once.

See docs/dealing-desk.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0088_dealing_auctions"
down_revision: str | None = "0401_goal_interest_savings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
T = "dealing_auctions"

TABLE = """
CREATE TABLE dealing_auctions (
    id push_id NOT NULL,
    product_code varchar(32) COLLATE "C" NOT NULL,
    auction_date date NOT NULL,
    added_by push_id NOT NULL,
    added_at timestamptz NOT NULL,
    cancelled_by push_id,
    cancelled_at timestamptz,
    cancel_reason varchar(500),
    CONSTRAINT pk_dealing_auctions PRIMARY KEY (id),
    CONSTRAINT ck_dealing_auctions_product CHECK (btrim(product_code) <> ''),
    CONSTRAINT ck_dealing_auctions_cancelled CHECK (
        (cancelled_by IS NULL) = (cancelled_at IS NULL)
        AND (cancelled_by IS NULL) = (cancel_reason IS NULL)
        AND (cancel_reason IS NULL OR btrim(cancel_reason) <> ''))
);
CREATE UNIQUE INDEX uq_dealing_auctions_standing
    ON dealing_auctions (product_code, auction_date) WHERE cancelled_by IS NULL;
CREATE INDEX ix_dealing_auctions_date ON dealing_auctions (auction_date, product_code)
"""

GUARD = """
CREATE FUNCTION finvest_dealing_auctions_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'an auction date is never removed, only cancelled'
                USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'the auction calendar is kept by staff' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.added_by IS DISTINCT FROM public.finvest_actor_id()
                OR NEW.cancelled_by IS NOT NULL THEN
                RAISE EXCEPTION 'an auction date is entered by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.cancelled_by IS NOT NULL OR NEW.cancelled_by IS NULL
            OR NEW.cancelled_by IS DISTINCT FROM public.finvest_actor_id() THEN
            RAISE EXCEPTION 'a standing auction date is cancelled once, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.product_code, NEW.auction_date, NEW.added_by, NEW.added_at)
           IS DISTINCT FROM
           (OLD.id, OLD.product_code, OLD.auction_date, OLD.added_by, OLD.added_at) THEN
            RAISE EXCEPTION 'an auction date does not change' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

STAFF = "public.finvest_actor_kind() = 'staff'"

RULES = [
    "REVOKE ALL ON FUNCTION finvest_dealing_auctions_guard() FROM PUBLIC",
    f"CREATE TRIGGER {T}_guard BEFORE INSERT OR UPDATE OR DELETE ON {T} "
    "FOR EACH ROW EXECUTE FUNCTION finvest_dealing_auctions_guard()",
    f"CREATE TRIGGER {T}_no_truncate BEFORE TRUNCATE ON {T} "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_dealing_auctions_guard()",
    f"REVOKE ALL ON {T} FROM PUBLIC",
    f"GRANT SELECT, INSERT ON {T} TO {APP_ROLE}",
    f"GRANT UPDATE (cancelled_by, cancelled_at, cancel_reason) ON {T} TO {APP_ROLE}",
    f"GRANT SELECT ON {T} TO {WORKER_ROLE}",
    f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY {T}_app_read ON {T} FOR SELECT TO {APP_ROLE} USING ({STAFF})",
    f"CREATE POLICY {T}_worker_read ON {T} FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY {T}_app_insert ON {T} FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF})",
    f"CREATE POLICY {T}_app_update ON {T} FOR UPDATE TO {APP_ROLE} "
    f"USING ({STAFF}) WITH CHECK ({STAFF})",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLE), GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM dealing_auctions) THEN
        RAISE EXCEPTION 'refusing to drop the auction calendar';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(f"DROP TABLE {T}")
    op.execute("DROP FUNCTION finvest_dealing_auctions_guard()")
