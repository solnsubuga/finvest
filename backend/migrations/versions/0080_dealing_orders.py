"""Add dealing desk orders.

Revision ID: 0080_dealing_orders
Revises: 0021_one_live_deposit

An order to buy or sell bills is prepared by one member of staff and approved by another. What
the dealer did is then recorded from the custodian's note. The database enforces who may take
each step, whatever the application does. Settling an order, which moves money and creates the
bills held, arrives with the holdings tables: until then no order can be marked settled.
See docs/dealing-desk.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0080_dealing_orders"
down_revision: str | None = "0021_one_live_deposit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE dealing_orders (
    id push_id NOT NULL,
    kind varchar(20) NOT NULL,
    -- The product code in the investments module. No foreign key: a module owns only its tables.
    product_code varchar(24) COLLATE "C" NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    face_minor bigint NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'prepared',
    -- Staff IDs in the identity module.
    prepared_by push_id NOT NULL,
    prepared_at timestamptz NOT NULL,
    -- Who approved it, sent it back or withdrew it.
    decided_by push_id,
    decided_at timestamptz,
    decision_note varchar(500),
    -- What the custodian's note says was dealt. All empty when nothing was dealt.
    result_face_minor bigint,
    result_amount_minor bigint,
    result_settled_on date,
    result_matures_on date,
    custodian_reference varchar(64),
    result_recorded_by push_id,
    result_recorded_at timestamptz,
    -- Why the last recorded result was rejected, until a new one is recorded.
    result_rejection_note varchar(500),
    CONSTRAINT pk_dealing_orders PRIMARY KEY (id),
    CONSTRAINT ck_dealing_orders_kind
        CHECK (kind IN ('auction_bid', 'secondary_purchase', 'secondary_sale')),
    CONSTRAINT ck_dealing_orders_product CHECK (product_code ~ '^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$'),
    CONSTRAINT ck_dealing_orders_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_dealing_orders_face CHECK (face_minor > 0),
    CONSTRAINT ck_dealing_orders_status CHECK (status IN (
        'prepared', 'approved', 'sent_back', 'withdrawn', 'result_recorded', 'settled',
        'unsuccessful')),
    CONSTRAINT ck_dealing_orders_decided CHECK (
        (status = 'prepared') = (decided_by IS NULL)
        AND (status = 'prepared') = (decided_at IS NULL)),
    -- Nobody approves or sends back their own order, and only the preparer withdraws it.
    CONSTRAINT ck_dealing_orders_second_person CHECK (
        status IN ('prepared', 'withdrawn') OR decided_by <> prepared_by),
    CONSTRAINT ck_dealing_orders_withdrawn CHECK (
        status <> 'withdrawn' OR decided_by = prepared_by),
    CONSTRAINT ck_dealing_orders_note CHECK (
        status <> 'sent_back' OR btrim(coalesce(decision_note, '')) <> ''),
    -- A result is whole or absent, and only an order that reached that step carries one.
    CONSTRAINT ck_dealing_orders_result_whole CHECK (
        num_nonnulls(result_face_minor, result_amount_minor, result_settled_on,
            result_matures_on, custodian_reference) IN (0, 5)),
    CONSTRAINT ck_dealing_orders_result_status CHECK (
        (result_face_minor IS NOT NULL) = (status IN ('result_recorded', 'settled'))),
    CONSTRAINT ck_dealing_orders_recorded_by CHECK (
        (result_recorded_by IS NOT NULL) = (status IN ('result_recorded', 'settled', 'unsuccessful'))
        AND (result_recorded_by IS NULL) = (result_recorded_at IS NULL)),
    CONSTRAINT ck_dealing_orders_result_amounts CHECK (
        result_face_minor BETWEEN 1 AND face_minor AND result_amount_minor > 0),
    CONSTRAINT ck_dealing_orders_result_dates CHECK (result_matures_on > result_settled_on),
    CONSTRAINT ck_dealing_orders_reference CHECK (
        btrim(custodian_reference) = custodian_reference AND custodian_reference <> ''),
    CONSTRAINT ck_dealing_orders_rejection_note CHECK (
        result_rejection_note IS NULL
        OR (status = 'approved' AND btrim(result_rejection_note) <> ''))
);
CREATE INDEX ix_dealing_orders_open ON dealing_orders (prepared_at, id)
    WHERE status IN ('prepared', 'approved', 'result_recorded')
"""

