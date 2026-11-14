"""Book an investment for a client, by two members of staff. The client pays for it as usual.

Revision ID: 0334_investment_bookings
Revises: 0333_maturity_pay_interest

The owner decided (2026-11-14, "Book it, client pays as usual"): when a client asks Finvest to
set up an investment for them, one member of staff books it with a reason, a different one
approves it or sends it back with a note, and only the one who booked it withdraws it. On
approval the worker saves an unpaid investment for the client on the product's terms of that
day, exactly as if the client had asked in the app, and the client pays for it as they would
any other. Nothing is paid and no money moves here.

`investment_bookings` keeps each booking. The investment the worker saves names its booking in
`source_type` (`investment_booking`, which `ck_investments_source` now allows) and
`source_id`, which the guard of migration 0241 already lets only the worker write, and `uq_investments_source` keeps one investment per booking. A booking the worker
cannot carry out, for example because the product closed or the client already has five unpaid
investments, records why. The database refuses a decision by the one who booked it, any change
after a decision, and anything but the worker finishing an approved booking.
See docs/investing.md, "Booking for a client".
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0334_investment_bookings"
down_revision: str | None = "0333_maturity_pay_interest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
STAFF = "public.finvest_actor_kind() = 'staff'"

TABLE = """
CREATE TABLE investment_bookings (
    id push_id NOT NULL,
    -- The client in the identity module.
    client_id push_id NOT NULL,
    product_id push_id NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    principal_minor bigint NOT NULL,
    at_maturity varchar(12),
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
    -- What the worker did with it.
    investment_id push_id,
    failure_code varchar(64),
    finished_at timestamptz,
    CONSTRAINT pk_investment_bookings PRIMARY KEY (id),
    CONSTRAINT fk_investment_bookings_product FOREIGN KEY (product_id)
        REFERENCES investment_products (id),
    CONSTRAINT fk_investment_bookings_investment FOREIGN KEY (investment_id)
        REFERENCES investments (id),
    CONSTRAINT ck_investment_bookings_amount CHECK (principal_minor > 0),
    CONSTRAINT ck_investment_bookings_at_maturity CHECK (
        at_maturity IS NULL OR at_maturity IN ('roll_over', 'pay_out', 'pay_interest')),
    CONSTRAINT ck_investment_bookings_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_investment_bookings_status CHECK (
        status IN ('proposed', 'approved', 'sent_back', 'withdrawn', 'done', 'failed')),
    CONSTRAINT ck_investment_bookings_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (status = 'proposed') = (decided_at IS NULL)),
    -- Nobody decides their own booking, and only the one who booked it withdraws it.
    CONSTRAINT ck_investment_bookings_second_person CHECK (
        status IN ('proposed', 'withdrawn') OR decided_by <> proposed_by),
    CONSTRAINT ck_investment_bookings_withdrawn CHECK (
        status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_investment_bookings_note CHECK (
        status <> 'sent_back' OR btrim(coalesce(decision_note, '')) <> ''),
    -- Approved means the worker was asked; done names the investment; failed says why.
    CONSTRAINT ck_investment_bookings_job CHECK (
        (status IN ('approved', 'done', 'failed')) = (job_id IS NOT NULL)),
    CONSTRAINT ck_investment_bookings_finished CHECK (
        (status IN ('done', 'failed')) = (finished_at IS NOT NULL)
        AND (status = 'done') = (investment_id IS NOT NULL)
        AND (status = 'failed') = (failure_code IS NOT NULL))
)
"""
INDEXES = [
    "CREATE INDEX ix_investment_bookings_proposed ON investment_bookings (proposed_at)",
    "CREATE INDEX ix_investment_bookings_client ON investment_bookings (client_id)",
    "CREATE UNIQUE INDEX uq_investment_bookings_investment ON investment_bookings "
    "(investment_id) WHERE investment_id IS NOT NULL",
]

# Staff book and decide, as the person signed in. Only the worker finishes an approved booking,
# with the investment it saved for that client and booking or with why it could not. Nothing
# else changes after a decision, and nothing is deleted or truncated, by anyone.
GUARD = """
CREATE FUNCTION public.finvest_investment_bookings_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        is_worker boolean := public.finvest_actor_kind() = 'system'
            AND current_user = 'finvest_worker';
        saved public.investments%ROWTYPE;
    BEGIN
        IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'a booking is never deleted' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' OR NEW.status <> 'proposed'
                OR NEW.proposed_by <> public.finvest_actor_id() OR NEW.job_id IS NOT NULL
                OR NEW.investment_id IS NOT NULL OR NEW.failure_code IS NOT NULL
                OR NEW.finished_at IS NOT NULL THEN
                RAISE EXCEPTION 'a booking starts as proposed, by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF (to_jsonb(NEW) - '{status,decided_by,decided_at,decision_note,job_id,investment_id,failure_code,finished_at}'::text[])
            <> (to_jsonb(OLD) - '{status,decided_by,decided_at,decision_note,job_id,investment_id,failure_code,finished_at}'::text[])
        THEN
            RAISE EXCEPTION 'what was booked never changes' USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'approved' THEN
            SELECT * INTO saved FROM public.investments WHERE id = NEW.investment_id;
            IF NOT is_worker OR NEW.status NOT IN ('done', 'failed')
                OR NEW.decided_by IS DISTINCT FROM OLD.decided_by
                OR NEW.decided_at IS DISTINCT FROM OLD.decided_at
                OR NEW.decision_note IS DISTINCT FROM OLD.decision_note
                OR NEW.job_id IS DISTINCT FROM OLD.job_id
                OR (NEW.status = 'done' AND (saved.id IS NULL
                    OR saved.client_id <> OLD.client_id
                    OR saved.source_type IS DISTINCT FROM 'investment_booking'
                    OR saved.source_id IS DISTINCT FROM OLD.id
                    OR saved.product_id <> OLD.product_id
                    OR saved.currency <> OLD.currency
                    OR saved.principal_minor <> OLD.principal_minor)) THEN
                RAISE EXCEPTION 'only the worker finishes a booking, with what was booked'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided booking never changes' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff'
            OR NEW.status NOT IN ('approved', 'sent_back', 'withdrawn')
            OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
            OR NEW.investment_id IS NOT NULL OR NEW.failure_code IS NOT NULL
            OR NEW.finished_at IS NOT NULL THEN
            RAISE EXCEPTION 'a booking is only decided, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION public.finvest_investment_bookings_guard() FROM PUBLIC",
    "CREATE TRIGGER investment_bookings_guard BEFORE INSERT OR UPDATE OR DELETE "
    "ON investment_bookings FOR EACH ROW "
    "EXECUTE FUNCTION public.finvest_investment_bookings_guard()",
    "CREATE TRIGGER investment_bookings_no_truncate BEFORE TRUNCATE "
    "ON investment_bookings FOR EACH STATEMENT "
    "EXECUTE FUNCTION public.finvest_investment_bookings_guard()",
    "REVOKE ALL ON investment_bookings FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_note, job_id) "
    f"ON investment_bookings TO {APP_ROLE}",
    f"GRANT SELECT, UPDATE (status, investment_id, failure_code, finished_at) "
    f"ON investment_bookings TO {WORKER_ROLE}",
    # Staff only on the API's role, so a client transaction reads no booking.
    "ALTER TABLE investment_bookings ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY investment_bookings_app ON investment_bookings "
    f"TO {APP_ROLE} USING ({STAFF}) WITH CHECK ({STAFF})",
    f"CREATE POLICY investment_bookings_worker ON investment_bookings "
    f"TO {WORKER_ROLE} USING (true) WITH CHECK (true)",
]


_SOURCES = "'autoinvest_run', 'goal_saving', 'transfer_received', 'transfer_kept'"


def _sources(kinds: str) -> list[str]:
    return [
        "ALTER TABLE investments DROP CONSTRAINT ck_investments_source",
        "ALTER TABLE investments ADD CONSTRAINT ck_investments_source CHECK ("
        "(source_type IS NULL AND source_id IS NULL) OR "
        f"(source_type IN ({kinds}) AND source_id IS NOT NULL))",
    ]


def upgrade() -> None:
    # An investment may name the booking it came from.
    for statement in [*_sources(f"{_SOURCES}, 'investment_booking'"), TABLE, *INDEXES]:
        op.execute(statement)
    for statement in [GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM investment_bookings) THEN
        RAISE EXCEPTION 'refusing to drop the record of investments staff booked for clients';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Who booked an investment for a client, and who agreed, is part of its story.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE investment_bookings")
    op.execute("DROP FUNCTION public.finvest_investment_bookings_guard()")
    for statement in _sources(_SOURCES):
        op.execute(statement)
