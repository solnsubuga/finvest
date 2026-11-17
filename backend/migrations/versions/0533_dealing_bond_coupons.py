"""Record the coupons Bank of Uganda pays in for the bonds the dealing desk holds.

Revision ID: 0533_dealing_bond_coupons
Revises: 0532_dealing_bonds

* One member of staff records a coupon from the custodian's note: the issue, the coupon date,
  the face value the desk held for it, the coupon that face value earns, what reached the trust
  account, its bank reference and the day it arrived. A different signed-in member of staff
  approves or declines it, once. What was recorded never changes.
* An issue's coupon on a date is recorded once, and a bank reference once, unless declined.
* An approved coupon names exactly the ledger entry it posted: from this coupon, on the day it
  arrived, with the trust account up and `dealing.bond_coupons`, Finvest's income, up by what
  arrived. Nothing is ever deleted. The approval posts in the second person's staff
  transaction, so `dealing.bond_coupon` joins the source types staff may post.

See docs/dealing-desk.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0533_dealing_bond_coupons"
down_revision: str | None = "0532_dealing_bonds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
T = "dealing_bond_coupons"

TABLE = """
CREATE TABLE dealing_bond_coupons (
    id push_id NOT NULL,
    -- The issue's code in the bonds catalogue. No foreign key: a module owns only its tables.
    issue_code varchar(24) COLLATE "C" NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    coupon_date date NOT NULL,
    -- The face value the desk held the day before the coupon date, and the coupon it earns
    -- before any tax, as Finvest works it out.
    face_minor bigint NOT NULL,
    expected_minor bigint NOT NULL,
    -- What reached the trust account, as the bank statement shows it.
    amount_minor bigint NOT NULL,
    value_date date NOT NULL,
    bank_reference varchar(100) NOT NULL,
    -- Why what arrived differs from what was expected. Required when it does.
    note varchar(500),
    status varchar(8) NOT NULL,
    recorded_by push_id NOT NULL,
    recorded_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    ledger_entry_id push_id,
    CONSTRAINT pk_dealing_bond_coupons PRIMARY KEY (id),
    CONSTRAINT ck_dealing_bond_coupons_code CHECK (issue_code ~ '^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$'),
    CONSTRAINT ck_dealing_bond_coupons_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_dealing_bond_coupons_amounts CHECK (
        face_minor > 0 AND expected_minor > 0 AND amount_minor > 0),
    CONSTRAINT ck_dealing_bond_coupons_value_date CHECK (value_date >= coupon_date),
    CONSTRAINT ck_dealing_bond_coupons_reference CHECK (
        btrim(bank_reference) = bank_reference AND bank_reference <> ''),
    CONSTRAINT ck_dealing_bond_coupons_note CHECK (
        (amount_minor = expected_minor AND note IS NULL)
        OR (amount_minor <> expected_minor AND btrim(coalesce(note, '')) <> '')),
    CONSTRAINT ck_dealing_bond_coupons_status
        CHECK (status IN ('recorded', 'approved', 'declined')),
    CONSTRAINT ck_dealing_bond_coupons_decided CHECK (
        (status = 'recorded') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (status = 'approved') = (ledger_entry_id IS NOT NULL)),
    CONSTRAINT ck_dealing_bond_coupons_two_people
        CHECK (decided_by IS NULL OR decided_by <> recorded_by),
    CONSTRAINT ck_dealing_bond_coupons_decline_reason
        CHECK (status <> 'declined' OR btrim(coalesce(decision_reason, '')) <> '')
);
CREATE UNIQUE INDEX uq_dealing_bond_coupons_coupon
    ON dealing_bond_coupons (issue_code, coupon_date) WHERE status <> 'declined';
CREATE UNIQUE INDEX uq_dealing_bond_coupons_bank_reference
    ON dealing_bond_coupons (currency, bank_reference) WHERE status <> 'declined';
CREATE UNIQUE INDEX uq_dealing_bond_coupons_entry
    ON dealing_bond_coupons (ledger_entry_id) WHERE ledger_entry_id IS NOT NULL;
CREATE INDEX ix_dealing_bond_coupons_recorded
    ON dealing_bond_coupons (recorded_at, id) WHERE status = 'recorded'