# Each step is taken by the member of staff the transaction acts for, from the one status that
# step belongs to. What the order is for never changes, and a finished order never changes.
GUARD = """
CREATE FUNCTION finvest_dealing_orders_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        actor push_id := finvest_actor_id();
        decision text[] := '{status,decided_by,decided_at,decision_note}';
        result text[] := '{status,result_face_minor,result_amount_minor,result_settled_on,'
            'result_matures_on,custodian_reference,result_recorded_by,result_recorded_at,'
            'result_rejection_note}';
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'a dealing order is never removed' USING ERRCODE = '42501';
        END IF;
        IF finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff work on dealing orders' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'prepared' OR NEW.prepared_by <> actor
                OR NEW.result_rejection_note IS NOT NULL THEN
                RAISE EXCEPTION 'a dealing order starts as prepared, by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;

        IF OLD.status = 'prepared' AND NEW.status IN ('approved', 'sent_back', 'withdrawn') THEN
            IF NEW.decided_by IS DISTINCT FROM actor
                OR (to_jsonb(NEW) - decision) <> (to_jsonb(OLD) - decision) THEN
                RAISE EXCEPTION 'a dealing order is only decided, by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF OLD.status = 'approved' AND NEW.status IN ('result_recorded', 'unsuccessful') THEN
            IF NEW.result_recorded_by IS DISTINCT FROM actor
                OR NEW.result_rejection_note IS NOT NULL
                OR (to_jsonb(NEW) - result) <> (to_jsonb(OLD) - result) THEN
                RAISE EXCEPTION 'a result is only recorded, by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF OLD.status = 'result_recorded' AND NEW.status = 'approved' THEN
            -- A second person found the result does not match the custodian's note.
            IF actor = OLD.result_recorded_by OR NEW.result_rejection_note IS NULL
                OR (to_jsonb(NEW) - result) <> (to_jsonb(OLD) - result) THEN
                RAISE EXCEPTION 'a result is rejected by a second person, with a note'
                    USING ERRCODE = '42501';
            END IF;
        ELSE
            -- This covers settling, which needs the holdings and the ledger entry.
            RAISE EXCEPTION 'a dealing order cannot go from % to %', OLD.status, NEW.status
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_dealing_orders_guard() FROM PUBLIC",
    "CREATE TRIGGER dealing_orders_guard BEFORE INSERT OR UPDATE OR DELETE ON dealing_orders "
    "FOR EACH ROW EXECUTE FUNCTION finvest_dealing_orders_guard()",
    "CREATE TRIGGER dealing_orders_no_truncate BEFORE TRUNCATE ON dealing_orders "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_dealing_orders_guard()",
    "REVOKE ALL ON dealing_orders FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_note, "
    f"result_face_minor, result_amount_minor, result_settled_on, result_matures_on, "
    f"custodian_reference, result_recorded_by, result_recorded_at, result_rejection_note) "
    f"ON dealing_orders TO {APP_ROLE}",
    "ALTER TABLE dealing_orders ENABLE ROW LEVEL SECURITY",
    # Orders are staff business. Which staff may prepare or approve is a permission, checked in
    # the application. The worker has no part in them yet and is granted nothing.
    f"CREATE POLICY dealing_orders_staff ON dealing_orders TO {APP_ROLE} "
    "USING (finvest_actor_kind() = 'staff') WITH CHECK (finvest_actor_kind() = 'staff')",
]


def upgrade() -> None:
    for statement in [*[part.strip() for part in TABLE.split(";\n")], GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM dealing_orders) THEN
        RAISE EXCEPTION 'refusing to drop dealing orders';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE dealing_orders")
    op.execute("DROP FUNCTION finvest_dealing_orders_guard()")
