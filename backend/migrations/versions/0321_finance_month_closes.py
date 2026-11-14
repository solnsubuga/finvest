"""Finance signs off each month: one person proposes the close, a different person approves.

Revision ID: 0321_finance_month_closes
Revises: 0320_report_schedules

* A row is one proposal to close a month, with the books for every currency as they stood when
  it was proposed, and their digest. A different person approves it, which closes the month,
  or declines it; the proposer may withdraw it while it waits.
* A month is closed once: at most one approved row per month, and one waiting at a time.
* The books kept with a close never change. Nothing is ever removed.

See docs/finance.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0321_finance_month_closes"
down_revision: str | None = "0320_report_schedules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
T = "finance_month_closes"

TABLE = """
CREATE TABLE finance_month_closes (
    id push_id NOT NULL,
    month date NOT NULL,
    books jsonb NOT NULL,
    books_sha256 char(64) COLLATE "C" NOT NULL,
    reason text NOT NULL,
    status varchar(16) NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason text,
    CONSTRAINT pk_finance_month_closes PRIMARY KEY (id),
    CONSTRAINT ck_finance_month_closes_month CHECK (extract(day FROM month) = 1),
    CONSTRAINT ck_finance_month_closes_books CHECK (jsonb_typeof(books) = 'array'),
    CONSTRAINT ck_finance_month_closes_sha CHECK (books_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_finance_month_closes_reason CHECK (char_length(btrim(reason)) BETWEEN 1 AND 500),
    CONSTRAINT ck_finance_month_closes_status
        CHECK (status IN ('proposed', 'approved', 'declined', 'withdrawn')),
    CONSTRAINT ck_finance_month_closes_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL) AND (decided_by IS NULL) = (decided_at IS NULL)),
    -- Closing or declining a month is a second person's decision.
    CONSTRAINT ck_finance_month_closes_second_person CHECK (
        status IN ('proposed', 'withdrawn') OR decided_by <> proposed_by),
    CONSTRAINT ck_finance_month_closes_withdrawn CHECK (
        status <> 'withdrawn' OR decided_by = proposed_by)
);
CREATE UNIQUE INDEX uq_finance_month_closes_closed
    ON finance_month_closes (month) WHERE status = 'approved';
CREATE UNIQUE INDEX uq_finance_month_closes_waiting
    ON finance_month_closes ((true)) WHERE status = 'proposed';
CREATE INDEX ix_finance_month_closes_month ON finance_month_closes (month DESC, proposed_at DESC)
"""

GUARD = """
CREATE FUNCTION finvest_finance_month_closes_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP = 'DELETE' OR TG_OP = 'TRUNCATE' THEN
            RAISE EXCEPTION 'a month close is never removed' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'months are closed by staff' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed'
                OR NEW.proposed_by IS DISTINCT FROM public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a month close is proposed by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF (NEW.month, NEW.books, NEW.books_sha256, NEW.reason, NEW.proposed_by,
                NEW.proposed_at)
            IS DISTINCT FROM (OLD.month, OLD.books, OLD.books_sha256, OLD.reason,
                OLD.proposed_by, OLD.proposed_at) THEN
            RAISE EXCEPTION 'the books of a month close never change' USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'proposed' AND NEW.status IN ('approved', 'declined', 'withdrawn') THEN
            IF NEW.decided_by IS DISTINCT FROM public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a month close is decided by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'a month close cannot move from % to %', OLD.status, NEW.status
            USING ERRCODE = '42501';
    END;
    $$
"""

STAFF = "public.finvest_actor_kind() = 'staff'"

RULES = [
    "REVOKE ALL ON FUNCTION finvest_finance_month_closes_guard() FROM PUBLIC",
    f"CREATE TRIGGER {T}_guard BEFORE INSERT OR UPDATE OR DELETE ON {T} "
    "FOR EACH ROW EXECUTE FUNCTION finvest_finance_month_closes_guard()",
    f"CREATE TRIGGER {T}_no_truncate BEFORE TRUNCATE ON {T} "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_finance_month_closes_guard()",
    f"REVOKE ALL ON {T} FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE ON {T} TO {APP_ROLE}",
    f"GRANT SELECT ON {T} TO {WORKER_ROLE}",
    f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY {T}_app ON {T} FOR ALL TO {APP_ROLE} USING ({STAFF}) WITH CHECK ({STAFF})",
    f"CREATE POLICY {T}_worker ON {T} FOR SELECT TO {WORKER_ROLE} USING (true)",
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
    IF EXISTS (SELECT FROM finance_month_closes) THEN
        RAISE EXCEPTION 'refusing to drop month closes';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(f"DROP TABLE {T}")
    op.execute("DROP FUNCTION finvest_finance_month_closes_guard()")
