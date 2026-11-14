"""Ask for the interest run or the maturity run again, by two people.

Revision ID: 0330_investment_run_requests
Revises: 0270_payout_wallet_short

`investment_run_requests` keeps each request staff make to run a day's interest again, or to
run today's maturities again. One member of staff proposes with a reason, a different one
approves or declines it, and only the proposer withdraws it. Approving queues the run itself in
the same transaction. Both runs only ever post what the formula says is still owed, so running
one again never pays a day twice. The database refuses a decision by the proposer, a second
open request for the same run and day, and any change after a decision. See docs/investing.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0330_investment_run_requests"
down_revision: str | None = "0270_payout_wallet_short"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
STAFF = "public.finvest_actor_kind() = 'staff'"

TABLE = """
CREATE TABLE investment_run_requests (
    id push_id NOT NULL,
    -- `interest` runs a day's interest again. `maturity` closes the terms due by that day.
    kind varchar(8) NOT NULL,
    -- The Kampala business day the run is for.
    run_date date NOT NULL,
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    -- Staff IDs in the identity module.
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_note varchar(500),
    -- The job the approval queued, in the jobs table.
    job_id push_id,
    CONSTRAINT pk_investment_run_requests PRIMARY KEY (id),
    CONSTRAINT ck_investment_run_requests_kind CHECK (kind IN ('interest', 'maturity')),
    CONSTRAINT ck_investment_run_requests_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_investment_run_requests_status CHECK (
        status IN ('proposed', 'approved', 'declined', 'withdrawn')),
    CONSTRAINT ck_investment_run_requests_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (status = 'proposed') = (decided_at IS NULL)),
    -- Nobody decides their own request, and only the proposer withdraws it.
    CONSTRAINT ck_investment_run_requests_second_person CHECK (
        status IN ('proposed', 'withdrawn') OR decided_by <> proposed_by),
    CONSTRAINT ck_investment_run_requests_withdrawn CHECK (
        status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_investment_run_requests_note CHECK (
        status <> 'declined' OR btrim(coalesce(decision_note, '')) <> ''),
    -- Approved means the run was queued.
    CONSTRAINT ck_investment_run_requests_job CHECK ((status = 'approved') = (job_id IS NOT NULL))
)
"""
INDEXES = [
    # One open request for each run and day.
    "CREATE UNIQUE INDEX uq_investment_run_requests_open ON investment_run_requests "
    "(kind, run_date) WHERE status = 'proposed'",
    "CREATE INDEX ix_investment_run_requests_proposed ON investment_run_requests (proposed_at)",
]

# Staff propose and decide, as the person signed in, and a run is never asked for a day that
# has not come by the database's own clock. Nothing changes after a decision, and nothing is
# deleted or truncated, by anyone.
GUARD = """
CREATE FUNCTION public.finvest_investment_run_requests_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        today date := (clock_timestamp() AT TIME ZONE 'Africa/Kampala')::date;
    BEGIN
        IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'a run request is never deleted' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff ask for a run again' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> public.finvest_actor_id()
                OR NEW.job_id IS NOT NULL OR NEW.run_date > today THEN
                RAISE EXCEPTION 'a run request starts as proposed, by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided run request never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status NOT IN ('approved', 'declined', 'withdrawn')
            OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
            OR (to_jsonb(NEW) - '{status,decided_by,decided_at,decision_note,job_id}'::text[])
                <> (to_jsonb(OLD) - '{status,decided_by,decided_at,decision_note,job_id}'::text[])
        THEN
            RAISE EXCEPTION 'a run request is only decided, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION public.finvest_investment_run_requests_guard() FROM PUBLIC",
    "CREATE TRIGGER investment_run_requests_guard BEFORE INSERT OR UPDATE OR DELETE "
    "ON investment_run_requests FOR EACH ROW "
    "EXECUTE FUNCTION public.finvest_investment_run_requests_guard()",
    "CREATE TRIGGER investment_run_requests_no_truncate BEFORE TRUNCATE "
    "ON investment_run_requests FOR EACH STATEMENT "
    "EXECUTE FUNCTION public.finvest_investment_run_requests_guard()",
    "REVOKE ALL ON investment_run_requests FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_note, job_id) "
    f"ON investment_run_requests TO {APP_ROLE}",
    f"GRANT SELECT ON investment_run_requests TO {WORKER_ROLE}",
    # Staff only on the API's role, so a client transaction reads no request.
    "ALTER TABLE investment_run_requests ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY investment_run_requests_app ON investment_run_requests "
    f"TO {APP_ROLE} USING ({STAFF}) WITH CHECK ({STAFF})",
    f"CREATE POLICY investment_run_requests_worker_read ON investment_run_requests FOR SELECT "
    f"TO {WORKER_ROLE} USING (true)",
]


def upgrade() -> None:
    for statement in [TABLE, *INDEXES, GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM investment_run_requests) THEN
        RAISE EXCEPTION 'refusing to drop the record of runs staff asked for again';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Who asked for a run that moved money is part of the books' story.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE investment_run_requests")
    op.execute("DROP FUNCTION public.finvest_investment_run_requests_guard()")