"""

GUARD = """
CREATE FUNCTION finvest_dealing_bond_coupons_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        bank bigint;
        income bigint;
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'a bond coupon received is never removed' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'bond coupons are recorded by staff' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status IS DISTINCT FROM 'recorded'
                OR NEW.recorded_by IS DISTINCT FROM public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a coupon starts recorded by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status IS DISTINCT FROM 'recorded'
            OR NEW.status NOT IN ('approved', 'declined') THEN
            RAISE EXCEPTION 'a recorded coupon is approved or declined, once'
                USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.issue_code, NEW.currency, NEW.coupon_date, NEW.face_minor,
            NEW.expected_minor, NEW.amount_minor, NEW.value_date, NEW.bank_reference, NEW.note,
            NEW.recorded_by, NEW.recorded_at)
           IS DISTINCT FROM
           (OLD.id, OLD.issue_code, OLD.currency, OLD.coupon_date, OLD.face_minor,
            OLD.expected_minor, OLD.amount_minor, OLD.value_date, OLD.bank_reference, OLD.note,
            OLD.recorded_by, OLD.recorded_at) THEN
            RAISE EXCEPTION 'what was recorded does not change' USING ERRCODE = '42501';
        END IF;
        IF NEW.decided_by IS DISTINCT FROM public.finvest_actor_id() THEN
            RAISE EXCEPTION 'a coupon is decided by the person signed in' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'declined' THEN
            RETURN NEW;
        END IF;
        IF NOT EXISTS (
            SELECT FROM public.ledger_journal_entries
            WHERE id = NEW.ledger_entry_id AND source_type = 'dealing.bond_coupon'
                AND source_id = NEW.id AND currency = NEW.currency
                AND effective_date = NEW.value_date AND reverses_entry_id IS NULL
        ) THEN
            RAISE EXCEPTION 'an approved coupon names the entry it posted' USING ERRCODE = '42501';
        END IF;
        SELECT
            coalesce(sum(line.amount_minor) FILTER (WHERE account.code = 'bank.trust'), 0),
            coalesce(sum(line.amount_minor) FILTER (WHERE account.code = 'dealing.bond_coupons'), 0)
            INTO bank, income
            FROM public.ledger_lines AS line
            JOIN public.ledger_accounts AS account ON account.id = line.account_id
            WHERE line.entry_id = NEW.ledger_entry_id
                AND account.owner_client_id IS NULL AND account.reference IS NULL;
        IF bank IS DISTINCT FROM NEW.amount_minor OR income IS DISTINCT FROM -NEW.amount_minor THEN
            RAISE EXCEPTION 'the entry puts what arrived in the trust account, as coupon income'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

STAFF = "public.finvest_actor_kind() = 'staff'"

RULES = [
    "REVOKE ALL ON FUNCTION finvest_dealing_bond_coupons_guard() FROM PUBLIC",
    f"CREATE TRIGGER {T}_guard BEFORE INSERT OR UPDATE OR DELETE ON {T} "
    "FOR EACH ROW EXECUTE FUNCTION finvest_dealing_bond_coupons_guard()",
    f"CREATE TRIGGER {T}_no_truncate BEFORE TRUNCATE ON {T} "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_dealing_bond_coupons_guard()",
    f"REVOKE ALL ON {T} FROM PUBLIC",
    f"GRANT SELECT, INSERT ON {T} TO {APP_ROLE}",
    f"GRANT UPDATE (status, decided_by, decided_at, decision_reason, ledger_entry_id) "
    f"ON {T} TO {APP_ROLE}",
    # The day close expects the coupons on the bank statement.
    f"GRANT SELECT ON {T} TO {WORKER_ROLE}",
    f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY {T}_app_read ON {T} FOR SELECT TO {APP_ROLE} USING ({STAFF})",
    f"CREATE POLICY {T}_worker_read ON {T} FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY {T}_app_insert ON {T} FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF})",
    f"CREATE POLICY {T}_app_update ON {T} FOR UPDATE TO {APP_ROLE} "
    f"USING ({STAFF}) WITH CHECK ({STAFF})",
    "INSERT INTO ledger_staff_source_types (source_type, purpose) VALUES "
    "('dealing.bond_coupon', 'dealing: a coupon Bank of Uganda paid on bonds the desk holds, "
    "approved by a second staff member')",
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
    IF EXISTS (SELECT FROM dealing_bond_coupons) THEN
        RAISE EXCEPTION 'refusing to drop bond coupons received';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DELETE FROM ledger_staff_source_types WHERE source_type = 'dealing.bond_coupon'")
    op.execute(f"DROP TABLE {T}")
    op.execute("DROP FUNCTION finvest_dealing_bond_coupons_guard()")
