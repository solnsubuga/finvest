"""Add the Treasury bill auction calendar, kept by two people, so the desk shows each product's
next auction.

Revision ID: 0088_dealing_auctions
Revises: 0401_goal_interest_savings

The Bank of Uganda publishes the dates of its Treasury bill auctions. Each row of
`dealing_auctions` is one proposed change to the calendar, by a member of staff with
`dealing_orders.change`:

* `add` puts a date forward for a product. It counts once a different person approves it.
* `cancel` puts forward cancelling an approved date, with a reason, if the Bank moves it. The
  date stops counting once a different person approves the cancellation.

Only the proposer may withdraw a change while it waits, and only someone else may approve or
decline it, as for the platform settings (migration 0340). PostgreSQL enforces all of it:

* One add waiting per product and day, and no second approved date for the same product and
  day while one stands.
* A cancellation names an approved date of the same product and day, and one waits or stands
  per date.
* A decided change never changes again, and nothing is ever deleted.

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
    action varchar(6) NOT NULL,
    product_code varchar(32) COLLATE "C" NOT NULL,
    auction_date date NOT NULL,
    cancels push_id,
    reason varchar(500),
    status varchar(10) NOT NULL DEFAULT 'proposed',
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_note varchar(500),
    CONSTRAINT pk_dealing_auctions PRIMARY KEY (id),
    CONSTRAINT fk_dealing_auctions_cancels FOREIGN KEY (cancels) REFERENCES dealing_auctions (id),
    CONSTRAINT ck_dealing_auctions_action CHECK (action IN ('add', 'cancel')),
    CONSTRAINT ck_dealing_auctions_product CHECK (btrim(product_code) <> ''),
    -- A cancellation names the date it cancels and says why. An add names nothing.
    CONSTRAINT ck_dealing_auctions_cancels CHECK (
        (action = 'add') = (cancels IS NULL)
        AND (action = 'add' OR btrim(coalesce(reason, '')) <> '')),
    CONSTRAINT ck_dealing_auctions_status
        CHECK (status IN ('proposed', 'approved', 'declined', 'withdrawn')),
    CONSTRAINT ck_dealing_auctions_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (status <> 'declined' OR btrim(coalesce(decision_note, '')) <> '')),
    -- Two people. Only the proposer can withdraw, and never approve or decline, their own.
    CONSTRAINT ck_dealing_auctions_two_people CHECK (
        status = 'proposed'
        OR (status = 'withdrawn' AND decided_by = proposed_by)
        OR (status IN ('approved', 'declined') AND decided_by <> proposed_by))
);
CREATE UNIQUE INDEX uq_dealing_auctions_add_waiting ON dealing_auctions (product_code, auction_date)
    WHERE action = 'add' AND status = 'proposed';
CREATE UNIQUE INDEX uq_dealing_auctions_cancel_live ON dealing_auctions (cancels)
    WHERE action = 'cancel' AND status IN ('proposed', 'approved');
CREATE INDEX ix_dealing_auctions_date ON dealing_auctions (auction_date, product_code)
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001). The guard
# names the acting member of staff on every row, so the two-person check compares the people
# whose transactions wrote the proposal and the decision.
GUARD = """
CREATE FUNCTION finvest_dealing_auctions_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        target dealing_auctions%ROWTYPE;
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'an auction date is never removed, only cancelled'
                USING ERRCODE = '42501';
        END IF;
        IF finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'the auction calendar is kept by staff' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.decided_by IS NOT NULL
                OR NEW.proposed_by IS DISTINCT FROM finvest_actor_id() THEN
                RAISE EXCEPTION 'a change starts as a proposal by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.action = 'cancel' THEN
                SELECT * INTO target FROM dealing_auctions WHERE id = NEW.cancels;
                IF target.action IS DISTINCT FROM 'add' OR target.status <> 'approved'
                    OR target.product_code <> NEW.product_code
                    OR target.auction_date <> NEW.auction_date THEN
                    RAISE EXCEPTION 'only an approved date is cancelled' USING ERRCODE = '42501';
                END IF;
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided change never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM finvest_actor_id()
            OR (to_jsonb(NEW) - ARRAY['status', 'decided_by', 'decided_at', 'decision_note'])
               <> (to_jsonb(OLD) - ARRAY['status', 'decided_by', 'decided_at', 'decision_note'])
        THEN
            RAISE EXCEPTION 'a change is decided once, by the acting member of staff'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'approved' AND NEW.action = 'add' AND EXISTS (
            SELECT FROM dealing_auctions a
            WHERE a.action = 'add' AND a.status = 'approved' AND a.id <> NEW.id
              AND a.product_code = NEW.product_code AND a.auction_date = NEW.auction_date
              AND NOT EXISTS (
                  SELECT FROM dealing_auctions c
                  WHERE c.action = 'cancel' AND c.cancels = a.id AND c.status = 'approved'))
        THEN
            RAISE EXCEPTION 'that auction already stands in the calendar' USING ERRCODE = '23505';
        END IF;
        RETURN NEW;
    END;
    $$
"""

STAFF = "finvest_actor_kind() = 'staff'"

RULES = [
    "REVOKE ALL ON FUNCTION finvest_dealing_auctions_guard() FROM PUBLIC",
    f"CREATE TRIGGER {T}_guard BEFORE INSERT OR UPDATE OR DELETE ON {T} "
    "FOR EACH ROW EXECUTE FUNCTION finvest_dealing_auctions_guard()",
    f"CREATE TRIGGER {T}_no_truncate BEFORE TRUNCATE ON {T} "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_dealing_auctions_guard()",
    f"REVOKE ALL ON {T} FROM PUBLIC",
    # No DELETE for anyone: who put a date forward, and who agreed, is part of the record.
    f"GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_note) ON {T} "
    f"TO {APP_ROLE}",
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
